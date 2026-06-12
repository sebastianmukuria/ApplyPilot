# Spec: ApplyPilot v2 backend — FastAPI server (`applypilot app`)

You are implementing the backend API for ApplyPilot's new web UI. Work entirely
inside this repository. **Do not commit** — leave all changes in the working
tree for review. Do not push, do not create branches.

## Context you need

ApplyPilot is a Python 3.11 job-application pipeline (Typer CLI, SQLite). Read
these before writing code:

- `src/applypilot/config.py` — APP_DIR (`~/.applypilot`, overridable via
  `APPLYPILOT_DIR`), DB_PATH, load_env, ensure_dirs, write_private_text.
- `src/applypilot/database.py` — init_db, get_connection; the `jobs` table.
- `src/applypilot/dashboard.py` — the CURRENT Streamlit GUI. It contains the
  exact business logic you must reuse: queue filtering, salary parsing,
  job flags, spend aggregation, run launch/stop, log redaction, answer
  generation. **The Streamlit app must keep working unchanged after your work.**
- `src/applypilot/cli.py` — Typer app; you'll add one command.
- `tests/` — pytest suite (58 tests). It must stay green: run
  `.venv/bin/python -m pytest tests/ -q` before finishing. fastapi, uvicorn,
  and httpx are ALREADY INSTALLED in `.venv` — you have no network access, do
  not try to pip install anything.

## Step 1 — extract shared logic into `src/applypilot/panel.py`

Create `panel.py` with NO streamlit imports — pure functions moved from
`dashboard.py` (move, don't copy, then re-import them in dashboard.py so both
GUIs share one implementation):

- `salary_num(s) -> int`
- `job_flags(row_dict) -> list[str]` (and the STAFFING/MARKETPLACE lists)
- `llm_spend() -> dict` (and USAGE_FILE/PRICES/DEFAULT_PRICE)
- `counts() -> dict`
- `mark_applied(url)`, `reset_job(url)`, `launch_apply(url, company, model)`,
  `stop_run() -> str`
- `tail(path, n=40) -> str` (keeps the bot-token and chat_id redaction)
- `read_text_sibling(path, suffix)`
- `gen_answer(profile, company, question, length, prev) -> str`
- `read_env() / write_env(updates)`, `load_profile() / save_profile(p)`,
  `load_hidden() / save_hidden(s)`
- The path constants (APP, DB, LOGDIR, ENV, PROFILE, HIDDEN_FILE, ACTIVE_FILE,
  PREFS_FILE) — define them in panel.py from `os.environ.get("APPLYPILOT_DIR", ...)`
  exactly as dashboard.py does today; dashboard.py imports them from panel.
- `PILLS` status mapping (status -> (css_modifier, label)).

Keep `db()` (sqlite connection helper) in panel.py too. After extraction,
`python -m py_compile src/applypilot/dashboard.py src/applypilot/panel.py`
must pass and the streamlit app must still start (you can verify imports with
`python -c "import applypilot.panel"`; do NOT try to run streamlit).

IMPORTANT (path freshness): panel.py must compute APP/DB/etc. at import time
the same way dashboard.py does, but tests monkeypatch `APPLYPILOT_DIR` — so
expose a small `paths()` accessor or module-level recompute helper the server
uses per-request for anything written to disk, OR simply read
`os.environ["APPLYPILOT_DIR"]` lazily inside functions that touch files. Tests
will set APPLYPILOT_DIR to a tmp dir before importing the server app factory;
make that work (lazy path resolution inside functions is the simplest route).

## Step 2 — `src/applypilot/server.py`

FastAPI app factory: `def create_app() -> FastAPI`. All routes under `/api`.
JSON only. CORS: allow `http://localhost:5173` (vite dev) only.

### Jobs

- `GET /api/jobs` — query params (all optional): `min_score:int=8`,
  `sources` (comma-separated), `search:str`, `sort:str` in
  `score|salary|company|location|recent` (default score),
  `show_applied:bool=false`, `only_docs_ready:bool=false`,
  `include_no_salary:bool=true`, `min_salary_k:int=0`,
  `hide_flagged:bool=true`, `hidden:bool=false` (include user-hidden),
  `limit:int=200`, `offset:int=0`.
  Mirror dashboard.py's queue logic EXACTLY: SQL prefilter
  (fit_score >= min_score, site IN sources, status filter
  `apply_status IS NULL OR apply_status IN ('failed','in_progress')` unless
  show_applied, docs-ready = tailored AND cover paths NOT NULL), then python
  post-filters (hidden set, search in company+title, salary floor semantics:
  no-salary rows dropped only when include_no_salary=false; min_salary_k
  applies only to rows that list one; hide_flagged drops rows with any flag).
  Response: `{total_matched, jobs:[{url, company, title, site, location,
  salary, salary_num, fit_score, apply_status, flags, docs_ready, has_resume,
  has_cover, score_reasoning, discovered_at, application_url, hidden}]}`.
- `GET /api/jobs/detail?url=` — one job + resume/cover text previews
  (read_text_sibling, cap 4000 chars each).
- `GET /api/jobs/handoffs` — all apply_status='handoff' rows (same job shape).
- `POST /api/jobs/mark-applied` body `{url}` — mark_applied.
- `POST /api/jobs/reset` body `{url}` — reset_job.
- `POST /api/jobs/hide` / `POST /api/jobs/unhide` body `{url}` — persist via
  load_hidden/save_hidden.
- `GET /api/files/resume?url=` and `GET /api/files/cover?url=` — stream the
  job's PDF (FileResponse, content-disposition attachment, clean filename);
  404 if missing. Resolve via the job row's *_path with .pdf suffix.

### Stats

