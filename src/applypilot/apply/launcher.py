"""Apply orchestration: acquire jobs, spawn Claude Code sessions, track results.

This is the main entry point for the apply pipeline. It pulls jobs from
the database, launches Chrome + Claude Code for each one, parses the
result, and updates the database. Supports parallel workers via --workers.
"""

import atexit
import json
import logging
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table

from applypilot import config
from applypilot.database import get_connection
from applypilot.notify import (
    last_needs_human_sent_at,
    needs_human_sent_recently,
    notify,
)
from applypilot.apply import chrome, dashboard, prompt as prompt_mod
from applypilot.apply.chrome import (
    launch_chrome, cleanup_worker, kill_all_chrome,
    reset_worker_dir, cleanup_on_exit, _kill_process_tree,
    BASE_CDP_PORT,
)
from applypilot.apply.dashboard import (
    init_worker, update_state, add_event, get_state,
    render_full, get_totals,
)

logger = logging.getLogger(__name__)

# Pinned Playwright MCP package version. Using @latest makes npx check the npm
# registry on every apply job; APPLYPILOT_MCP_VERSION is a local escape hatch.
PLAYWRIGHT_MCP_VERSION = "0.0.76"

# Blocked sites loaded from config/sites.yaml
def _load_blocked():
    from applypilot.config import load_blocked_sites
    return load_blocked_sites()

# How often to poll the DB when the queue is empty (seconds)
POLL_INTERVAL = config.DEFAULTS["poll_interval"]

# Thread-safe shutdown coordination
_stop_event = threading.Event()

# Track active Claude Code processes for skip (Ctrl+C) handling
_claude_procs: dict[int, subprocess.Popen] = {}
_claude_lock = threading.Lock()

# Register cleanup on exit
atexit.register(cleanup_on_exit)
if platform.system() != "Windows":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


# ---------------------------------------------------------------------------
# MCP config
# ---------------------------------------------------------------------------

def _make_mcp_config(cdp_port: int) -> dict:
    """Build MCP config dict for a specific CDP port."""
    mcp_version = os.environ.get("APPLYPILOT_MCP_VERSION", PLAYWRIGHT_MCP_VERSION).strip()
    if not mcp_version:
        mcp_version = PLAYWRIGHT_MCP_VERSION
    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": [
                    "-y",
                    f"@playwright/mcp@{mcp_version}",
                    f"--cdp-endpoint=http://localhost:{cdp_port}",
                    f"--viewport-size={config.DEFAULTS['viewport']}",
                ],
            },
            "gmail": {
                "command": "npx",
                "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
            },
        }
    }


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify_human(worker_id: int, reason: str) -> None:
    """Notify the user when the agent emits a ``NEEDHUMAN:`` line."""
    before = last_needs_human_sent_at(worker_id)
    notify("needs_human", reason, worker_id=worker_id)
    after = last_needs_human_sent_at(worker_id)
    if after is not None and after != before:
        add_event(f"[W{worker_id}] 🔔 pinged you: {reason[:30]}")


def _job_company(job: dict) -> str:
    return job.get("company") or job.get("site") or "the company"


def _notify_run_failed(job: dict, reason: str, worker_id: int) -> None:
    title = job.get("title") or "Application"
    notify("run_failed", f"{title} at {_job_company(job)} failed: {reason}", worker_id=worker_id)


def _notify_handoff_finished(job: dict, worker_id: int) -> None:
    if not needs_human_sent_recently(worker_id, within=60):
        notify(
            "run_finished",
            f"{_job_company(job)} is filled and waiting for your review",
            worker_id=worker_id,
        )


def _notify_applied(job: dict, worker_id: int) -> None:
    notify("run_finished", f"Applied to {_job_company(job)}", worker_id=worker_id)


