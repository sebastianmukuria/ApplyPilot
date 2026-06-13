"""Background operations behind the web app: pipeline runs, autopilot, digest.

Each operation runs in a daemon thread and reports progress through a small
JSON state file under the app dir, so the API (and the UI polling it) never
blocks on long work. State helpers resolve paths lazily so tests can point
APPLYPILOT_DIR at a tmp dir.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_pipeline_thread: threading.Thread | None = None
_autopilot_thread: threading.Thread | None = None
_autopilot_stop = threading.Event()
# Guard the check-then-start of each background op so two concurrent POSTs
# (a double-click, two clients) can't both pass the running() check and
# double-spawn — duplicating LLM spend / double-launching applies.
_pipeline_lock = threading.Lock()
_autopilot_lock = threading.Lock()


def _app_dir() -> Path:
    return Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_state(name: str) -> dict:
    try:
        return json.loads((_app_dir() / name).read_text())
    except (OSError, ValueError):
        return {}


def write_state(name: str, state: dict) -> None:
    try:
        path = _app_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:
        logger.debug("Could not write state file %s", name, exc_info=True)


# ---------------------------------------------------------------------------
# Pipeline runs (discover -> ... -> pdf) from the app
# ---------------------------------------------------------------------------

PIPELINE_STATE = "pipeline_state.json"


def pipeline_running() -> bool:
    return _pipeline_thread is not None and _pipeline_thread.is_alive()


def start_pipeline(stages: list[str], min_score: int = 7, workers: int = 2) -> bool:
    """Kick off a pipeline run in a daemon thread. False if one is running."""
    global _pipeline_thread
    state = {"started": _now(), "stages": stages, "current_stage": None,
             "done": {}, "done_at": None, "error": None}

    def _run() -> None:
        try:
            from applypilot.pipeline import run_pipeline

            def on_stage(stage: str, result: dict | None) -> None:
                if result is None:  # stage starting
                    state["current_stage"] = stage
                else:  # stage finished
                    state["done"][stage] = _summarize_stage(result)
                    state["current_stage"] = None
                write_state(PIPELINE_STATE, state)

            result = run_pipeline(stages=stages, min_score=min_score,
                                  workers=workers, on_stage=on_stage)
            if isinstance(result, dict) and result.get("errors"):
                state["error"] = str(result["errors"])[:500]
        except Exception as e:  # noqa: BLE001 — surfaced via state file
            logger.exception("Pipeline run failed")
            state["error"] = str(e)[:500]
        finally:
            state["current_stage"] = None
            state["done_at"] = _now()
            write_state(PIPELINE_STATE, state)

    with _pipeline_lock:
        if pipeline_running():
            return False
        # Write the starting state synchronously so the endpoint's immediate
        # status read never returns the previous run's stale file.
        write_state(PIPELINE_STATE, state)
        _pipeline_thread = threading.Thread(target=_run, name="ap-pipeline", daemon=True)
        _pipeline_thread.start()
    return True


def _summarize_stage(result) -> dict:
    if isinstance(result, dict):
        return {k: v for k, v in result.items()
                if isinstance(v, (int, float, str)) and k != "elapsed"} or {"ok": True}
    return {"ok": True}


def pipeline_status() -> dict:
    return {**read_state(PIPELINE_STATE), "running": pipeline_running()}


# ---------------------------------------------------------------------------
# Autopilot: batch-apply the best unapplied jobs through the run slots
# ---------------------------------------------------------------------------

AUTOPILOT_STATE = "autopilot_state.json"


def autopilot_running() -> bool:
    return _autopilot_thread is not None and _autopilot_thread.is_alive()


def next_autopilot_job(conn, exclude_urls: set[str]) -> dict | None:
    """The best docs-ready job not yet attempted or excluded (pure, testable)."""
    rows = conn.execute(
        """
        SELECT url, company FROM jobs
        WHERE tailored_resume_path IS NOT NULL
          AND (apply_status IS NULL OR apply_status = 'failed')
          AND (apply_attempts IS NULL OR apply_attempts < 3)
          AND fit_score IS NOT NULL
        ORDER BY fit_score DESC, url
        LIMIT 50
        """
    ).fetchall()
    for row in rows:
        if row["url"] not in exclude_urls:
            return {"url": row["url"], "company": row["company"]}
    return None


def start_autopilot(count: int, model: str = "sonnet") -> bool:
    """Launch up to `count` applies, one per free run slot. False if running."""
    global _autopilot_thread
    state = {"target": count, "launched": 0, "finished": 0, "current": [],
             "started": _now(), "done_at": None, "stopped": False}

    def _run() -> None:
        from applypilot import panel
        from applypilot.database import get_connection

        launched_urls: set[str] = set()
        tracked: set[str] = set()  # run_ids we launched

        try:
            while not _autopilot_stop.is_set():
                runs = panel.load_runs()
                alive = {rid for rid, r in runs.items() if panel._pid_alive(r.get("pid"))}
                state["finished"] = state["launched"] - len(tracked & alive)
                state["current"] = sorted(tracked & alive)
                write_state(AUTOPILOT_STATE, state)

                if state["launched"] >= count:
                    if not (tracked & alive):
                        break  # everything we launched has landed
                    time.sleep(3)
                    continue

                slot = panel.alloc_slot(panel.max_gui_runs())
                if slot is None:
                    time.sleep(5)
                    continue

                conn = get_connection()
                in_flight = {r.get("url") for r in runs.values()}
                job = next_autopilot_job(conn, launched_urls | in_flight)
                if job is None:
                    break  # queue exhausted

                run_id = panel.launch_apply_slot(job["url"], job["company"] or "job",
                                                 model, slot)
                launched_urls.add(job["url"])
                tracked.add(run_id)
                state["launched"] += 1
                write_state(AUTOPILOT_STATE, state)
                time.sleep(2)  # let the registry settle before the next slot check
        except Exception as e:  # noqa: BLE001 — surfaced via state file
            logger.exception("Autopilot crashed")
            state["error"] = str(e)[:300]
        finally:
            state["stopped"] = _autopilot_stop.is_set()
            state["done_at"] = _now()
            write_state(AUTOPILOT_STATE, state)
            try:
                from applypilot.notify import notify
                notify("batch_done",
                       f"Autopilot done: launched {state['launched']} of {state['target']} applications")
            except Exception:  # noqa: BLE001 — notification is best-effort
                logger.debug("Autopilot notify failed", exc_info=True)

    with _autopilot_lock:
        if autopilot_running():
            return False
        _autopilot_stop.clear()
        write_state(AUTOPILOT_STATE, state)
        _autopilot_thread = threading.Thread(target=_run, name="ap-autopilot", daemon=True)
        _autopilot_thread.start()
    return True


def stop_autopilot() -> None:
    """Stop launching new applies; in-flight runs finish naturally."""
    _autopilot_stop.set()


def autopilot_status() -> dict:
    return {**read_state(AUTOPILOT_STATE), "running": autopilot_running()}


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------

DIGEST_STATE = "digest_state.json"


def compose_digest(conn, since: str | None) -> str:
    """A four-line morning briefing from the jobs DB (pure, testable)."""
    day_ago = "datetime('now', '-1 day')"
    new_jobs = conn.execute(
        f"SELECT COUNT(*) FROM jobs WHERE discovered_at >= {day_ago}").fetchone()[0]
    strong = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fit_score >= 8 AND apply_status IS NULL"
    ).fetchone()[0]
    docs_ready = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL "
        "AND cover_letter_path IS NOT NULL AND apply_status IS NULL"
    ).fetchone()[0]
    applied_24h = conn.execute(
        f"SELECT COUNT(*) FROM jobs WHERE applied_at >= {day_ago}").fetchone()[0]
    lines = [
        f"Daily brief: {new_jobs} new jobs found, {strong} strong matches waiting.",
        f"{docs_ready} fully prepped and ready to apply.",
        f"{applied_24h} applied in the last 24h.",
    ]
    try:
        params = (since or "1970-01-01",)
        events = conn.execute(
            "SELECT COUNT(*) FROM app_events WHERE created_at > ?", params
        ).fetchone()[0]
        if events:
            lines.append(f"{events} new application signals — check the Pipeline Radar.")
    except Exception:  # noqa: BLE001 — app_events may not exist yet
        pass
    return "\n".join(lines)


def maybe_send_digest(now: datetime | None = None) -> bool:
    """Send the digest once per day at/after the configured hour. True if sent."""
    hour_raw = os.environ.get("APPLYPILOT_DIGEST_HOUR", "").strip()
    if hour_raw == "":
        return False
    try:
        hour = int(hour_raw)
    except ValueError:
        return False

    now = now or datetime.now()
    state = read_state(DIGEST_STATE)
    today = now.date().isoformat()
    if state.get("last_digest") == today or now.hour < hour:
        return False

    from applypilot.database import get_connection
    from applypilot.notify import notify

    text = compose_digest(get_connection(), state.get("last_digest"))
    notify("digest", text)
    write_state(DIGEST_STATE, {"last_digest": today, "sent_at": _now()})
    return True
