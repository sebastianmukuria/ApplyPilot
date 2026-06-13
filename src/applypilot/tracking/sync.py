"""Turn classified emails into job outcomes — backfill + incremental sync.

Pure decision logic (match_job, plan_transition) is separated from the Gmail
I/O so it's unit-testable without a network. Dedupe is by Gmail message_id
via the app_events UNIQUE constraint — re-running a sync inserts nothing.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot.database import get_connection
from applypilot.tracking import auth, classifier

logger = logging.getLogger(__name__)

STATE_FILE = "tracking_state.json"
CONFIDENCE_THRESHOLD = 0.7

# event_type -> jobs.outcome (None = log the event, change nothing)
OUTCOME_FOR_EVENT = {
    "rejection": "rejected",
    "offer": "offer",
    "interview_scheduled": "interview",
    "reschedule": "interview",
    "screen_invite": "screen",
    "recruiter_inbound": None,
    "applied": None,
    "other": None,
}
# forward-only ladder; rejected outranks everything except offer
_RANK = {"responded": 1, "screen": 2, "interview": 3, "rejected": 4, "offer": 5}

_backfill_thread: threading.Thread | None = None


def _app_dir() -> Path:
    return Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))


def _read_state() -> dict:
    import json
    try:
        return json.loads((_app_dir() / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    import json
    try:
        (_app_dir() / STATE_FILE).write_text(json.dumps(state))
    except OSError:
        logger.debug("Could not write tracking state", exc_info=True)


# ---------------------------------------------------------------------------
# Pure decision logic
# ---------------------------------------------------------------------------

_STRIP = re.compile(r"\b(inc|llc|ltd|corp|co)\b\.?|[^\w\s]")


def _norm(name: str) -> str:
    return _STRIP.sub("", (name or "").lower()).strip()


def match_job(company: str, conn) -> str | None:
    """URL of the applied/handed-off job this company refers to, or None.

    Containment either direction after normalization; ambiguous (2+) → None.
    """
    needle = _norm(company)
    if not needle:
        return None
    rows = conn.execute(
        "SELECT url, company FROM jobs WHERE apply_status IN ('applied','handoff')"
    ).fetchall()
    hits = []
    for row in rows:
        have = _norm(row["company"] or "")
        if have and (needle in have or have in needle):
            hits.append(row["url"])
    return hits[0] if len(hits) == 1 else None


def plan_transition(current: str | None, event_type: str, confidence: float) -> str | None:
    """The new outcome to set, or None to leave the job untouched."""
    target = OUTCOME_FOR_EVENT.get(event_type)
    if target is None or confidence < CONFIDENCE_THRESHOLD:
        return None
    if current and _RANK.get(target, 0) <= _RANK.get(current, 0):
        return None  # never downgrade or repeat
    return target


# ---------------------------------------------------------------------------
# Gmail I/O
# ---------------------------------------------------------------------------

# Server-side Gmail narrowing so we fetch job-related mail only — drops the
# whole-inbox scan to a few hundred messages, which both saves thousands of
# metadata GETs AND means the page cap can't truncate a real backfill. The
# deterministic prefilter still runs as a precision second pass.
_GMAIL_JOB_TERMS = (
    'applying OR application OR "thank you for applying" OR interview OR '
    'recruiter OR recruiting OR candidacy OR "next steps" OR "your interest" OR '
    'offer OR "moving forward" OR "phone screen" OR "talent acquisition"'
)


def _job_query(window: str) -> str:
    return (f"newer_than:{window} -in:sent -in:chats "
            f"-category:promotions -category:social ({_GMAIL_JOB_TERMS})")


def _fetch_candidates(service, query: str, max_pages: int = 60) -> list[dict]:
    """Metadata-only fetch (From/Subject/Date + snippet) — never full bodies.

    max_pages*100 is a safety ceiling; with the narrowed _job_query it is never
    reached in practice. If it ever is, the truncation is logged, not silent.
    """
    out: list[dict] = []
    page_token = None
    pages = 0
    for pages in range(1, max_pages + 1):
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token
        ).execute()
        for ref in resp.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            out.append({
                "message_id": msg["id"],
                "thread_id": msg.get("threadId"),
                "sender": headers.get("from", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "snippet": msg.get("snippet", ""),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    if page_token:
        logger.warning("Gmail fetch hit the %d-page ceiling; older mail not scanned "
                       "this pass (it will be picked up incrementally).", max_pages)
    return out


def _known_message_ids(conn) -> set[str]:
    """message_ids already processed — in app_events (actionable) OR in the
    seen-ledger (everything classified, incl. 'other'), so the LLM never
    re-classifies the same email on the next sync."""
    ids = {r[0] for r in
           conn.execute("SELECT message_id FROM app_events WHERE message_id IS NOT NULL")}
    try:
        ids |= {r[0] for r in conn.execute("SELECT message_id FROM tracking_seen")}
    except Exception:  # noqa: BLE001 — table created lazily on first write
        pass
    return ids


def _known_companies(conn) -> set[str]:
    return {(_norm(r["company"]) or "") for r in
            conn.execute("SELECT DISTINCT company FROM jobs WHERE company IS NOT NULL")} - {""}


def _email_ts(date_header: str) -> str:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_header).astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001 — junk Date headers exist in the wild
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_seen_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS tracking_seen ("
                 "message_id TEXT PRIMARY KEY, seen_at TEXT)")


def _apply_events(conn, emails: list[dict], classifications: list[dict],
                  on_progress=None) -> dict:
    import sqlite3
    inserted = 0
    outcomes_set = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _ensure_seen_table(conn)
    for email, cls in zip(emails, classifications):
        # Record EVERY classified message so a non-actionable 'other' is not
        # re-sent to the LLM on every future sync.
        conn.execute("INSERT OR IGNORE INTO tracking_seen VALUES (?,?)",
                     (email["message_id"], now))
        if cls["event_type"] == "other":
            if on_progress:
                on_progress()
            continue
        job_url = match_job(cls["company"], conn)
        try:
            conn.execute(
                "INSERT INTO app_events (message_id, thread_id, job_url, company, role,"
                " event_type, confidence, email_ts, subject, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (email["message_id"], email.get("thread_id"), job_url,
                 cls["company"], cls["role"], cls["event_type"], cls["confidence"],
                 _email_ts(email.get("date", "")), (email.get("subject") or "")[:300], now),
            )
            inserted += 1
        except sqlite3.IntegrityError:  # UNIQUE(message_id) — already recorded
            continue
        if job_url:
            row = conn.execute("SELECT outcome FROM jobs WHERE url=?", (job_url,)).fetchone()
            new = plan_transition(row["outcome"] if row else None,
                                  cls["event_type"], cls["confidence"])
            if new:
                conn.execute(
                    "UPDATE jobs SET outcome=?, outcome_at=?, outcome_source='gmail' WHERE url=?",
                    (new, now, job_url),
                )
                outcomes_set += 1
        if on_progress:
            on_progress()
    conn.commit()
    return {"events": inserted, "outcomes_set": outcomes_set}


def _process(service, conn, query: str, on_progress=None) -> dict:
    emails = _fetch_candidates(service, query)
    known_ids = _known_message_ids(conn)
    fresh = [e for e in emails if e["message_id"] not in known_ids]
    companies = _known_companies(conn)
    candidates = [e for e in fresh if classifier.prefilter(e, companies)]

    totals = {"scanned": len(emails), "fresh": len(fresh),
              "classified": len(candidates), "events": 0, "outcomes_set": 0}
    for i in range(0, len(candidates), classifier.BATCH):
        chunk = candidates[i:i + classifier.BATCH]
        result = _apply_events(conn, chunk, classifier.classify_batch(chunk), on_progress)
        totals["events"] += result["events"]
        totals["outcomes_set"] += result["outcomes_set"]
    return totals


def sync(window: str = "2d") -> dict:
    """Incremental sync of recent mail. Raises TrackingNotConfigured."""
    service = auth.get_service()
    conn = get_connection()
    totals = _process(service, conn, _job_query(window))
    state = _read_state()
    state["last_sync"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_state(state)
    return totals


def auto_sync(min_interval_s: int = 1200) -> dict | None:
    """Ticker entry: sync when configured and stale; quiet no-op otherwise."""
    if not auth.is_configured() or backfill_running():
        return None
    state = _read_state()
    last = state.get("last_sync")
    if last:
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(last)).total_seconds()
            if age < min_interval_s:
                return None
        except ValueError:
            pass
    try:
        return sync()
    except Exception:  # noqa: BLE001 — the ticker must survive anything
        logger.debug("auto_sync failed", exc_info=True)
        return None


def backfill_running() -> bool:
    return _backfill_thread is not None and _backfill_thread.is_alive()


_backfill_lock = threading.Lock()


def start_backfill(days: int = 90) -> bool:
    """Reconstruct history from Gmail in a daemon thread. False if running."""
    global _backfill_thread
    # scanned/total mean ONE thing throughout (classified-so-far / to-classify),
    # so the progress bar is a clean 0..100%. `fetched` carries the raw count.
    initial = {"phase": "scanning", "scanned": 0, "total": 0, "fetched": 0,
               "events": 0, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "done_at": None}

    def _run() -> None:
        state = {**{k: v for k, v in _read_state().items() if k != "backfill"},
                 "backfill": initial}
        _write_state(state)
        bf = state["backfill"]
        try:
            service = auth.get_service()
            conn = get_connection()
            emails = _fetch_candidates(service, _job_query(f"{days}d"))
            known_ids = _known_message_ids(conn)
            fresh = [e for e in emails if e["message_id"] not in known_ids]
            companies = _known_companies(conn)
            candidates = [e for e in fresh if classifier.prefilter(e, companies)]
            bf.update(phase="classifying", fetched=len(emails), total=len(candidates))
            _write_state(state)

            done = 0
            for i in range(0, len(candidates), classifier.BATCH):
                chunk = candidates[i:i + classifier.BATCH]
                result = _apply_events(conn, chunk, classifier.classify_batch(chunk))
                done += len(chunk)
                bf.update(scanned=done, events=bf["events"] + result["events"])
                _write_state(state)
            bf["phase"] = "done"
        except Exception as e:  # noqa: BLE001 — surfaced via state file
            logger.exception("Backfill failed")
            bf["phase"] = "error"
            bf["error"] = str(e)[:300]
        finally:
            bf["done_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state["last_sync"] = bf["done_at"]
            _write_state(state)

    with _backfill_lock:
        if backfill_running():
            return False
        _write_state({**{k: v for k, v in _read_state().items() if k != "backfill"},
                      "backfill": initial})
        _backfill_thread = threading.Thread(target=_run, name="ap-backfill", daemon=True)
        _backfill_thread.start()
    return True


def status() -> dict:
    conn = get_connection()
    try:
        events_total = conn.execute("SELECT COUNT(*) FROM app_events").fetchone()[0]
        matched_total = conn.execute(
            "SELECT COUNT(*) FROM app_events WHERE job_url IS NOT NULL").fetchone()[0]
    except Exception:  # noqa: BLE001 — table may not exist on old DBs
        events_total = matched_total = 0
    state = _read_state()
    return {
        "configured": auth.is_configured(),
        "last_sync": state.get("last_sync"),
        "backfill": state.get("backfill") if (backfill_running()
                    or state.get("backfill", {}).get("phase") in ("done", "error")) else None,
        "events_total": events_total,
        "matched_total": matched_total,
    }
