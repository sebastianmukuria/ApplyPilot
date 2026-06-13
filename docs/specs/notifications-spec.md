# Spec: notification fan-out (ntfy / webhooks / macOS banner / more events)

Work inside this repository; **do not commit**. No network access (httpx
installed; never actually call external services in tests — monkeypatch).

Read first: `src/applypilot/apply/launcher.py` `_notify_human` (current
Telegram + afplay implementation and its 40s throttle), `src/applypilot/
panel.py` (read_env/write_env), `src/applypilot/server.py` (settings
endpoints), `tests/`. Suite must stay green: `.venv/bin/python -m pytest
tests/ -q` (87 pass today).

## Part 1 — `src/applypilot/notify.py`

A small fan-out module, no Streamlit/FastAPI imports:

- `notify(event: str, reason: str, *, worker_id: int = 0) -> None` — fires
  every CONFIGURED channel, each wrapped in its own try/except (one channel
  failing never blocks another), each call best-effort with timeout=10.
- Events: `needs_human`, `run_failed`, `run_finished`, `batch_done`.
- Throttle: per (worker_id, event) at most one send per 40s for
  `needs_human` (same behavior as today); other events are not throttled.
- Channels, each enabled purely by env presence:
  - **telegram** — move the existing httpx sendMessage call here unchanged
    (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).
  - **ntfy** — `APPLYPILOT_NTFY_TOPIC` (and optional APPLYPILOT_NTFY_SERVER,
    default https://ntfy.sh): POST `{server}/{topic}` with the message as
    body and headers Title: "ApplyPilot", Priority: "high" for needs_human /
    "default" otherwise, Tags: "airplane".
  - **generic webhook** — `APPLYPILOT_WEBHOOK_URL`: POST JSON
    `{event, reason, ts}` (ISO UTC).
  - **discord** — `DISCORD_WEBHOOK_URL`: POST `{"content": text}`.
  - **slack** — `SLACK_WEBHOOK_URL`: POST `{"text": text}`.
  - **macos banner** — Darwin only, no env needed, on by default; disable
    with `APPLYPILOT_MACOS_BANNER=0`. `osascript -e 'display notification
    {reason!quoted} with title "ApplyPilot"'` — build argv WITHOUT shell,
    pass the script via `-e` with proper escaping of double quotes in
    reason (test the escaping with a reason containing quotes/backslashes).
    Keep the existing afplay Glass sound alongside (move it here).
- Message text: `f"ApplyPilot: {reason}"` for webhooks/ntfy; keep the
  current bell-emoji prefix for telegram only (existing behavior).

## Part 2 — launcher integration

- `_notify_human` becomes a thin wrapper calling
  `notify("needs_human", reason, worker_id=...)` (keep the dashboard
  add_event line where it is).
- New event call sites in the worker loop:
  - run failed (the else-branch that marks failed): `notify("run_failed",
    f"{title} at {company} failed: {reason}")` — once per job, never
    throttled away.
  - hand-off: `notify("run_finished", f"{company} is filled and waiting for
    your review")` — NOTE: hand-off currently also fires NEEDHUMAN from the
    agent; do NOT double-ping: only fire run_finished here when no
    needs_human ping happened for this worker in the last 60s (track last
    needs_human send time in notify module and expose a helper).
  - applied (unsupervised success): `notify("run_finished",
    f"Applied to {company}")`.
  - batch end (the run summary after the worker pool drains, only when
    total jobs > 1): `notify("batch_done", f"Batch done: {applied} applied,
    {failed} failed")`.

## Part 3 — settings surface (server.py)

- `GET /api/settings` gains booleans only: `ntfy_configured`,
  `webhook_configured`, `discord_configured`, `slack_configured`,
  `macos_banner` (true unless env disables; only meaningful on Darwin —
  include `platform_darwin: bool`). NEVER return topic/url values.
- `PUT /api/settings` accepts optional write-only string fields
  `ntfy_topic`, `webhook_url`, `discord_webhook_url`, `slack_webhook_url`
  (empty string = remove the key from .env) and bool `macos_banner`.
  Values are written via write_env; response is the refreshed GET payload.
- Validate URLs: must start with https:// (reject otherwise, 422); ntfy
  topic must match `[A-Za-z0-9_-]{1,64}`.

## Part 4 — tests

- notify(): each channel fires when configured (monkeypatch httpx +
  subprocess), one channel raising doesn't stop the others, needs_human
  throttle works and run_failed bypasses it, osascript argv escaping with
  hostile reason strings, macOS banner skipped off-Darwin.
- settings: GET exposes only booleans (assert no topic/url string leaks
  anywhere in the JSON), PUT round-trips into .env (read the tmp .env),
  empty string removes, invalid URL → 422, invalid topic → 422.
- launcher: monkeypatch notify and assert run_failed / run_finished /
  batch_done fire from the right branches (drive the worker-loop helpers
  directly or factor the call sites to be testable — follow the existing
  test style in tests/test_dryrun.py / test_stale_locks.py).

## Constraints

Don't touch web/, docs/, scoring, prompt.py. Never log or return secret
values. Style matches neighbors. Full suite green; short summary; no commit.
