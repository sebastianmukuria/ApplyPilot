"""FastAPI backend for the ApplyPilot v2 web app."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from applypilot import panel


class UrlBody(BaseModel):
    url: str


class RunBody(BaseModel):
    url: str
    model: str = "sonnet"
    supervised: bool | None = None


class SettingsUpdate(BaseModel):
    supervised: bool | None = None
    fixed_resume: bool | None = None
    salary_mode: str | None = None
    salary_fixed: str | None = None


class WorkContextBody(BaseModel):
    projects: list[dict] = Field(default_factory=list)
    llm_usage: str = ""
    answer_rules: str = ""


class AnswerBody(BaseModel):
    company: str = ""
    question: str
    length: str = "2-3 sentences"
    prev: str = ""


def create_app() -> FastAPI:
    """Create the ApplyPilot API app."""
    _ensure_storage()
    app = FastAPI(title="ApplyPilot API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/jobs")
    def list_jobs(
        min_score: int = 8,
        sources: str | None = None,
        search: str | None = None,
        sort: Literal["score", "salary", "company", "location", "recent"] = "score",
        show_applied: bool = False,
        only_docs_ready: bool = False,
        include_no_salary: bool = True,
        min_salary_k: int = 0,
        hide_flagged: bool = True,
        hidden: bool = False,
        limit: int = Query(200, ge=0),
        offset: int = Query(0, ge=0),
    ):
        rows = _queue_rows(
            min_score=min_score,
            sources=_parse_sources(sources),
            search=search,
            sort=sort,
            show_applied=show_applied,
            only_docs_ready=only_docs_ready,
            include_no_salary=include_no_salary,
            min_salary_k=min_salary_k,
            hide_flagged=hide_flagged,
            include_hidden=hidden,
        )
        hidden_urls = panel.load_hidden()
        page = rows[offset:offset + limit] if limit else []
        return {
            "total_matched": len(rows),
            "jobs": [_job_shape(r, hidden_urls=hidden_urls) for r in page],
        }

    @app.get("/api/jobs/detail")
    def job_detail(url: str):
        row = _get_job(url)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        data = _job_shape(row, hidden_urls=panel.load_hidden())
        data["resume_preview"] = panel.read_text_sibling(row["tailored_resume_path"])[:4000]
        data["cover_preview"] = panel.read_text_sibling(row["cover_letter_path"])[:4000]
        return data

    @app.get("/api/jobs/handoffs")
    def handoffs():
        rows = _fetch_rows(
            "SELECT * FROM jobs WHERE apply_status='handoff' ORDER BY COALESCE(last_attempted_at,'') DESC"
        )
        hidden_urls = panel.load_hidden()
        return {"jobs": [_job_shape(r, hidden_urls=hidden_urls) for r in rows]}

    @app.post("/api/jobs/mark-applied")
    def api_mark_applied(body: UrlBody):
        panel.mark_applied(body.url)
        return {"ok": True}

    @app.post("/api/jobs/reset")
    def api_reset_job(body: UrlBody):
        panel.reset_job(body.url)
        return {"ok": True}

    @app.post("/api/jobs/hide")
    def hide_job(body: UrlBody):
        hidden = panel.load_hidden()
        hidden.add(body.url)
        panel.save_hidden(hidden)
        return {"ok": True}

    @app.post("/api/jobs/unhide")
    def unhide_job(body: UrlBody):
        hidden = panel.load_hidden()
        hidden.discard(body.url)
        panel.save_hidden(hidden)
        return {"ok": True}

    @app.get("/api/files/resume")
    def resume_file(url: str):
        return _pdf_response(url, "tailored_resume_path", "resume")

    @app.get("/api/files/cover")
    def cover_file(url: str):
        return _pdf_response(url, "cover_letter_path", "cover")

    @app.get("/api/stats")
    def stats():
        allrows = _fetch_rows("SELECT * FROM jobs")
        perday = Counter()
        for r in allrows:
            ts = r["applied_at"] or (r["last_attempted_at"] if r["apply_status"] == "handoff" else None)
            if r["apply_status"] in ("applied", "handoff") and ts:
                perday[ts[:10]] += 1

        status_counts = Counter(
            _status_label(r["apply_status"])
            for r in allrows
            if r["fit_score"] and r["fit_score"] >= 7
        )
        score_counts = Counter(
            r["fit_score"]
            for r in allrows
            if r["fit_score"] and r["site"] in ("linkedin", "indeed")
        )
        return {
            "funnel": panel.counts(),
            "per_day": [{"date": d, "applications": n} for d, n in sorted(perday.items())],
            "status_breakdown": [{"status": k, "count": v} for k, v in sorted(status_counts.items())],
            "score_distribution": [{"score": k, "count": v} for k, v in sorted(score_counts.items())],
            "spend": panel.llm_spend(),
        }

    @app.get("/api/run")
    def run_status():
        return _run_payload()

    @app.post("/api/run")
    def start_run(body: RunBody):
        active_file = panel.paths()["ACTIVE_FILE"]
        active = _read_active()
        if active and _pid_alive(active.get("pid")):
            raise HTTPException(status_code=409, detail="run already active")
        row = _get_job(body.url)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        if body.supervised is not None:
            panel.write_env({"APPLYPILOT_SUPERVISED": "1" if body.supervised else "0"})
        active_file.unlink(missing_ok=True)
        panel.launch_apply(body.url, row["company"] or "job", body.model)
        return _run_payload()

    @app.post("/api/run/stop")
    def stop_run():
        return {"stopped": panel.stop_run()}

    @app.post("/api/run/clear")
    def clear_run():
        panel.paths()["ACTIVE_FILE"].unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/run/log")
    def run_log():
        active = _read_active()
        if not active:
            raise HTTPException(status_code=404, detail="no active run")
        return {"lines": panel.tail(active.get("log"), 200)}

    @app.get("/api/run/log/stream")
    def run_log_stream():
        return StreamingResponse(_log_stream(), media_type="text/event-stream")

    @app.get("/api/settings")
    def settings():
        return _settings_payload()

    @app.put("/api/settings")
    def update_settings(body: SettingsUpdate):
        updates = {}
        if body.supervised is not None:
            updates["APPLYPILOT_SUPERVISED"] = "1" if body.supervised else "0"
        if body.fixed_resume is not None:
            updates["APPLYPILOT_FIXED_RESUME"] = "1" if body.fixed_resume else "0"
        if body.salary_mode is not None:
            updates["APPLYPILOT_SALARY_MODE"] = body.salary_mode
        if body.salary_fixed is not None:
            updates["APPLYPILOT_SALARY_FIXED"] = body.salary_fixed.strip()
        if updates:
            panel.write_env(updates)
        return _settings_payload()

    @app.get("/api/work-context")
    def work_context():
        return _work_context_payload()

    @app.put("/api/work-context")
    def update_work_context(body: WorkContextBody):
        profile = panel.load_profile()
        profile.setdefault("work_context", {})
        profile["work_context"]["projects"] = [
            p for p in body.projects if str(p.get("name", "")).strip()
        ]
        profile["work_context"]["llm_usage"] = body.llm_usage
        profile["work_context"]["answer_rules"] = body.answer_rules
        panel.save_profile(profile)
        return _work_context_payload()

    @app.post("/api/answers")
    async def answers(body: AnswerBody):
        if not body.question.strip():
            raise HTTPException(status_code=400, detail="question is required")
        try:
            answer = await run_in_threadpool(
                panel.gen_answer,
                panel.load_profile(),
                body.company,
                body.question,
                body.length,
                body.prev,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"answer": answer}

    _mount_frontend(app)
    return app


def _ensure_storage() -> None:
    paths = panel.paths()
    paths["APP"].mkdir(parents=True, exist_ok=True)
    paths["LOGDIR"].mkdir(parents=True, exist_ok=True)
    try:
        paths["APP"].chmod(0o700)
    except OSError:
        pass
    from applypilot.database import init_db
    init_db(db_path=paths["DB"])


def _parse_sources(sources: str | None) -> list[str]:
    if not sources:
        return []
    return [s.strip() for s in sources.split(",") if s.strip()]


def _fetch_rows(sql: str, params: list | tuple = ()) -> list[dict]:
    conn = panel.db()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _get_job(url: str) -> dict | None:
    rows = _fetch_rows("SELECT * FROM jobs WHERE url=?", (url,))
    return rows[0] if rows else None


def _queue_rows(
    *,
    min_score: int,
    sources: list[str],
    search: str | None,
    sort: str,
    show_applied: bool,
    only_docs_ready: bool,
    include_no_salary: bool,
    min_salary_k: int,
    hide_flagged: bool,
    include_hidden: bool,
) -> list[dict]:
    sql = "SELECT * FROM jobs WHERE fit_score >= ?"
    params: list = [min_score]
    if sources:
        sql += f" AND site IN ({','.join('?' for _ in sources)})"
        params += sources
    if not show_applied:
        sql += " AND (apply_status IS NULL OR apply_status IN ('failed', 'in_progress'))"
    if only_docs_ready:
        sql += " AND tailored_resume_path IS NOT NULL AND cover_letter_path IS NOT NULL"

    rows = _fetch_rows(sql, params)
    hidden_urls = panel.load_hidden()
    needle = search.lower() if search else ""
    filtered = []
    for r in rows:
        if not include_hidden and r["url"] in hidden_urls:
            continue
        if needle and needle not in ((r["company"] or "") + (r["title"] or "")).lower():
            continue
        sn = panel.salary_num(r["salary"])
        if not include_no_salary and sn == 0:
            continue
        if min_salary_k > 0 and sn > 0 and sn < min_salary_k * 1000:
            continue
        flags = panel.job_flags(r)
        if hide_flagged and flags:
            continue
        filtered.append(r)

    sort_key = {
        "score": lambda r: -(r["fit_score"] or 0),
        "salary": lambda r: -panel.salary_num(r["salary"]),
        "company": lambda r: (r["company"] or "").lower(),
        "location": lambda r: (r["location"] or "").lower(),
        "recent": lambda r: r["discovered_at"] or "",
    }[sort]
    filtered.sort(key=sort_key, reverse=(sort == "recent"))
    return filtered


def _job_shape(row: dict, hidden_urls: set | None = None) -> dict:
    hidden_urls = hidden_urls if hidden_urls is not None else panel.load_hidden()
    has_resume = bool(row["tailored_resume_path"])
    has_cover = bool(row["cover_letter_path"])
    return {
        "url": row["url"],
        "company": row["company"],
        "title": row["title"],
        "site": row["site"],
        "location": row["location"],
        "salary": row["salary"],
        "salary_num": panel.salary_num(row["salary"]),
        "fit_score": row["fit_score"],
        "apply_status": row["apply_status"],
        "flags": panel.job_flags(row),
        "docs_ready": has_resume and has_cover,
        "has_resume": has_resume,
        "has_cover": has_cover,
        "score_reasoning": row["score_reasoning"],
        "discovered_at": row["discovered_at"],
        "application_url": row["application_url"],
        "hidden": row["url"] in hidden_urls,
    }


def _pdf_response(url: str, path_field: str, kind: str) -> FileResponse:
    row = _get_job(url)
    if row is None or not row[path_field]:
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    pdf = Path(row[path_field]).with_suffix(".pdf")
    if not pdf.exists():
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    return FileResponse(
        pdf,
        media_type="application/pdf",
        filename=_clean_filename(row, kind),
    )


def _clean_filename(row: dict, kind: str) -> str:
    stem = "_".join(
        part
        for part in (
            _filename_part(row["company"]),
            _filename_part(row["title"]),
            kind,
        )
        if part
    )
    return f"{stem or kind}.pdf"


def _filename_part(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:80]


def _status_label(status: str | None) -> str:
    if status in panel.PILLS:
        return panel.PILLS[status][1]
    if not status:
        return panel.PILLS[None][1]
    return status


def _read_active() -> dict | None:
    active_file = panel.paths()["ACTIVE_FILE"]
    if not active_file.exists():
        return None
    try:
        return json.loads(active_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _run_payload() -> dict:
    active = _read_active()
    payload = {
        "active": False,
        "company": None,
        "url": None,
        "model": None,
        "started": None,
        "pid": None,
        "needs_you": False,
        "done": False,
        "status": None,
    }
    if not active:
        return payload

    log_text = panel.tail(active.get("log"))
    done = "Done:" in log_text
    needs_you = ("pinged you" in log_text or "NEEDHUMAN" in log_text) and not done
    row = _get_job(active.get("url", ""))
    payload.update({
        "active": True,
        "company": active.get("company"),
        "url": active.get("url"),
        "model": active.get("model"),
        "started": active.get("started"),
        "pid": active.get("pid"),
        "needs_you": needs_you,
        "done": done,
        "status": row["apply_status"] if row else None,
    })
    return payload


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


async def _log_stream():
    offset = 0
    active = _read_active()
    if not active:
        return
    log_path = Path(active.get("log", ""))
    try:
        offset = log_path.stat().st_size
    except OSError:
        offset = 0

    last_status = 0.0
    while True:
        active = _read_active()
        if not active:
            return
        log_path = Path(active.get("log", ""))
        try:
            size = log_path.stat().st_size
            if size < offset:
                offset = 0
            if size > offset:
                with log_path.open(errors="ignore") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
                for line in chunk.splitlines():
                    yield _sse("log", {"line": panel.redact_log_line(line)})
        except OSError:
            pass

        now = time.monotonic()
        if now - last_status >= 3:
            last_status = now
            yield _sse("status", _run_payload())
        await asyncio.sleep(1)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _settings_payload() -> dict:
    env = panel.read_env()
    app_dir = panel.paths()["APP"]
    return {
        "supervised": env.get("APPLYPILOT_SUPERVISED", "1") == "1",
        "fixed_resume": env.get("APPLYPILOT_FIXED_RESUME", "0") == "1",
        "salary_mode": env.get("APPLYPILOT_SALARY_MODE", "posting"),
        "salary_fixed": env.get("APPLYPILOT_SALARY_FIXED", ""),
        "llm_model": env.get("LLM_MODEL", ""),
        "telegram_connected": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
        "master_resume_exists": (app_dir / "master_resume.pdf").exists(),
    }


def _work_context_payload() -> dict:
    wc = panel.load_profile().get("work_context", {})
    return {
        "projects": wc.get("projects", []),
        "llm_usage": wc.get("llm_usage", ""),
        "answer_rules": wc.get("answer_rules", ""),
    }


def _mount_frontend(app: FastAPI) -> None:
    webdist = Path(__file__).parent / "webdist"
    if not webdist.exists():
        @app.get("/")
        def api_only():
            return {"status": "api-only", "hint": "web UI not built"}
        return

    assets = webdist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index():
        return FileResponse(webdist / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(webdist / "index.html")
