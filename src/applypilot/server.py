"""FastAPI backend for the ApplyPilot v2 web app."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
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


class RunIdBody(BaseModel):
    run_id: str


class ResumeSelectBody(BaseModel):
    id: str


class SettingsUpdate(BaseModel):
    supervised: bool | None = None
    fixed_resume: bool | None = None
    salary_mode: str | None = None
    salary_fixed: str | None = None
    ntfy_topic: str | None = None
    webhook_url: str | None = None
    discord_webhook_url: str | None = None
    slack_webhook_url: str | None = None
    macos_banner: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


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
    def resume_file(url: str, inline: bool = False):
        return _pdf_response(url, "tailored_resume_path", "resume", inline=inline)

    @app.get("/api/files/cover")
    def cover_file(url: str, inline: bool = False):
        return _pdf_response(url, "cover_letter_path", "cover", inline=inline)

    @app.get("/api/files/master")
    def master_file(inline: bool = False):
        pdf = panel.paths()["APP"] / "master_resume.pdf"
        if not pdf.exists():
            raise HTTPException(status_code=404, detail="master resume not found")
        return _file_response(pdf, "master_resume.pdf", inline=inline)

    @app.get("/api/resumes")
    def resumes():
        return _resumes_payload()

    @app.get("/api/resumes/file")
    def resume_library_file(id: str, inline: bool = False):
        path = _resolve_resume_id(id)
        return _file_response(path, path.name, inline=inline)

    @app.post("/api/resumes/upload")
    async def upload_resume(file: UploadFile = File(...)):
        data = await file.read()
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="file must be a PDF")
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="file is too large")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="file is not a valid PDF")
        path = _dedupe_resume_name(_sanitize_pdf_name(file.filename or "resume.pdf"))
        path.write_bytes(data)
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass
        return _resume_item(f"lib:{path.name}", path, "library")

    @app.post("/api/resumes/select")
    def select_resume(body: ResumeSelectBody):
        src = _resolve_resume_id(body.id)
        app_dir = panel.paths()["APP"]
        dst = app_dir / "master_resume.pdf"
        shutil.copyfile(src, dst)
        try:
            os.chmod(dst, 0o644)
        except OSError:
            pass
        _regenerate_master_text(dst)
        return _resumes_payload()

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

    @app.get("/api/runs")
    def runs_status():
        return _runs_payload()

    @app.post("/api/run")
    def start_run(body: RunBody):
        row = _get_job(body.url)
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        for run in panel.load_runs().values():
            if run.get("url") == body.url and panel._pid_alive(run.get("pid")):
                raise HTTPException(status_code=409, detail="job already running")
        slot = panel.alloc_slot(panel.max_gui_runs())
        if slot is None:
            raise HTTPException(status_code=409, detail="no free slot")
        if body.supervised is not None:
            panel.write_env({"APPLYPILOT_SUPERVISED": "1" if body.supervised else "0"})
        run_id = panel.launch_apply_slot(body.url, row["company"] or "job", body.model, slot)
        return _run_payload(run_id)

    @app.post("/api/run/stop")
    def stop_run(body: RunIdBody):
        if body.run_id not in panel.load_runs(prune=False):
            raise HTTPException(status_code=404, detail="run not found")
        return {"stopped": panel.stop_run_id(body.run_id)}

    @app.post("/api/run/stop-all")
    def stop_all_runs():
        stopped = panel.stop_run()
        panel.paths()["RUNS_FILE"].unlink(missing_ok=True)
        return {"stopped": stopped}

    @app.post("/api/run/clear")
    def clear_run(body: RunIdBody):
        runs = panel.load_runs(prune=False)
        run = runs.get(body.run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        if panel._pid_alive(run.get("pid")):
            raise HTTPException(status_code=409, detail="run is still active")
        runs.pop(body.run_id, None)
        panel._save_runs(runs)
        return {"ok": True}

    @app.get("/api/run/log")
    def run_log(run_id: str):
        run = panel.load_runs(prune=False).get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run not found")
        return {"lines": panel.tail(run.get("log"), 200)}

    @app.get("/api/run/log/stream")
    def run_log_stream(run_id: str):
        return StreamingResponse(_log_stream(run_id), media_type="text/event-stream")

    @app.get("/api/run")
    def run_status():
        for run_id, run in panel.load_runs().items():
            if panel._pid_alive(run.get("pid")):
                payload = _run_payload(run_id)
                payload["active"] = True
                return payload
        return {"active": False}

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
        if body.ntfy_topic is not None:
            updates["APPLYPILOT_NTFY_TOPIC"] = _settings_topic_value(body.ntfy_topic)
        if body.webhook_url is not None:
            updates["APPLYPILOT_WEBHOOK_URL"] = _settings_https_url_value(body.webhook_url)
        if body.discord_webhook_url is not None:
            updates["DISCORD_WEBHOOK_URL"] = _settings_https_url_value(body.discord_webhook_url)
        if body.slack_webhook_url is not None:
            updates["SLACK_WEBHOOK_URL"] = _settings_https_url_value(body.slack_webhook_url)
        if body.macos_banner is not None:
            updates["APPLYPILOT_MACOS_BANNER"] = "1" if body.macos_banner else "0"
        if body.telegram_bot_token is not None:
            updates["TELEGRAM_BOT_TOKEN"] = _settings_telegram_token_value(body.telegram_bot_token)
        if body.telegram_chat_id is not None:
            updates["TELEGRAM_CHAT_ID"] = _settings_telegram_chat_value(body.telegram_chat_id)
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


def _pdf_response(url: str, path_field: str, kind: str, inline: bool = False) -> FileResponse:
    row = _get_job(url)
    if row is None or not row[path_field]:
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    pdf = Path(row[path_field]).with_suffix(".pdf")
    if not pdf.exists():
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    return _file_response(pdf, _clean_filename(row, kind), inline=inline)


def _file_response(path: Path, filename: str, inline: bool = False) -> FileResponse:
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
    )


def _resumes_dir() -> Path:
    d = panel.paths()["APP"] / "resumes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resumes_payload() -> dict:
    app_dir = panel.paths()["APP"]
    master = app_dir / "master_resume.pdf"
    items = []
    for path in sorted(_resumes_dir().glob("*.pdf"), key=lambda p: p.name.lower()):
        if path.is_file():
            items.append(_resume_item(f"lib:{path.name}", path, "library", master=master))
    base = app_dir / "resume.pdf"
    if base.exists():
        items.append(_resume_item("base:resume.pdf", base, "base", master=master))
    if master.exists():
        items.append(_resume_item("master:master_resume.pdf", master, "master", master=master))
    return {"master_exists": master.exists(), "items": items}


def _resume_item(item_id: str, path: Path, kind: str, master: Path | None = None) -> dict:
    st = path.stat()
    master = master if master is not None else panel.paths()["APP"] / "master_resume.pdf"
    return {
        "id": item_id,
        "name": path.name,
        "kind": kind,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "is_master": _same_file(path, master) if master.exists() else False,
    }


def _same_file(path: Path, master: Path) -> bool:
    try:
        if path.resolve() == master.resolve():
            return True
        if path.stat().st_size != master.stat().st_size:
            return False
        return _sha256(path) == _sha256(master)
    except OSError:
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_resume_id(item_id: str) -> Path:
    token = unquote(item_id or "")
    if ":" not in token:
        raise HTTPException(status_code=400, detail="invalid resume id")
    kind, name = token.split(":", 1)
    app_dir = panel.paths()["APP"]
    if kind == "lib":
        if not name.lower().endswith(".pdf") or _unsafe_filename(name):
            raise HTTPException(status_code=400, detail="invalid resume id")
        root = _resumes_dir().resolve()
        path = (root / name).resolve()
        try:
            path.relative_to(root)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid resume id") from e
    elif kind == "base" and name == "resume.pdf":
        root = app_dir.resolve()
        path = (root / "resume.pdf").resolve()
    elif kind == "master" and name == "master_resume.pdf":
        root = app_dir.resolve()
        path = (root / "master_resume.pdf").resolve()
    else:
        raise HTTPException(status_code=400, detail="invalid resume id")
    try:
        path.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid resume id") from e
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="resume not found")
    return path


def _unsafe_filename(name: str) -> bool:
    return "/" in name or "\\" in name or ".." in name


def _sanitize_pdf_name(filename: str) -> str:
    name = Path(filename).name
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return f"{(stem or 'resume')[:120]}.pdf"


def _dedupe_resume_name(filename: str) -> Path:
    root = _resumes_dir()
    stem = filename[:-4]
    candidate = root / filename
    n = 1
    while candidate.exists():
        candidate = root / f"{stem}-{n}.pdf"
        n += 1
    return candidate


def _regenerate_master_text(pdf: Path) -> None:
    app_dir = panel.paths()["APP"]
    txt = app_dir / "master_resume.txt"
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        txt.write_text(text)
    except Exception:
        if txt.exists():
            return
        fallback = app_dir / "resume.txt"
        txt.write_text(fallback.read_text(errors="ignore") if fallback.exists() else "")


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


def _runs_payload() -> dict:
    max_slots = panel.max_gui_runs()
    runs = panel.load_runs()
    live_slots = {
        int(run.get("worker_slot", 0))
        for run in runs.values()
        if panel._pid_alive(run.get("pid"))
    }
    return {
        "slots": max_slots,
        "free": max_slots - len(live_slots),
        "runs": [
            _run_payload(run_id, run)
            for run_id, run in sorted(runs.items(), key=lambda item: item[1].get("started", ""))
        ],
    }


def _run_payload(run_id: str, run: dict | None = None) -> dict:
    if run is None:
        run = panel.load_runs(prune=False).get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    done, needs_you = _run_log_flags(run)
    row = _get_job(run.get("url", ""))
    return {
        "run_id": run_id,
        "url": run.get("url"),
        "company": run.get("company"),
        "model": run.get("model"),
        "started": run.get("started"),
        "worker_slot": int(run.get("worker_slot", 0)),
        "alive": panel._pid_alive(run.get("pid")),
        "needs_you": needs_you,
        "done": done,
        "status": row["apply_status"] if row else None,
    }


def _run_log_flags(run: dict) -> tuple[bool, bool]:
    log_text = panel.tail(run.get("log"))
    done = "Done:" in log_text
    needs_you = ("pinged you" in log_text or "NEEDHUMAN" in log_text) and not done
    return done, needs_you


async def _log_stream(run_id: str):
    offset = 0
    run = panel.load_runs(prune=False).get(run_id)
    if not run:
        return
    log_path = Path(run.get("log", ""))
    try:
        offset = log_path.stat().st_size
    except OSError:
        offset = 0

    last_status = 0.0
    while True:
        run = panel.load_runs(prune=False).get(run_id)
        if not run:
            return
        log_path = Path(run.get("log", ""))
        wrote_lines = False
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
                    wrote_lines = True
                    yield _sse("log", {"line": panel.redact_log_line(line)})
        except OSError:
            pass

        now = time.monotonic()
        if now - last_status >= 3:
            last_status = now
            yield _sse("status", _run_payload(run_id, run))
        if not wrote_lines and not panel._pid_alive(run.get("pid")):
            return
        await asyncio.sleep(1)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _settings_payload() -> dict:
    env = panel.read_env()
    app_dir = panel.paths()["APP"]
    platform_darwin = platform.system() == "Darwin"
    return {
        "supervised": env.get("APPLYPILOT_SUPERVISED", "1") == "1",
        "fixed_resume": env.get("APPLYPILOT_FIXED_RESUME", "0") == "1",
        "salary_mode": env.get("APPLYPILOT_SALARY_MODE", "posting"),
        "salary_fixed": env.get("APPLYPILOT_SALARY_FIXED", ""),
        "llm_model": env.get("LLM_MODEL", ""),
        "telegram_connected": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
        "ntfy_configured": bool(env.get("APPLYPILOT_NTFY_TOPIC")),
        "webhook_configured": bool(env.get("APPLYPILOT_WEBHOOK_URL")),
        "discord_configured": bool(env.get("DISCORD_WEBHOOK_URL")),
        "slack_configured": bool(env.get("SLACK_WEBHOOK_URL")),
        "platform_darwin": platform_darwin,
        "macos_banner": env.get("APPLYPILOT_MACOS_BANNER", "1") != "0",
        "master_resume_exists": (app_dir / "master_resume.pdf").exists(),
    }


def _settings_topic_value(value: str) -> str | None:
    value = value.strip()
    if value == "":
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise HTTPException(status_code=422, detail="ntfy topic must be 1-64 letters, numbers, underscores, or dashes")
    return value


def _settings_telegram_token_value(value: str) -> str | None:
    value = value.strip()
    if value == "":
        return None
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", value):
        raise HTTPException(status_code=422, detail="that doesn't look like a Telegram bot token (digits:secret)")
    return value


def _settings_telegram_chat_value(value: str) -> str | None:
    value = value.strip()
    if value == "":
        return None
    if not re.fullmatch(r"-?\d{1,20}", value):
        raise HTTPException(status_code=422, detail="Telegram chat id must be numeric")
    return value


def _settings_https_url_value(value: str) -> str | None:
    value = value.strip()
    if value == "":
        return None
    if not value.startswith("https://"):
        raise HTTPException(status_code=422, detail="webhook URL must start with https://")
    return value


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