def _notify_batch_done(applied: int, failed: int) -> None:
    if applied + failed > 1:
        notify("batch_done", f"Batch done: {applied} applied, {failed} failed")


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def acquire_job(target_url: str | None = None, min_score: int = 7,
                worker_id: int = 0) -> dict | None:
    """Atomically acquire the next job to apply to.

    Args:
        target_url: Apply to a specific URL instead of picking from queue.
        min_score: Minimum fit_score threshold.
        worker_id: Worker claiming this job (for tracking).

    Returns:
        Job dict or None if the queue is empty.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")

        if target_url:
            # Exact match FIRST. The query string is often the entire job
            # identity (indeed ?jk=, linkedin currentJobId=) -- stripping it
            # for a LIKE would match every job on that board and apply to an
            # arbitrary one.
            _sel = """
                SELECT url, title, company, site, application_url, tailored_resume_path,
                       fit_score, location, full_description, cover_letter_path
                FROM jobs
                WHERE ({match})
                  AND tailored_resume_path IS NOT NULL
                  AND (apply_status IS NULL OR apply_status NOT IN ('in_progress', 'applied'))
                  AND applied_at IS NULL
                LIMIT 1
            """
            row = conn.execute(_sel.format(match="url = ? OR application_url = ?"),
                               (target_url, target_url)).fetchone()
            if row is None:
                # Tolerant fallback for scheme / trailing-slash variants of the
                # SAME url -- the query string stays in the pattern.
                like = "%" + target_url.split("://", 1)[-1].rstrip("/") + "%"
                row = conn.execute(_sel.format(match="url LIKE ? OR application_url LIKE ?"),
                                   (like, like)).fetchone()
        else:
            blocked_sites, blocked_patterns = _load_blocked()
            # Build parameterized filters to avoid SQL injection
            params: list = [min_score]
            site_clause = ""
            if blocked_sites:
                placeholders = ",".join("?" * len(blocked_sites))
                site_clause = f"AND site NOT IN ({placeholders})"
                params.extend(blocked_sites)
            url_clauses = ""
            if blocked_patterns:
                url_clauses = " ".join(f"AND url NOT LIKE ?" for _ in blocked_patterns)
                params.extend(blocked_patterns)
            row = conn.execute(f"""
                SELECT url, title, company, site, application_url, tailored_resume_path,
                       fit_score, location, full_description, cover_letter_path
                FROM jobs
                WHERE tailored_resume_path IS NOT NULL
                  AND (apply_status IS NULL OR apply_status = 'failed')
                  AND (apply_attempts IS NULL OR apply_attempts < ?)
                  AND fit_score >= ?
                  {site_clause}
                  {url_clauses}
                ORDER BY fit_score DESC, url
                LIMIT 1
            """, [config.DEFAULTS["max_apply_attempts"]] + params).fetchone()

        if not row:
            conn.rollback()
            return None

        # Skip manual ATS sites (unsolvable CAPTCHAs)
        from applypilot.config import is_manual_ats
        apply_url = row["application_url"] or row["url"]
        if is_manual_ats(apply_url):
            conn.execute(
                "UPDATE jobs SET apply_status = 'manual', apply_error = 'manual ATS' WHERE url = ?",
                (row["url"],),
            )
            conn.commit()
            logger.info("Skipping manual ATS: %s", row["url"][:80])
            return None

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE jobs SET apply_status = 'in_progress',
                           agent_id = ?,
                           last_attempted_at = ?
            WHERE url = ?
        """, (f"worker-{worker_id}", now, row["url"]))
        conn.commit()

        return dict(row)
    except Exception:
        conn.rollback()
        raise


def mark_result(url: str, status: str, error: str | None = None,
                permanent: bool = False, duration_ms: int | None = None,
                task_id: str | None = None) -> None:
    """Update a job's apply status in the database."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    if status == "applied":
        conn.execute("""
            UPDATE jobs SET apply_status = 'applied', applied_at = ?,
                           apply_error = NULL, agent_id = NULL,
                           apply_duration_ms = ?, apply_task_id = ?
            WHERE url = ?
        """, (now, duration_ms, task_id, url))
    else:
        attempts = 99 if permanent else "COALESCE(apply_attempts, 0) + 1"
        conn.execute(f"""
            UPDATE jobs SET apply_status = ?, apply_error = ?,
                           apply_attempts = {attempts}, agent_id = NULL,
                           apply_duration_ms = ?, apply_task_id = ?
            WHERE url = ?
        """, (status, error or "unknown", duration_ms, task_id, url))
    conn.commit()


def release_lock(url: str) -> None:
    """Release the in_progress lock without changing status."""
    conn = get_connection()
    conn.execute(
        "UPDATE jobs SET apply_status = NULL, agent_id = NULL WHERE url = ? AND apply_status = 'in_progress'",
        (url,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Utility modes (--gen, --mark-applied, --mark-failed, --reset-failed)
# ---------------------------------------------------------------------------

def gen_prompt(target_url: str, min_score: int = 7,
               model: str = "sonnet", worker_id: int = 0) -> Path | None:
    """Generate a prompt file and print the Claude CLI command for manual debugging.

    Returns:
        Path to the generated prompt file, or None if no job found.
    """
    job = acquire_job(target_url=target_url, min_score=min_score, worker_id=worker_id)
    if not job:
        return None

    # Read resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    prompt = prompt_mod.build_prompt(job=job, tailored_resume=resume_text, worker_id=worker_id)

    # Release the lock so the job stays available
    release_lock(job["url"])

    # Write prompt file
    config.ensure_dirs()
    site_slug = (job.get("site") or "unknown")[:20].replace(" ", "_")
    prompt_file = config.LOG_DIR / f"prompt_{site_slug}_{job['title'][:30].replace(' ', '_')}.txt"
    # The generated prompt embeds the CapSolver key and the job-site password.
    config.write_private_text(prompt_file, prompt)

    # Write MCP config for reference
    port = BASE_CDP_PORT + worker_id
    mcp_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
    config.write_private_text(mcp_path, json.dumps(_make_mcp_config(port)))

    return prompt_file


def mark_job(url: str, status: str, reason: str | None = None) -> None:
    """Manually mark a job's apply status in the database.

    Args:
        url: Job URL to mark.
        status: Either 'applied' or 'failed'.
        reason: Failure reason (only for status='failed').
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    if status == "applied":
        conn.execute("""
            UPDATE jobs SET apply_status = 'applied', applied_at = ?,
                           apply_error = NULL, agent_id = NULL
            WHERE url = ?
        """, (now, url))
    else:
        conn.execute("""
            UPDATE jobs SET apply_status = 'failed', apply_error = ?,
                           apply_attempts = 99, agent_id = NULL
            WHERE url = ?
        """, (reason or "manual", url))
    conn.commit()


def reset_stale_locks() -> int:
    """Clear jobs stuck in 'in_progress' from a previous crashed run.

    All workers live in this process, so anything still 'in_progress' at startup
    is by definition stale (the worker that held it is gone). Returns NULL so the
    job is eligible again.

    Returns:
        Number of stale locks cleared.
    """
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE jobs SET apply_status = NULL, agent_id = NULL "
        "WHERE apply_status = 'in_progress'"
    )
    conn.commit()
    return cursor.rowcount


