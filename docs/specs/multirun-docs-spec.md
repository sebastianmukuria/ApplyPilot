# Spec: concurrent UI runs + in-app document library

Work entirely inside this repository. **Do not commit**; leave changes in the
working tree. No network access — fastapi, uvicorn, httpx, pypdf, and
python-multipart are already installed in `.venv`.

Read first: `src/applypilot/server.py` (current single-run endpoints),
`src/applypilot/panel.py` (launch_apply, stop_run, ACTIVE_FILE, tail),
`src/applypilot/cli.py` (apply command), `src/applypilot/apply/launcher.py`
(main(), the single-url path at ~line 1258 hardcodes `worker_id=0`),
`src/applypilot/apply/chrome.py` (per-worker ports/profiles, detached-port
hand-off protection — NEVER weaken it), `tests/test_server.py`.
Suite: `.venv/bin/python -m pytest tests/ -q` (77 pass today) must stay green.

## Part 1 — worker-slot plumbing (CLI → launcher)

`applypilot apply --url X` currently always runs as worker 0 (port 9222,
profile worker-0), which is why the GUI is limited to one run. Add a
`--worker-slot N` int option (default 0, hidden from main help is fine) to
the `apply` command, threaded through `launcher.main(...)` into the
single-url path's `run_worker(worker_id=N, port=BASE_CDP_PORT + N, ...)`.
Multi-worker batch mode (`-w`) is untouched.

## Part 2 — run registry in panel.py

Replace the GUI's single ACTIVE_FILE model with a registry while keeping the
legacy file working for the old Streamlit panel:

- `RUNS_FILE = <app dir>/gui_runs.json` — `{run_id: {url, company, model,
  pid, worker_slot, log, started}}`. run_id = `started` timestamp + slot
  (e.g. "20260613_101502_w1").
- `load_runs() -> dict` prunes entries whose pid is dead AND whose log says
  the run finished long ago — pruning rule: drop if pid dead, BUT keep
  dead-pid entries for 10 minutes after `started` so a just-finished run's
  final state (done/handoff) remains visible; expose `prune=False` escape.
  Keep it simple and deterministic for tests: accept a `now` parameter.
- `alloc_slot(max_slots) -> int | None` — lowest slot in [0, max_slots) not
  owned by a live-pid run. Max slots from env `APPLYPILOT_MAX_RUNS`
  (default 3).
- `launch_apply_slot(url, company, model, slot) -> run_id` — same subprocess
  pattern as launch_apply but appends `--worker-slot {slot}`, registers in
  RUNS_FILE, AND (back-compat) when slot == 0 also writes ACTIVE_FILE the
  way launch_apply does so the legacy Streamlit panel still sees slot-0 runs.
- `stop_run_id(run_id) -> bool` — targeted stop: SIGTERM→SIGKILL that run's
  process group, pkill the matching `--url <url>` claude/apply processes is
  NOT possible per-run via pkill safely — instead kill the registered pid's
  process group only, then `_kill_on_port(BASE_CDP_PORT + worker_slot)` via
  chrome helpers UNLESS that port is detached (use chrome._read_detached /
  _detached_ports guard — import the public-ish helpers or add a tiny
  `chrome.is_port_detached(port)`). Remove the registry entry (and
  ACTIVE_FILE if it points at the same url). The existing nuke-everything
  `stop_run()` stays for the stop-all path.

## Part 3 — server.py endpoints

Rework the run section (update tests accordingly; the old single-run
endpoints are replaced — the frontend ships simultaneously):

- `GET /api/runs` → `{slots: max_slots, free: n, runs: [run_payload]}`.
  run_payload = {run_id, url, company, model, started, worker_slot, alive,
  needs_you, done, status} — needs_you/done derived from that run's own log
  tail exactly like today; status from the job row.
- `POST /api/run` {url, model, supervised?} → 409 "no free slot" when
  alloc_slot returns None; 409 "job already running" if any live run has the
  same url; else launch_apply_slot. Returns the run_payload.
- `POST /api/run/stop` {run_id} → stop_run_id; 404 unknown run_id.
- `POST /api/run/stop-all` → panel.stop_run() (the nuke) + clear registry.
- `POST /api/run/clear` {run_id} → remove a dead run from the registry
  (409 if pid still alive).
- `GET /api/run/log?run_id=` → tail 200 (redacted).
- `GET /api/run/log/stream?run_id=` → per-run SSE, same event protocol as
  today (`log` lines + `status` frames every 3s with that run's payload);
  ends when the run leaves the registry or its log stops AND pid is dead.
- `GET /api/run` (legacy alias) → first alive run's payload or
  {active: false} — keep so nothing external breaks.

## Part 4 — documents: inline viewing + résumé library

- All three file endpoints (`/api/files/resume`, `/api/files/cover`, new
  `/api/files/master`) accept `inline=1` → `Content-Disposition: inline`
  (default stays attachment). `/api/files/master` serves
  `<app>/master_resume.pdf`, 404 if missing.
- Library dir: `<app>/resumes/` (create on demand).
  - `GET /api/resumes` → `{master_exists: bool, items: [{id, name, kind,
    size, mtime, is_master}]}` where items = every *.pdf in resumes/ plus,
    when present, the well-known files `resume.pdf` ("base") and
    `master_resume.pdf` ("master"). `kind` in {library, base, master}.
    `is_master` = byte-identical to master_resume.pdf (compare size then
    hash; cache nothing).
  - **id safety**: ids are opaque tokens of the form `lib:<filename>`,
    `base:resume.pdf`, `master:master_resume.pdf`. Resolve STRICTLY: reject
    any filename containing `/`, `\\`, or `..`; resolve inside the allowed
    dir and verify `.resolve()` stays under it. Add a test that
    `lib:../.env` and `lib:..%2F.env` style ids return 400/404, never bytes.
  - `GET /api/resumes/file?id=&inline=1` → serve that PDF.
  - `POST /api/resumes/upload` — multipart file field `file`; reject unless
    filename ends .pdf, content starts with `%PDF-`, and size <= 15 MB;
    sanitized filename (basename, collapse weird chars, dedupe with -1, -2);
    saves into resumes/; returns the new item.
  - `POST /api/resumes/select` {id} → copy that PDF to master_resume.pdf
    and regenerate master_resume.txt by extracting text with pypdf
    (best-effort: on extraction failure keep the existing txt if present,
    else write resume.txt's content if it exists, else empty — never crash).
    Returns the refreshed /api/resumes payload.

## Part 5 — tests (extend tests/test_server.py or a new file)

Cover at minimum: slot allocation (0,1,2 then 409; freed slot reused);
"job already running" 409; per-run stop kills only its registry entry
(monkeypatch the kill helpers — no real processes; launch_apply_slot's
subprocess must be monkeypatched in tests, same approach the existing tests
use for single-run); clear refuses a live pid; legacy /api/run alias;
inline vs attachment disposition; library listing with kinds; id
path-traversal rejection; upload validation (bad extension, bad magic, too
big) and dedupe; select copies bytes + writes extracted text (use pypdf to
WRITE a tiny one-page pdf in the test fixture, or commit a ~1KB fixture pdf
under tests/fixtures/); pruning honors the 10-minute grace via the `now`
parameter.

## Constraints

- Never weaken hand-off protection (detached ports are untouchable by any
  stop path).
- Never return or log secret values; uploads land 0644 in resumes/ but the
  registry and env writes keep existing permission patterns.
- Don't touch web/, docs/, scoring, or the Streamlit dashboard beyond what
  Part 2's back-compat requires (it should require nothing).
- Style matches neighboring code. Full suite green at the end; short summary
  of changed files; no commits.
