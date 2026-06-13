# Spec C: Gmail application tracking — backfill + live pipeline radar

Work inside this repo; **do not commit**. No network — google libs
(google-api-python-client, google-auth, google-auth-oauthlib) are installed;
tests must monkeypatch ALL transport (Gmail service + LLM).

**Reference implementation** (the user's own project, reuse the DESIGN and
adapt code freely — it's his): `docs/specs/reference/daily-os-bot/` —
`auth_google.py` (OAuth installed-app flow), `pipeline_classifier.py`
(two-pass: deterministic prefilter → LLM with structured output; the
prefilter domain/phrase knowledge is battle-tested — port it),
`pipeline_state.py` (match + confidence-gated transitions),
`pipeline_ingest.py` (pure plan_email), `pipeline_backfill.py` (90-day
reconstruct with per-message classification cache), `test_classifier.py`.

Also read: `src/applypilot/database.py` (the new `app_events` table and
jobs.outcome columns from the docx-outcomes change), `src/applypilot/llm.py`
(get_client(stage=...) — use `stage="track"`), `src/applypilot/server.py`,
`src/applypilot/cli.py`, `src/applypilot/config.py` (APP_DIR), `panel.py`.

## 1. Package `src/applypilot/tracking/`

- `auth.py` — adapt auth_google.py: scope ONLY
  `https://www.googleapis.com/auth/gmail.readonly`; credentials at
  `APP_DIR/google_credentials.json`, token at `APP_DIR/google_token.json`
  (chmod 0600 on write, reuse config.write_private_text if suitable).
  `get_service()` returns an authorized gmail service or raises
  `TrackingNotConfigured` with a message telling the user to run
  `applypilot track auth`. Token refresh handled (google.auth refresh on
  expiry, persist refreshed token).
- `classifier.py` — port the prefilter knowledge (domains, phrases,
  negative signals) from the reference, generalized: also treat any sender
  domain matching a company name in OUR jobs table as job-related. LLM pass
  BATCHED: classify up to 15 emails per call via get_client(stage="track")
  with a strict JSON-array contract ({message_id, company, role,
  event_type, confidence}); parse defensively, anything unparseable →
  event_type "other"/confidence 0. Event types exactly as the reference:
  applied, screen_invite, interview_scheduled, reschedule, rejection,
  offer, recruiter_inbound, other.
- `sync.py` —
  - `match_job(company, conn)` — match app_events to jobs rows: candidates
    are jobs with apply_status IN ('applied','handoff'); company match =
    case-insensitive containment either direction after stripping
    punctuation/Inc/LLC; ambiguous (2+) → no auto-match (event saved
    unmatched).
  - event_type → outcome mapping: rejection→rejected, offer→offer,
    interview_scheduled/reschedule→interview, screen_invite→screen,
    recruiter_inbound/other→no outcome change (event logged only),
    applied→no change (we already know).
  - Outcome upgrades only move FORWARD (responded<screen<interview<offer;
    rejected always wins over everything except offer) and only with
    confidence >= 0.7; never downgrade; outcome_source='gmail'.
  - `sync(window="2d")` — Gmail query `newer_than:<window>` minus
    message_ids already in app_events (the dedupe/cache), prefilter →
    classify → insert app_events → apply outcome transitions. Returns
    {scanned, classified, events, outcomes_set}.
  - `backfill(days=90)` — same path with `newer_than:90d`, chunked
    (pagination), progress written to `APP_DIR/tracking_state.json`
    ({phase, scanned, total, events, done_at}) so the UI can poll it.
    Also stamps last_sync in the same state file; sync() updates it too.
- All Gmail reads: metadata + snippet only (format='metadata', headers
  From/Subject/Date + snippet) — never fetch full bodies.

## 2. CLI + server

- `applypilot track auth|backfill|sync|status` command group (auth runs the
  browser flow; backfill takes --days; status prints state + counts).
- Server endpoints:
  - `GET /api/tracking/status` — {configured, last_sync, backfill: <state
    file content or null>, events_total, matched_total}.
  - `POST /api/tracking/sync` — run in threadpool, return sync summary;
    409 if a backfill is currently running.
  - `POST /api/tracking/backfill` {days=90} — start in a daemon thread,
    return 202 immediately; 409 if already running. Progress via status.
  - Background poller: asyncio task on app lifespan — every 20 minutes, if
    configured, run sync in a thread; failures logged, never crash the app.
- pyproject: new extra `tracking = ["google-api-python-client>=2",
  "google-auth>=2", "google-auth-oauthlib>=1"]`; also append these to the
  `app` extra. Import google libs lazily so the package works without them.

## 3. Tests (fixtures, zero network)

Port the reference's fixture style: a list of synthetic emails (ATS
confirmations, rejection, interview invite, recruiter spam, apartment
"application" false positive). Test: prefilter precision on the fixtures;
batched classifier parsing (monkeypatched LLM returning the JSON array,
plus a malformed-response case); match_job (exact, fuzzy containment,
ambiguous→unmatched); forward-only outcome transitions incl. the
rejected/offer precedence and the confidence gate; sync dedupe by
message_id (second run inserts nothing); backfill state file progress;
endpoints (status unconfigured vs configured via monkeypatch, sync 409
during backfill). The lifespan poller: just assert it's registered/started
without running real sleeps (factor the interval loop to be injectable).

## Constraints

Read-only Gmail scope, metadata-only fetches, never store email bodies;
never log tokens. Don't touch web/, docs/, apply/, scoring/ beyond imports.
Style matches neighbors; full suite green; short summary; no commits.
