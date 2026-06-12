# Spec: make the apply agent significantly faster

Work entirely inside this repository. **Do not commit** — leave changes in the
working tree. You have NO network access; never pip/npm install.

## Context

ApplyPilot's auto-apply (`src/applypilot/apply/launcher.py`) spawns, per job:
a Chrome with CDP (`apply/chrome.py`), then a `claude` CLI subprocess that
drives it through a Playwright MCP server (`npx @playwright/mcp@latest
--cdp-endpoint=...`, see `_make_mcp_config`). The agent's stdout is
`--output-format stream-json` parsed line-by-line in `run_job`.

Real-world timing today: ~370–390s per application (measured via the
HANDOFF events in `~/.applypilot/logs/gui_apply_*.log`). The user needs
100+ applications/day, so per-job time and between-job overhead both matter.

Read first: `apply/launcher.py` (run_job, run_worker loop, _make_mcp_config,
_build_claude_cmd), `apply/chrome.py` (launch_chrome — note the detached-port
hand-off protection added recently; do not weaken it), `apply/prompt.py`
(build_prompt — note SUPERVISED/handoff and DROPDOWN DISCIPLINE sections;
their semantics are untouchable). Tests: `.venv/bin/python -m pytest tests/ -q`
must stay green (68 pass today).

## Task 1 — per-run timing instrumentation

In `run_job`'s stream-json loop, record wall-clock timing per event:
- claude process spawn → first event (startup latency)
- each `tool_use` / tool_result pair: tool name, duration
- model turns: count, total duration, tokens if present in the events
- total run duration and the RESULT line

Write a summary JSON per run to `config.LOG_DIR / f"timing_{ts}_w{worker}.json"`:
`{job_url, total_s, startup_s, model_turns, tool_calls: {name: {count,
total_s}}, top_slowest: [...], result}`. Never let instrumentation break a
run (wrap in try/except, log at debug).

Add `applypilot apply --timing-report` (utility mode, no Chrome/Claude): reads
all timing_*.json files and prints an aggregate Rich table — avg total, avg
startup, avg model-turn time, tool-call breakdown — plus the 3 slowest runs.

Tests: unit-test the stream-json timing parser against a small inline fixture
(list of JSON lines with fake timestamps — refactor the parsing so the
timing logic is a pure function that can be fed events) and the report
aggregation over two fixture timing files in tmp_path.

## Task 2 — kill the npx @latest cold start

`_make_mcp_config` uses `npx @playwright/mcp@latest` — that re-checks the npm
registry on EVERY job. Change to a pinned version constant
`PLAYWRIGHT_MCP_VERSION = "0.0.76"` (module-level, documented) and use
`npx -y @playwright/mcp@{PLAYWRIGHT_MCP_VERSION}`. Allow override via env
`APPLYPILOT_MCP_VERSION`. Update any tests that assert on the config.

## Task 3 — reuse Chrome across jobs in a batch run

In the worker loop, Chrome is currently killed and relaunched per job
(launch_chrome → cleanup_worker every iteration). Change to: launch once per
worker, reuse across jobs; between jobs navigate the browser back to
`about:blank` via CDP HTTP (`PUT /json/new?about:blank` is unreliable —
instead use the existing pattern: hit `http://localhost:{port}/json/list`,
then `http://localhost:{port}/json/close/{targetId}` for every page target,
which leaves Chrome with zero tabs and the next job's browser_navigate opens
fresh). Implement as `chrome.reset_tabs(port)` with a 5s httpx timeout and
graceful fallback: if reset fails OR the previous job ended in any
failure/timeout state, fall back to the existing kill+relaunch path.
Hand-off (`result == "handoff"`) still detaches and ends the run — untouched.
cleanup at the END of the worker loop still runs (the `finally` and
kill_all_chrome paths must keep working).

Also remove the unconditional `time.sleep(3)` after Chrome launch: poll
`http://localhost:{port}/json/version` every 0.25s up to 6s instead, so a
ready Chrome proceeds in <1s.

Tests: reset_tabs against a fake CDP server (spin a tiny http.server in a
thread inside the test, or monkeypatch httpx) covering success and
fallback-on-error; the launch poll with a monkeypatched ready endpoint.

## Task 4 — SPEED PROTOCOL in the prompt

Add a compact section to `build_prompt` in `apply/prompt.py` (near the
screening/dropdown sections):

```
== SPEED PROTOCOL ==
- Fill in BATCHES: complete every field visible in the current view, then
  verify with ONE snapshot. Never snapshot after every single field.
- Use browser_fill_form (or one JS evaluate) for groups of plain text fields
  instead of one tool call per field.
- Do not scroll pixel-by-pixel: jump section to section.
- Open-ended answers: 2-3 sentences, write once, no redrafting loops.
- Do not re-read the job description after the location check.
- Target: a standard application in under 3 minutes of work.
```

Do NOT alter the supervised/handoff instructions, the EEO/dropdown sections,
or any safety rule. Keep the existing tests green (tests/test_dryrun.py greps
the prompt).

## Constraints

- Don't touch: `src/applypilot/server.py`, `panel.py`, `dashboard.py`,
  `web/`, scoring pipeline, docs.
- Don't weaken hand-off protection in chrome.py (detached ports must never be
  killed or tab-reset — guard reset_tabs against detached ports too).
- Full suite green at the end; leave a short changed-files summary; no commits.
