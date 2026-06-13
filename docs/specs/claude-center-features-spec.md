# Spec D: Claude-subscription centering + pipeline-from-app + autopilot + digest + onboarding API

Work inside this repo; **do not commit**. No network; tests monkeypatch all
transport/subprocesses. Suite green before stopping:
`.venv/bin/python -m pytest tests/ -q`.

Read first: `src/applypilot/llm.py` (the new provider layer: ProviderSpec,
resolve_provider_name, ClaudeCLIClient, stage envs), `src/applypilot/
scoring/scorer.py` (per-job scoring loop), `src/applypilot/pipeline.py`
(run_pipeline stages), `src/applypilot/config.py` (check_tier tiers),
`src/applypilot/panel.py` (runs registry: load_runs/alloc_slot/
launch_apply_slot), `src/applypilot/notify.py`, `src/applypilot/server.py`,
`tests/`.

## Part 1 — Claude subscription becomes the center of gravity

1. **Auto-detect order** in llm.py: explicit stage/pipeline env overrides
   stay supreme; otherwise auto order becomes:
   `claude-cli` (when `shutil.which("claude")`) → `local` (LLM_URL) →
   `gemini` → `openai`. Add `APPLYPILOT_PIPELINE_PROVIDER` value `auto`
   treated as unset. Document in the module docstring: a Claude Code
   subscription alone now runs the ENTIRE pipeline; API keys are optional
   accelerators.
2. **Batched scoring** (required to make claude-cli viable at volume, and it
   helps Gemini RPM too): refactor scorer.py so the LLM scores up to
   `APPLYPILOT_SCORE_BATCH` (default 8) jobs per call. Contract: numbered
   job blocks (title/company/snippet of description capped ~1500 chars
   each) in, strict JSON array out `[{n, score, reasoning}]`; parse
   defensively — any job missing from the response falls back to an
   individual call (existing path). Keep the existing single-job path as
   the fallback primitive. Validation/DB writes unchanged per job.
3. **Tier gate** (config.check_tier): Tier 2 (AI stages) is satisfied by
   EITHER an API key OR the claude CLI on PATH. Update doctor output
   accordingly (the check lives in cli.py doctor / config — follow it).
4. ClaudeCLIClient hardening for volume: honor `max_tokens` loosely (cap
   prompt at sane size), and add simple serialization — a module-level lock
   so concurrent pipeline threads don't spawn unbounded claude processes
   (max 2 concurrent via a semaphore).

## Part 2 — Run the pipeline from the app

- `POST /api/pipeline/run` body `{stages: list[str] = ["all"], min_score:
  int = 7, workers: int = 2}` — validate stages against VALID_STAGES;
  409 if a pipeline run is already active. Runs `run_pipeline` in a daemon
  thread. Progress: write `APP_DIR/pipeline_state.json`
  `{started, stages, current_stage, done: {stage: {…counts}}, done_at,
  error}` — update it between stages (wrap the stage loop; if
  run_pipeline's structure makes that awkward, add an optional
  `on_stage(stage, result)` callback parameter to run_pipeline — small,
  backward-compatible). 
- `GET /api/pipeline/status` — state file + `running: bool` (thread alive).
- The discover stage can take minutes — that's fine, it's a background
  thread; never block the event loop.

## Part 3 — Autopilot (batch applies from the app)

- `POST /api/autopilot` body `{count: int (1..30), model: str = "sonnet"}`:
  409 if autopilot already running. Daemon thread loop:
  - state file `APP_DIR/autopilot_state.json` `{target, launched, finished,
    skipped, current: [run_ids], started, done_at, stopped}`.
  - while finished < target and not stopped: if a slot is free
    (panel.alloc_slot), pick the next job exactly like the batch
    acquire_job ordering (docs-ready, status NULL/failed, attempts under
    max, fit_score desc) EXCLUDING urls already in the runs registry or
    already launched this session; launch via panel.launch_apply_slot;
    else sleep 5s. A launched run counts as finished when it leaves the
    registry alive-set (done/handoff/dead) — poll the registry.
  - `POST /api/autopilot/stop` sets stopped (does NOT kill in-flight runs —
    they finish naturally; the loop just stops launching).
  - `GET /api/autopilot/status` — state + running flag.
- Notify integration: on autopilot completion fire
  `notify("batch_done", f"Autopilot done: {finished} of {target} launched...")`.

## Part 4 — Daily digest

- `notify`-powered summary. Env `APPLYPILOT_DIGEST_HOUR` ("" = off,
  "8" = 8am local). Server lifespan task (alongside the tracking poller):
  every 10 min, if digest hour configured, last_digest (stored in
  `APP_DIR/digest_state.json`) is not today, and now.hour >= digest hour →
  compose + `notify("digest", text)` + stamp. Text: new jobs discovered in
  the last 24h, count of strong (≥8) untouched, docs-ready count, applied
  yesterday, outcomes since last digest (from app_events if the table
  exists — guard with try). Keep it to ~4 lines.
- Settings: GET gains `digest_hour` (string), PUT accepts it (validate
  "" or 0-23, 422 otherwise).

## Part 5 — Onboarding/status API (drives the first-run wizard UI)

- `GET /api/onboarding` →
  `{resume_ready, profile_ready, searches_ready, claude_cli, api_key_present,
  telegram_configured, tracking_configured, jobs_discovered, docs_ready,
  applied}` — resume_ready = master_resume.pdf OR resume.txt exists;
  profile_ready = profile.json exists with personal.full_name and
  personal.email non-empty; searches_ready = searches.yaml exists and has at
  least one search title (parse with yaml.safe_load, tolerate any shape).
- `GET /api/profile/personal` / `PUT /api/profile/personal` — subset fields
  {full_name, email, phone, city, province_state, country, linkedin_url,
  github_url, website_url}; PUT merges into profile.json (create the file
  with sensible empty structure if missing — mirror profile.example.json's
  top-level keys with empty values so downstream code never KeyErrors;
  also ensure work_authorization defaults to
  {"legally_authorized_to_work": "Yes", "require_sponsorship": "No"}).
- `GET /api/searches` / `PUT /api/searches` — read/write searches.yaml:
  expose `{titles: [...], locations: [...], remote: bool}` mapped onto the
  existing searches.yaml schema (read config/searches.example.yaml to learn
  it; preserve any keys you don't model by round-tripping the loaded dict
  and only replacing the modeled parts).
- `/api/resumes/select` addition: when `resume.txt` does NOT exist, also
  write the extracted text there (the pipeline's base resume) — onboarding
  uploads one PDF and everything downstream works.

## Part 6 — tests

Batched scorer (batch parse, partial response falls back per-job, batch env
size); provider auto-order with/without `claude` on PATH (monkeypatch
shutil.which); tier gate via claude-only env; pipeline run endpoint (409
while running, state file progression — monkeypatch run_pipeline to a stub
writing fake stage results); autopilot loop logic factored pure enough to
test: slot-wait, exclusion of already-launched urls, stop flag, completion
counting (monkeypatch panel.launch_apply_slot/load_runs); digest compose
text from seeded DB + the hour/once-a-day gate (injectable now); onboarding
payload truth table; profile PUT merge creates a complete skeleton;
searches round-trip preserves unmodeled keys; resumes/select writes
resume.txt only when missing.

## Constraints

Don't touch web/, docs/, apply/ (registry helpers in panel.py are fine to
USE, not modify), tracking/ (may not exist yet — do not create it).
Never log secrets or prompts. Style matches neighbors; suite green; short
summary; no commits.