- `GET /api/stats` — `{funnel: counts(), per_day:[{date, applications}],
  status_breakdown:[{status, count}] (score>=7, humanized labels),
  score_distribution:[{score, count}] (linkedin/indeed only),
  spend: llm_spend()}`. Per-day counts applied + handoff rows exactly as
  dashboard.py does (applied_at, or last_attempted_at for handoff).

### Run lifecycle

- `GET /api/run` — `{active:bool, company, url, model, started, pid,
  needs_you:bool, done:bool, status}` from ACTIVE_FILE + log heuristics
  (same strings dashboard uses: "Done:" in tail = done; "pinged you" or
  "NEEDHUMAN" and not done = needs_you) + the job's apply_status.
- `POST /api/run` body `{url, model="sonnet", supervised:bool|null}` —
  409 if ACTIVE_FILE exists and its pid is alive; otherwise write_env
  supervised if provided, launch_apply, return the new run info.
- `POST /api/run/stop` — stop_run(); returns `{stopped: <str>}`.
- `POST /api/run/clear` — remove ACTIVE_FILE only.
- `GET /api/run/log` — `{lines: tail(active.log, 200)}` (redacted), 404 if no
  active file.
- `GET /api/run/log/stream` — Server-Sent Events: poll the active log file
  every 1s, emit only NEW lines (redacted with the same regexes as tail()),
  event name `log`; emit event `status` with the /api/run payload every 3s;
  end the stream when ACTIVE_FILE disappears. Use StreamingResponse with
  media_type="text/event-stream" (no extra dependency).

### Settings & profile

- `GET /api/settings` — `{supervised:bool, fixed_resume:bool,
  salary_mode:str, salary_fixed:str, llm_model:str, telegram_connected:bool,
  master_resume_exists:bool}`. NEVER return key/token VALUES — booleans only
  for anything secret.
- `PUT /api/settings` body with any of `{supervised, fixed_resume,
  salary_mode, salary_fixed}` — write_env the corresponding
  APPLYPILOT_* vars (same names dashboard.py uses).
- `GET /api/work-context` — profile.json work_context
  `{projects, llm_usage, answer_rules}`.
- `PUT /api/work-context` — replace those three keys (drop projects with
  empty name), save_profile.
- `POST /api/answers` body `{company, question, length="2-3 sentences",
  prev=""}` — run gen_answer in a thread
  (`fastapi.concurrency.run_in_threadpool`), return `{answer}`; 400 if
  question empty; wrap exceptions into `{"detail": str(e)}` 502.

### Static frontend

- If `src/applypilot/webdist/` exists, mount it: serve `index.html` at `/`,
  assets under `/assets`, and SPA-fallback any unknown non-/api GET to
  index.html. If absent, `GET /` returns a small JSON
  `{"status":"api-only","hint":"web UI not built"}`.

## Step 3 — CLI command

In `cli.py`, add:

```
@app.command(name="app")
def app_command(port: int = typer.Option(8765, "--port", "-p"),
                host: str = typer.Option("127.0.0.1", "--host")):
    """Launch the ApplyPilot v2 web app (API + UI)."""
```

`_bootstrap()` first; friendly error if fastapi/uvicorn aren't importable
(`pip install 'applypilot[app]'`); then
`uvicorn.run(create_app(), host=host, port=port, log_level="warning")` and
print the URL.

Also add to `pyproject.toml` optional-dependencies:
`app = ["fastapi>=0.115", "uvicorn[standard]>=0.30"]`.

## Step 4 — tests (`tests/test_server.py`)

Use `fastapi.testclient.TestClient` (httpx installed). Fixture: tmp
APPLYPILOT_DIR via monkeypatch.setenv BEFORE importing/creating the app,
init_db there, seed jobs covering: scores 5..10, sites linkedin/indeed/other,
salary strings ("$120,000 - $150,000/yr", None), statuses (NULL, failed,
handoff, applied, in_progress), tailored/cover paths set/unset, a staffing
company ("Acme Staffing"). Assert at minimum:

1. /api/jobs default excludes applied+handoff, includes failed and
   in_progress, respects min_score.
2. only_docs_ready filters to rows with both paths; include_no_salary=false
   drops no-salary rows; min_salary_k drops only listed-below-floor rows;
   hide_flagged drops the staffing row; search matches company OR title.
3. hide → row disappears from /api/jobs; unhide restores; hidden=true shows it.
4. mark-applied sets apply_status + applied_at (verify via sqlite).
5. /api/stats funnel matches seeded counts; spend handles a seeded
   llm_usage.jsonl (one record → correct cost math per panel.PRICES).
6. /api/run with no active file → active:false; POST /api/run/stop is safe
   with nothing running.
7. /api/settings GET returns booleans (no token strings anywhere in the JSON);
   PUT round-trips supervised/salary_mode into the .env file.
8. /api/answers with empty question → 400.
9. /api/jobs/handoffs returns exactly the handoff-status rows.
10. SSE route: with no ACTIVE_FILE returns/closes promptly (don't hang the
    test suite; guard with a short timeout).

Whole suite green: `.venv/bin/python -m pytest tests/ -q`.

## Hard constraints

- Never log or return env values / API keys / tokens. The .env file is
  written with `write_env` (which chmods 0600) — reuse it.
- `launch_apply` subprocess pattern must stay EXACTLY as dashboard.py has it
  (`[sys.executable, "-m", "applypilot", "apply", ...]`,
  start_new_session=True, log file opened 0600).
- Don't touch `src/applypilot/apply/`, the scoring pipeline, or existing
  tests. Don't reformat files you didn't need to change.
- Style: match the existing code (logging via logging module, docstrings,
  type hints as in neighboring files).
- When done: run the full test suite one last time, then STOP. Leave a short
  summary of files changed. Do not commit.