def reset_failed() -> int:
    """Reset all failed jobs so they can be retried.

    Returns:
        Number of jobs reset.
    """
    conn = get_connection()
    cursor = conn.execute("""
        UPDATE jobs SET apply_status = NULL, apply_error = NULL,
                       apply_attempts = 0, agent_id = NULL
        WHERE apply_status = 'failed'
          OR (apply_status IS NOT NULL AND apply_status != 'applied'
              AND apply_status != 'in_progress')
    """)
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Timing instrumentation
# ---------------------------------------------------------------------------

_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)


def _display_tool_name(name: str) -> str:
    """Normalize MCP tool names for logs and timing summaries."""
    return (
        name
        .replace("mcp__playwright__", "")
        .replace("mcp__gmail__", "gmail:")
    )


def _new_timing_state(spawn_ts: float) -> dict:
    return {
        "spawn_ts": spawn_ts,
        "first_event_ts": None,
        "last_event_ts": None,
        "model_count": 0,
        "model_total_s": 0.0,
        "reported_turns": None,
        "tokens": {},
        "pending_tools": {},
        "tool_calls": {},
        "slowest": [],
    }


def _content_blocks(msg: dict) -> list[dict]:
    message = msg.get("message")
    content = message.get("content") if isinstance(message, dict) else msg.get("content")
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _record_usage_tokens(state: dict, usage: object) -> None:
    if not isinstance(usage, dict):
        return
    tokens = state["tokens"]
    for key in _TOKEN_KEYS:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            tokens[key] = tokens.get(key, 0) + int(value)


def _observe_timing_event(state: dict, msg: dict, now: float) -> None:
    """Update timing state from one parsed Claude stream-json event."""
    if state["first_event_ts"] is None:
        state["first_event_ts"] = now

    msg_type = msg.get("type")
    if msg_type == "assistant":
        prev = state["last_event_ts"]
        turn_start = state["spawn_ts"] if prev is None else prev
        state["model_count"] += 1
        state["model_total_s"] += max(0.0, now - turn_start)

    _record_usage_tokens(state, msg.get("usage"))
    message = msg.get("message")
    if isinstance(message, dict):
        _record_usage_tokens(state, message.get("usage"))

    if msg_type == "result":
        turns = msg.get("num_turns")
        if isinstance(turns, int):
            state["reported_turns"] = turns

    for block in _content_blocks(msg):
        block_type = block.get("type")
        if block_type == "tool_use":
            tool_id = block.get("id") or block.get("tool_use_id")
            if not tool_id:
                continue
            state["pending_tools"][tool_id] = {
                "name": _display_tool_name(str(block.get("name", "unknown"))),
                "start": now,
            }
        elif block_type == "tool_result":
            tool_id = block.get("tool_use_id") or block.get("id")
            if not tool_id:
                continue
            pending = state["pending_tools"].pop(tool_id, None)
            if not pending:
                continue
            duration = max(0.0, now - pending["start"])
            name = pending["name"]
            bucket = state["tool_calls"].setdefault(name, {"count": 0, "total_s": 0.0})
            bucket["count"] += 1
            bucket["total_s"] += duration
            state["slowest"].append({
                "name": name,
                "duration_s": duration,
                "tool_use_id": tool_id,
            })

    state["last_event_ts"] = now


def _finish_timing_summary(state: dict, job_url: str, result: str,
                           finished_ts: float) -> dict:
    startup = None
    if state["first_event_ts"] is not None:
        startup = max(0.0, state["first_event_ts"] - state["spawn_ts"])

    model_count = state["model_count"]
    if not model_count and state["reported_turns"]:
        model_count = state["reported_turns"]

    model_turns: dict = {
        "count": int(model_count or 0),
        "total_s": round(float(state["model_total_s"]), 3),
    }
    if state["tokens"]:
        model_turns["tokens"] = dict(sorted(state["tokens"].items()))
    if state["reported_turns"] is not None:
        model_turns["reported_count"] = state["reported_turns"]

    tool_calls = {
        name: {
            "count": int(data["count"]),
            "total_s": round(float(data["total_s"]), 3),
        }
        for name, data in sorted(state["tool_calls"].items())
    }
    slowest = sorted(state["slowest"], key=lambda x: x["duration_s"], reverse=True)[:10]
    for item in slowest:
        item["duration_s"] = round(float(item["duration_s"]), 3)

    return {
        "job_url": job_url,
        "total_s": round(max(0.0, finished_ts - state["spawn_ts"]), 3),
        "startup_s": round(startup, 3) if startup is not None else None,
        "model_turns": model_turns,
        "tool_calls": tool_calls,
        "top_slowest": slowest,
        "result": result,
    }


def summarize_stream_json_events(records, *, spawn_ts: float = 0.0,
                                 finished_ts: float | None = None,
                                 job_url: str = "",
                                 result: str = "") -> dict:
    """Pure timing parser for Claude stream-json events.

    ``records`` is an iterable of ``(timestamp, event)`` pairs where event is
    either a parsed dict or one JSON line. Invalid JSON strings are ignored.
    """
    state = _new_timing_state(spawn_ts)
    last_ts = spawn_ts
    for ts, raw in records:
        if isinstance(raw, str):
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(raw, dict):
            msg = raw
        else:
            continue
        _observe_timing_event(state, msg, ts)
        last_ts = ts
    return _finish_timing_summary(
        state,
        job_url=job_url,
        result=result,
        finished_ts=finished_ts if finished_ts is not None else last_ts,
    )


def _write_timing_summary(state: dict, *, job_url: str, worker_id: int,
                          result: str, finished_ts: float) -> None:
    try:
        summary = _finish_timing_summary(state, job_url, result, finished_ts)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = config.LOG_DIR / f"timing_{ts}_w{worker_id}.json"
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        config.write_private_text(path, json.dumps(summary, indent=2, sort_keys=True))
    except Exception:
        logger.debug("Failed to write timing summary", exc_info=True)


def _result_line_from_output(output: str, fallback: str) -> str:
    for line in output.splitlines():
        if "RESULT:" in line:
            return line.strip()
    return fallback


def load_timing_reports(log_dir: Path | None = None) -> list[dict]:
    """Load all timing_*.json files from a log directory."""
    root = Path(log_dir or config.LOG_DIR)
    reports: list[dict] = []
    for path in sorted(root.glob("timing_*_w*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_file"] = str(path)
                reports.append(data)
        except Exception:
            logger.debug("Skipping unreadable timing report: %s", path, exc_info=True)
    return reports


def _model_turn_parts(report: dict) -> tuple[int, float]:
    model_turns = report.get("model_turns") or {}
    if isinstance(model_turns, dict):
        return int(model_turns.get("count") or 0), float(model_turns.get("total_s") or 0.0)
    if isinstance(model_turns, int):
        return model_turns, 0.0
    return 0, 0.0


def aggregate_timing_reports(reports: list[dict]) -> dict:
    """Aggregate timing summaries for CLI reporting and tests."""
    count = len(reports)
    total_s = sum(float(r.get("total_s") or 0.0) for r in reports)
    startup_values = [
        float(r["startup_s"]) for r in reports
        if r.get("startup_s") is not None
    ]
    model_counts = 0
    model_total_s = 0.0
    tools: dict[str, dict] = {}
    for report in reports:
        c, total = _model_turn_parts(report)
        model_counts += c
        model_total_s += total
        for name, data in (report.get("tool_calls") or {}).items():
            bucket = tools.setdefault(name, {"count": 0, "total_s": 0.0})
            bucket["count"] += int(data.get("count") or 0)
            bucket["total_s"] += float(data.get("total_s") or 0.0)

    tool_breakdown = {
        name: {
            "count": data["count"],
            "total_s": round(data["total_s"], 3),
            "avg_s": round(data["total_s"] / data["count"], 3) if data["count"] else 0.0,
        }
        for name, data in sorted(
            tools.items(),
            key=lambda item: item[1]["total_s"],
            reverse=True,
        )
    }
    slowest_runs = sorted(
        (
            {
                "job_url": r.get("job_url", ""),
                "total_s": float(r.get("total_s") or 0.0),
                "result": r.get("result", ""),
                "file": r.get("_file", ""),
            }
            for r in reports
        ),
        key=lambda r: r["total_s"],
        reverse=True,
    )[:3]

    return {
        "runs": count,
        "avg_total_s": round(total_s / count, 3) if count else 0.0,
        "avg_startup_s": round(sum(startup_values) / len(startup_values), 3) if startup_values else 0.0,
        "avg_model_turn_s": round(model_total_s / model_counts, 3) if model_counts else 0.0,
        "model_turns": model_counts,
        "tool_calls": tool_breakdown,
        "slowest_runs": slowest_runs,
    }


def print_timing_report(log_dir: Path | None = None,
                        console: Console | None = None) -> None:
    """Print an aggregate timing report from timing_*.json files."""
    console = console or Console()
    reports = load_timing_reports(log_dir)
    if not reports:
        console.print("[yellow]No timing reports found.[/yellow]")
        return

    aggregate = aggregate_timing_reports(reports)

    summary = Table(title="ApplyPilot Timing Summary")
    summary.add_column("Metric")
    summary.add_column("Value", justify="right")
    summary.add_row("Runs", str(aggregate["runs"]))
    summary.add_row("Avg total", f"{aggregate['avg_total_s']:.1f}s")
    summary.add_row("Avg startup", f"{aggregate['avg_startup_s']:.1f}s")
    summary.add_row("Avg model turn", f"{aggregate['avg_model_turn_s']:.1f}s")
    summary.add_row("Model turns", str(aggregate["model_turns"]))
    console.print(summary)

    tools = Table(title="Tool Calls")
    tools.add_column("Tool")
    tools.add_column("Calls", justify="right")
    tools.add_column("Total", justify="right")
    tools.add_column("Avg", justify="right")
    for name, data in aggregate["tool_calls"].items():
        tools.add_row(
            name,
            str(data["count"]),
            f"{data['total_s']:.1f}s",
            f"{data['avg_s']:.1f}s",
        )
    console.print(tools)

    slowest = Table(title="3 Slowest Runs")
    slowest.add_column("Total", justify="right")
    slowest.add_column("Result")
    slowest.add_column("Job URL")
    for run in aggregate["slowest_runs"]:
        slowest.add_row(f"{run['total_s']:.1f}s", run["result"], run["job_url"])
    console.print(slowest)


# ---------------------------------------------------------------------------
# Per-job execution
# ---------------------------------------------------------------------------

# Gmail MCP tools the agent must never use (drafts, deletes, label/filter admin).
_GMAIL_DISALLOWED = (
    "mcp__gmail__draft_email,mcp__gmail__modify_email,"
    "mcp__gmail__delete_email,mcp__gmail__download_attachment,"
    "mcp__gmail__batch_modify_emails,mcp__gmail__batch_delete_emails,"
    "mcp__gmail__create_label,mcp__gmail__update_label,"
    "mcp__gmail__delete_label,mcp__gmail__get_or_create_label,"
    "mcp__gmail__list_email_labels,mcp__gmail__create_filter,"
    "mcp__gmail__list_filters,mcp__gmail__get_filter,"
    "mcp__gmail__delete_filter"
)


def _build_claude_cmd(model: str, mcp_config_path: str, dry_run: bool = False) -> list[str]:
    """Build the `claude` subprocess argv for the apply agent.

    The agent runs with bypassPermissions, so the disallowed-tools list is the
    only guard. In dry-run mode the Gmail send tool is also disallowed so the
    agent cannot send a real application email.
    """
    # The agent browses untrusted employer pages with bypassPermissions, so a
    # prompt injection could run shell or exfiltrate the profile/.env. Deny all
    # built-in tools that touch the host or the network outside the browser.
    # Read stays allowed (used to inspect the tailored resume).
    # browser_close is banned outright: Chrome's lifecycle belongs to the
    # launcher, and an agent "tidying up" after a supervised hand-off closes
    # the tab the human is still reviewing.
    disallowed = (
        "Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task,KillShell,"
        "mcp__playwright__browser_close,"
        + _GMAIL_DISALLOWED
    )
    if dry_run:
        disallowed += ",mcp__gmail__send_email"
    return [
        "claude",
        "--model", model,
        "-p",
        "--mcp-config", mcp_config_path,
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
        "--disallowedTools", disallowed,
        "--output-format", "stream-json",
        "--verbose", "-",
    ]

def run_job(job: dict, port: int, worker_id: int = 0,
            model: str = "sonnet", dry_run: bool = False) -> tuple[str, int]:
    """Spawn a Claude Code session for one job application.

    Returns:
        Tuple of (status_string, duration_ms). Status is one of:
        'applied', 'expired', 'captcha', 'login_issue',
        'failed:reason', or 'skipped'.
    """
    # Read tailored resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    # Reset the worker dir FIRST: build_prompt copies the resume/cover PDFs into
    # APPLY_WORKER_DIR/worker-{id}/current, which reset_worker_dir would wipe.
    worker_dir = reset_worker_dir(worker_id)

    # Build the prompt
    agent_prompt = prompt_mod.build_prompt(
        job=job,
        tailored_resume=resume_text,
        dry_run=dry_run,
        worker_id=worker_id,
    )

    # Write per-worker MCP config
    mcp_config_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
    config.write_private_text(mcp_config_path, json.dumps(_make_mcp_config(port)))

    # Build claude command
    cmd = _build_claude_cmd(model, str(mcp_config_path), dry_run)

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    update_state(worker_id, status="applying", job_title=job["title"],
                 company=job.get("company") or job.get("site", ""), score=job.get("fit_score", 0),
                 start_time=time.time(), actions=0, last_action="starting")
    add_event(f"[W{worker_id}] Starting: {job['title'][:40]} @ {job.get('site', '')}")

    worker_log = config.LOG_DIR / f"worker-{worker_id}.log"
    ts_header = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_header = (
        f"\n{'=' * 60}\n"
        f"[{ts_header}] {job['title']} @ {job.get('site', '')}\n"
        f"URL: {job.get('application_url') or job['url']}\n"
        f"Score: {job.get('fit_score', 'N/A')}/10\n"
        f"{'=' * 60}\n"
    )

    start = time.time()
    timing_state = _new_timing_state(start)
    timing_result = ""
    stats: dict = {}
    proc = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(worker_dir),
        )
        timing_state = _new_timing_state(time.time())
        with _claude_lock:
            _claude_procs[worker_id] = proc

        proc.stdin.write(agent_prompt)
        proc.stdin.close()

        text_parts: list[str] = []
        with open(worker_log, "a", encoding="utf-8") as lf:
            lf.write(log_header)

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    try:
                        _observe_timing_event(timing_state, msg, time.time())
                    except Exception:
                        logger.debug("Timing instrumentation failed for stream event", exc_info=True)
                    msg_type = msg.get("type")
                    if msg_type == "assistant":
                        for block in msg.get("message", {}).get("content", []):
                            bt = block.get("type")
                            if bt == "text":
                                text_parts.append(block["text"])
                                lf.write(block["text"] + "\n")
                                if "NEEDHUMAN:" in block["text"]:
                                    reason = block["text"].split("NEEDHUMAN:", 1)[1].strip().split("\n")[0][:160]
                                    _notify_human(worker_id, reason or "an application needs your input")
                            elif bt == "tool_use":
                                name = _display_tool_name(block.get("name", ""))
                                inp = block.get("input", {})
                                if "url" in inp:
                                    desc = f"{name} {inp['url'][:60]}"
                                elif "ref" in inp:
                                    desc = f"{name} {inp.get('element', inp.get('text', ''))}"[:50]
                                elif "fields" in inp:
                                    desc = f"{name} ({len(inp['fields'])} fields)"
                                elif "paths" in inp:
                                    desc = f"{name} upload"
                                else:
                                    desc = name

                                lf.write(f"  >> {desc}\n")
                                ws = get_state(worker_id)
                                cur_actions = ws.actions if ws else 0
                                update_state(worker_id,
                                             actions=cur_actions + 1,
                                             last_action=desc[:35])
                    elif msg_type == "result":
                        stats = {
                            "input_tokens": msg.get("usage", {}).get("input_tokens", 0),
                            "output_tokens": msg.get("usage", {}).get("output_tokens", 0),
                            "cache_read": msg.get("usage", {}).get("cache_read_input_tokens", 0),
                            "cache_create": msg.get("usage", {}).get("cache_creation_input_tokens", 0),
                            "cost_usd": msg.get("total_cost_usd", 0),
                            "turns": msg.get("num_turns", 0),
                        }
                        text_parts.append(msg.get("result", ""))
                except json.JSONDecodeError:
                    text_parts.append(line)
                    lf.write(line + "\n")

        proc.wait(timeout=300)
        returncode = proc.returncode
        proc = None

        if returncode and returncode < 0:
            timing_result = "skipped"
            return "skipped", int((time.time() - start) * 1000)

        output = "\n".join(text_parts)
        elapsed = int(time.time() - start)
        duration_ms = int((time.time() - start) * 1000)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_log = config.LOG_DIR / f"claude_{ts}_w{worker_id}_{job.get('site', 'unknown')[:20]}.txt"
        job_log.write_text(output, encoding="utf-8")

        if stats:
            cost = stats.get("cost_usd", 0)
            ws = get_state(worker_id)
            prev_cost = ws.total_cost if ws else 0.0
            update_state(worker_id, total_cost=prev_cost + cost)

        def _clean_reason(s: str) -> str:
            return re.sub(r'[*`"]+$', '', s).strip()

        for result_status in ["DRYRUN", "HANDOFF", "APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"]:
            if f"RESULT:{result_status}" in output:
                timing_result = _result_line_from_output(output, f"RESULT:{result_status}")
                add_event(f"[W{worker_id}] {result_status} ({elapsed}s): {job['title'][:30]}")
                update_state(worker_id, status=result_status.lower(),
                             last_action=f"{result_status} ({elapsed}s)")
                return result_status.lower(), duration_ms

        if "RESULT:FAILED" in output:
            for out_line in output.split("\n"):
                if "RESULT:FAILED" in out_line:
                    reason = (
                        out_line.split("RESULT:FAILED:")[-1].strip()
                        if ":" in out_line[out_line.index("FAILED") + 6:]
                        else "unknown"
                    )
                    reason = _clean_reason(reason)
                    PROMOTE_TO_STATUS = {"captcha", "expired", "login_issue"}
                    if reason in PROMOTE_TO_STATUS:
                        timing_result = out_line.strip()
                        add_event(f"[W{worker_id}] {reason.upper()} ({elapsed}s): {job['title'][:30]}")
                        update_state(worker_id, status=reason,
                                     last_action=f"{reason.upper()} ({elapsed}s)")
                        return reason, duration_ms
                    timing_result = out_line.strip()
                    add_event(f"[W{worker_id}] FAILED ({elapsed}s): {reason[:30]}")
                    update_state(worker_id, status="failed",
                                 last_action=f"FAILED: {reason[:25]}")
                    return f"failed:{reason}", duration_ms
            timing_result = "RESULT:FAILED:unknown"
            return "failed:unknown", duration_ms

        timing_result = "RESULT:FAILED:no_result_line"
        add_event(f"[W{worker_id}] NO RESULT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"no result ({elapsed}s)")
        return "failed:no_result_line", duration_ms

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        elapsed = int(time.time() - start)
        timing_result = "RESULT:FAILED:timeout"
        add_event(f"[W{worker_id}] TIMEOUT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"TIMEOUT ({elapsed}s)")
        return "failed:timeout", duration_ms
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        timing_result = f"RESULT:FAILED:{str(e)[:100]}"
        add_event(f"[W{worker_id}] ERROR: {str(e)[:40]}")
        update_state(worker_id, status="failed", last_action=f"ERROR: {str(e)[:25]}")
        return f"failed:{str(e)[:100]}", duration_ms
    finally:
        _write_timing_summary(
            timing_state,
            job_url=job.get("application_url") or job.get("url", ""),
            worker_id=worker_id,
            result=timing_result or "unknown",
            finished_ts=time.time(),
        )
        with _claude_lock:
            _claude_procs.pop(worker_id, None)
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)


# ---------------------------------------------------------------------------
# Permanent failure classification
# ---------------------------------------------------------------------------

PERMANENT_FAILURES: set[str] = {
    "expired", "captcha", "login_issue",
    "not_eligible_location", "not_eligible_salary",
    "already_applied", "account_required",
    "not_a_job_application", "unsafe_permissions",
    "unsafe_verification", "sso_required",
    "site_blocked", "cloudflare_blocked", "blocked_by_cloudflare",
}

PERMANENT_PREFIXES: tuple[str, ...] = ("site_blocked", "cloudflare", "blocked_by")


def _is_permanent_failure(result: str) -> bool:
    """Determine if a failure should never be retried."""
    reason = result.split(":", 1)[-1] if ":" in result else result
    return (
        result in PERMANENT_FAILURES
        or reason in PERMANENT_FAILURES
        or any(reason.startswith(p) for p in PERMANENT_PREFIXES)
    )


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

def worker_loop(worker_id: int = 0, limit: int = 1,
                target_url: str | None = None,
                min_score: int = 7, headless: bool = False,
                model: str = "sonnet", dry_run: bool = False) -> tuple[int, int]:
    """Run jobs sequentially until limit is reached or queue is empty.

    Args:
        worker_id: Numeric worker identifier.
        limit: Max jobs to process (0 = continuous).
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        headless: Run Chrome headless.
        model: Claude model name.
        dry_run: Don't click Submit.

    Returns:
        Tuple of (applied_count, failed_count).
    """
    applied = 0
    failed = 0
    continuous = limit == 0
    jobs_done = 0
    empty_polls = 0
    port = BASE_CDP_PORT + worker_id
    # Dry-run releases its lock without changing status, so a job returns to the
    # head of the queue and would be re-selected forever. Track what we've
    # already dry-run this session and stop when the queue only repeats.
    seen_urls: set[str] = set()
    chrome_proc = None
    browser_used = False
    relaunch_before_next = False

    try:
        while not _stop_event.is_set():
            if not continuous and jobs_done >= limit:
                break

            update_state(worker_id, status="idle", job_title="", company="",
                         last_action="waiting for job", actions=0)

            job = acquire_job(target_url=target_url, min_score=min_score,
                              worker_id=worker_id)
            if dry_run and job and job["url"] in seen_urls:
                release_lock(job["url"])
                add_event(f"[W{worker_id}] Dry-run queue exhausted")
                update_state(worker_id, status="done", last_action="dry-run done")
                break
            if not job:
                if not continuous:
                    add_event(f"[W{worker_id}] Queue empty")
                    update_state(worker_id, status="done", last_action="queue empty")
                    break
                empty_polls += 1
                update_state(worker_id, status="idle",
                             last_action=f"polling ({empty_polls})")
                if empty_polls == 1:
                    add_event(f"[W{worker_id}] Queue empty, polling every {POLL_INTERVAL}s...")
                # Use Event.wait for interruptible sleep
                if _stop_event.wait(timeout=POLL_INTERVAL):
                    break  # Stop was requested during wait
                continue

            empty_polls = 0
            seen_urls.add(job["url"])

            result = ""
            try:
                if chrome_proc is None or chrome_proc.poll() is not None:
                    add_event(f"[W{worker_id}] Launching Chrome...")
                    # the actual port can shift if a handed-off Chrome still owns ours
                    chrome_proc, port = launch_chrome(worker_id, port=port, headless=headless)
                    browser_used = False
                    relaunch_before_next = False
                elif relaunch_before_next:
                    add_event(f"[W{worker_id}] Relaunching Chrome after previous failure...")
                    cleanup_worker(worker_id, chrome_proc)
                    chrome_proc = None
                    chrome_proc, port = launch_chrome(worker_id, port=port, headless=headless)
                    browser_used = False
                    relaunch_before_next = False
                elif browser_used:
                    add_event(f"[W{worker_id}] Resetting browser tabs...")
                    if not chrome.reset_tabs(port):
                        add_event(f"[W{worker_id}] Tab reset failed; relaunching Chrome...")
                        cleanup_worker(worker_id, chrome_proc)
                        chrome_proc = None
                        chrome_proc, port = launch_chrome(worker_id, port=port, headless=headless)
                        browser_used = False

                result, duration_ms = run_job(job, port=port, worker_id=worker_id,
                                                model=model, dry_run=dry_run)
                browser_used = True
                relaunch_before_next = result not in {"applied", "dryrun"}

                if result == "skipped":
                    release_lock(job["url"])
                    add_event(f"[W{worker_id}] Skipped: {job['title'][:30]}")
                    continue
                elif result == "dryrun":
                    # No DB side effects; release the lock and fall through to the
                    # loop tail (jobs_done/target_url) -- do NOT `continue`.
                    release_lock(job["url"])
                    add_event(f"[W{worker_id}] DRY RUN OK: {job['title'][:30]}")
                elif result == "handoff":
                    # Supervised hand-off: agent filled the form and left the browser
                    # open for the human to finish (review, assessment, submit). Detach
                    # Chrome so it is NOT killed by worker cleanup.
                    chrome.detach_worker(worker_id)
                    chrome_proc = None
                    browser_used = False
                    relaunch_before_next = False
                    mark_result(job["url"], "handoff")
                    add_event(f"[W{worker_id}] Handed off -- browser left open for you: {job['title'][:30]}")
                    _notify_handoff_finished(job, worker_id)
                elif result == "applied" and dry_run:
                    # Agent ignored the dry-run instruction and claimed APPLIED.
                    # Do NOT mark applied -- release and warn.
                    release_lock(job["url"])
                    logger.warning("Worker %d: agent emitted APPLIED during dry-run; not marking", worker_id)
                    add_event(f"[W{worker_id}] Dry-run: ignored stray APPLIED")
                elif result == "applied":
                    mark_result(job["url"], "applied", duration_ms=duration_ms)
                    _notify_applied(job, worker_id)
                    applied += 1
                    update_state(worker_id, jobs_applied=applied,
                                 jobs_done=applied + failed)
                else:
                    reason = result.split(":", 1)[-1] if ":" in result else result
                    mark_result(job["url"], "failed", reason,
                                permanent=_is_permanent_failure(result),
                                duration_ms=duration_ms)
                    _notify_run_failed(job, reason, worker_id)
                    failed += 1
                    update_state(worker_id, jobs_failed=failed,
                                 jobs_done=applied + failed)

            except KeyboardInterrupt:
                relaunch_before_next = True
                release_lock(job["url"])
                if _stop_event.is_set():
                    break
                add_event(f"[W{worker_id}] Job skipped (Ctrl+C)")
                continue
            except Exception as e:
                relaunch_before_next = True
                logger.exception("Worker %d launcher error", worker_id)
                add_event(f"[W{worker_id}] Launcher error: {str(e)[:40]}")
                release_lock(job["url"])
                failed += 1
                update_state(worker_id, jobs_failed=failed)

            jobs_done += 1
            # A hand-off ends the run: the human is now busy finishing that one
            # (review/assessment/submit) in the browser we left open. Re-run apply
            # to get the next job once they're done.
            if target_url or result == "handoff":
                if result == "handoff":
                    add_event(f"[W{worker_id}] Stopping after hand-off -- finish it, then re-run apply for the next")
                break
    finally:
        if chrome_proc:
            cleanup_worker(worker_id, chrome_proc)

    update_state(worker_id, status="done", last_action="finished")
    return applied, failed


# ---------------------------------------------------------------------------
# Main entry point (called from cli.py)
# ---------------------------------------------------------------------------

def main(limit: int = 1, target_url: str | None = None,
         min_score: int = 7, headless: bool = False, model: str = "sonnet",
         dry_run: bool = False, continuous: bool = False,
         poll_interval: int = 60, workers: int = 1,
         worker_slot: int = 0) -> None:
    """Launch the apply pipeline.

    Args:
        limit: Max jobs to apply to (0 or with continuous=True means run forever).
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        headless: Run Chrome in headless mode.
        model: Claude model name.
        dry_run: Don't click Submit.
        continuous: Run forever, polling for new jobs.
        poll_interval: Seconds between DB polls when queue is empty.
        workers: Number of parallel workers (default 1).
        worker_slot: Worker slot to use for a single target URL.
    """
    global POLL_INTERVAL
    POLL_INTERVAL = poll_interval
    _stop_event.clear()

    config.ensure_dirs()
    console = Console()

    # Recover jobs stranded 'in_progress' by a previous crashed run.
    recovered = reset_stale_locks()
    if recovered:
        console.print(f"[yellow]Recovered {recovered} stale in-progress job(s)[/yellow]")

    if continuous:
        effective_limit = 0
        mode_label = "continuous"
    else:
        effective_limit = limit
        mode_label = f"{limit} jobs"

    single_worker_id = worker_slot if target_url and workers == 1 else 0

    # Initialize dashboard for all workers
    if workers == 1:
        init_worker(single_worker_id)
    else:
        for i in range(workers):
            init_worker(i)

    worker_label = f"{workers} worker{'s' if workers > 1 else ''}"
    console.print(f"Launching apply pipeline ({mode_label}, {worker_label}, poll every {POLL_INTERVAL}s)...")
    console.print("[dim]Ctrl+C = skip current job(s) | Ctrl+C x2 = stop[/dim]")

    # Double Ctrl+C handler
    _ctrl_c_count = 0

    def _sigint_handler(sig, frame):
        nonlocal _ctrl_c_count
        _ctrl_c_count += 1
        if _ctrl_c_count == 1:
            console.print("\n[yellow]Skipping current job(s)... (Ctrl+C again to STOP)[/yellow]")
            # Kill all active Claude processes to skip current jobs
            with _claude_lock:
                for wid, cproc in list(_claude_procs.items()):
                    if cproc.poll() is None:
                        _kill_process_tree(cproc.pid)
        else:
            console.print("\n[red bold]STOPPING[/red bold]")
            _stop_event.set()
            with _claude_lock:
                for wid, cproc in list(_claude_procs.items()):
                    if cproc.poll() is None:
                        _kill_process_tree(cproc.pid)
            kill_all_chrome()
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        with Live(render_full(), console=console, refresh_per_second=2) as live:
            # Daemon thread for display refresh only (no business logic)
            _dashboard_running = True

            def _refresh():
                while _dashboard_running:
                    live.update(render_full())
                    time.sleep(0.5)

            refresh_thread = threading.Thread(target=_refresh, daemon=True)
            refresh_thread.start()

            if workers == 1:
                # Single worker — run directly in main thread
                total_applied, total_failed = worker_loop(
                    worker_id=single_worker_id,
                    limit=effective_limit,
                    target_url=target_url,
                    min_score=min_score,
                    headless=headless,
                    model=model,
                    dry_run=dry_run,
                )
            else:
                # Multi-worker — distribute limit across workers
                if effective_limit:
                    base = effective_limit // workers
                    extra = effective_limit % workers
                    limits = [base + (1 if i < extra else 0)
                              for i in range(workers)]
                else:
                    limits = [0] * workers  # continuous mode

                with ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="apply-worker") as executor:
                    futures = {
                        executor.submit(
                            worker_loop,
                            worker_id=i,
                            limit=limits[i],
                            target_url=target_url,
                            min_score=min_score,
                            headless=headless,
                            model=model,
                            dry_run=dry_run,
                        ): i
                        for i in range(workers)
                    }

                    results: list[tuple[int, int]] = []
                    for future in as_completed(futures):
                        wid = futures[future]
                        try:
                            results.append(future.result())
                        except Exception:
                            logger.exception("Worker %d crashed", wid)
                            results.append((0, 0))

                total_applied = sum(r[0] for r in results)
                total_failed = sum(r[1] for r in results)

            _dashboard_running = False
            refresh_thread.join(timeout=2)
            live.update(render_full())

        totals = get_totals()
        console.print(
            f"\n[bold]Done: {total_applied} applied, {total_failed} failed "
            f"(${totals['cost']:.3f})[/bold]"
        )
        _notify_batch_done(total_applied, total_failed)
        console.print(f"Logs: {config.LOG_DIR}")

    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        kill_all_chrome()
