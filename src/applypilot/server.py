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
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from applypilot.llm import ALLOWED_PROVIDERS, resolve_provider_name
from applypilot import ops, panel

VALID_OUTCOMES = {"responded", "screen", "interview", "offer", "rejected"}
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UrlBody(BaseModel):
    url: str


class OutcomeBody(BaseModel):
    url: str
    outcome: str = ""


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
    cover_provider: str | None = None
    ntfy_topic: str | None = None
    webhook_url: str | None = None
    discord_webhook_url: str | None = None
    slack_webhook_url: str | None = None
    macos_banner: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    digest_hour: str | None = None
    gemini_api_key: str | None = None


class PipelineRunBody(BaseModel):
    stages: list[str] = Field(default_factory=lambda: ["all"])
    min_score: int = 7
    workers: int = 2


class AutopilotBody(BaseModel):
    count: int = Field(5, ge=1, le=30)
    model: str = "sonnet"


class BackfillBody(BaseModel):
    days: int = Field(90, ge=1, le=365)


class PersonalBody(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    province_state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    website_url: str | None = None


class SearchesBody(BaseModel):
    titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote: bool = True


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

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app):
        task = asyncio.create_task(_background_ticker())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="ApplyPilot API", lifespan=_lifespan)
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

    @app.post("/api/jobs/outcome")
    def api_job_outcome(body: OutcomeBody):
        return _set_job_outcome(body.url, body.outcome)

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
    def resume_file(url: str, inline: bool = False, fmt: Literal["pdf", "docx"] = "pdf"):
        return _document_response(url, "tailored_resume_path", "resume", inline=inline, fmt=fmt)

    @app.get("/api/files/cover")
    def cover_file(url: str, inline: bool = False, fmt: Literal["pdf", "docx"] = "pdf"):
        return _document_response(url, "cover_letter_path", "cover", inline=inline, fmt=fmt)

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

    @app.get("/api/outcomes/summary")
    def outcomes_summary():
        return _outcomes_summary()

    @app.get("/api/outcomes/events")
    def outcome_events(limit: int = Query(30, ge=1, le=100)):
        return {"events": _outcome_events(limit)}

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
        if body.cover_provider is not None:
            provider = _settings_provider_value(body.cover_provider)
            updates["APPLYPILOT_COVER_PROVIDER"] = provider
            updates["APPLYPILOT_ANSWER_PROVIDER"] = provider
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
        if body.digest_hour is not None:
            updates["APPLYPILOT_DIGEST_HOUR"] = _settings_digest_hour_value(body.digest_hour)
        if body.gemini_api_key is not None:
            updates["GEMINI_API_KEY"] = body.gemini_api_key.strip() or None
        if updates:
            panel.write_env(updates)
        return _settings_payload()

    # ------------------------------------------------------------------
    # Pipeline runs / autopilot / onboarding (web-app operations)
    # ------------------------------------------------------------------

    @app.post("/api/pipeline/run")
    def pipeline_run(body: PipelineRunBody):
        from applypilot.pipeline import STAGE_ORDER
        valid = set(STAGE_ORDER) | {"all"}
        for s in body.stages:
            if s not in valid:
                raise HTTPException(status_code=422, detail=f"unknown stage: {s}")
        if not ops.start_pipeline(body.stages, min_score=body.min_score,
                                  workers=max(1, min(body.workers, 4))):
            raise HTTPException(status_code=409, detail="a pipeline run is already active")
        return ops.pipeline_status()

    @app.get("/api/pipeline/status")
    def pipeline_status():
        return ops.pipeline_status()

    @app.post("/api/autopilot")
    def autopilot_start(body: AutopilotBody):
        if not ops.start_autopilot(body.count, model=body.model):
            raise HTTPException(status_code=409, detail="autopilot is already running")
        return ops.autopilot_status()

    @app.post("/api/autopilot/stop")
    def autopilot_stop():
        ops.stop_autopilot()
        return ops.autopilot_status()

    @app.get("/api/autopilot/status")
    def autopilot_status():
        return ops.autopilot_status()

    @app.get("/api/tracking/status")
    def tracking_status():
        from applypilot.tracking import sync as tracking_sync
        return tracking_sync.status()

    @app.post("/api/tracking/sync")
    async def tracking_sync_now():
        from applypilot.tracking import auth as tracking_auth, sync as tracking_sync
        if tracking_sync.backfill_running():
            raise HTTPException(status_code=409, detail="backfill in progress")
        try:
            return await run_in_threadpool(tracking_sync.sync)
        except tracking_auth.TrackingNotConfigured as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.post("/api/tracking/backfill")
    def tracking_backfill(body: BackfillBody):
        from applypilot.tracking import auth as tracking_auth, sync as tracking_sync
        if not tracking_auth.is_configured():
            raise HTTPException(status_code=409, detail="tracking not connected")
        if not tracking_sync.start_backfill(days=body.days):
            raise HTTPException(status_code=409, detail="backfill already running")
        return JSONResponse(tracking_sync.status(), status_code=202)

    @app.get("/api/onboarding")
    def onboarding():
        return _onboarding_payload()

    @app.get("/api/profile/personal")
    def get_personal():
        profile = panel.load_profile()
        personal = profile.get("personal", {}) if isinstance(profile, dict) else {}
        keys = ("full_name", "email", "phone", "city", "province_state",
                "country", "linkedin_url", "github_url", "website_url")
        return {k: personal.get(k, "") for k in keys}

    @app.put("/api/profile/personal")
    def put_personal(body: PersonalBody):
        profile = panel.load_profile() or {}
        _ensure_profile_skeleton(profile)
        for k, v in body.model_dump(exclude_none=True).items():
            profile["personal"][k] = v.strip()
        panel.save_profile(profile)
        return get_personal()

    @app.get("/api/searches")
    def get_searches():
        return _searches_payload()

    @app.put("/api/searches")
    def put_searches(body: SearchesBody):
        _write_searches(body)
        return _searches_payload()

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


def _set_job_outcome(url: str, raw_outcome: str) -> dict:
    row = _get_job(url)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")

    outcome = (raw_outcome or "").strip().lower()
    if outcome and outcome not in VALID_OUTCOMES:
        allowed = ", ".join(sorted(VALID_OUTCOMES))
        raise HTTPException(status_code=422, detail=f"outcome must be one of: {allowed}")

    now = datetime.now(timezone.utc).isoformat()
    conn = panel.db()
    try:
        if outcome:
            conn.execute(
                "UPDATE jobs SET outcome=?, outcome_at=?, outcome_source='manual' WHERE url=?",
                (outcome, now, url),
            )
            event_type = outcome
            subject = f"Manual outcome: {outcome}"
            payload = {"outcome": outcome, "outcome_at": now, "outcome_source": "manual"}
        else:
            conn.execute(
                "UPDATE jobs SET outcome=NULL, outcome_at=NULL, outcome_source=NULL WHERE url=?",
                (url,),
            )
            event_type = "cleared"
            subject = "Manual outcome cleared"
            payload = {"outcome": None, "outcome_at": None, "outcome_source": None}

        conn.execute(
            """
            INSERT INTO app_events (
                message_id, thread_id, job_url, company, role, event_type, source,
                confidence, email_ts, subject, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                None,
                None,
                url,
                row.get("company"),
                row.get("title"),
                event_type,
                "manual",
                1.0,
                now,
                subject,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, **payload}


def _outcomes_summary() -> dict:
    conn = panel.db()
    try:
        rows = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN apply_status IN ('applied','handoff') THEN 1 ELSE 0 END) AS applied,
                SUM(CASE WHEN outcome IS NOT NULL AND outcome <> '' THEN 1 ELSE 0 END) AS responded,
                SUM(CASE WHEN outcome IN ('screen','interview','offer') THEN 1 ELSE 0 END) AS screen,
                SUM(CASE WHEN outcome IN ('interview','offer') THEN 1 ELSE 0 END) AS interview,
                SUM(CASE WHEN outcome = 'offer' THEN 1 ELSE 0 END) AS offer,
                SUM(CASE WHEN outcome = 'rejected' THEN 1 ELSE 0 END) AS rejected
            FROM jobs
            """
        ).fetchone()
        funnel = {
            "applied": rows["applied"] or 0,
            "responded": rows["responded"] or 0,
            "screen": rows["screen"] or 0,
            "interview": rows["interview"] or 0,
            "offer": rows["offer"] or 0,
            "rejected": rows["rejected"] or 0,
        }

        by_score = []
        for band in range(7, 11):
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN apply_status IN ('applied','handoff') THEN 1 ELSE 0 END) AS applied,
                    SUM(CASE WHEN outcome IS NOT NULL AND outcome <> '' THEN 1 ELSE 0 END) AS responded
                FROM jobs
                WHERE fit_score = ?
                """,
                (band,),
            ).fetchone()
            applied = row["applied"] or 0
            responded = row["responded"] or 0
            by_score.append({
                "band": str(band),
                "applied": applied,
                "responded": responded,
                "response_rate": _response_rate(responded, applied),
            })

        source_rows = conn.execute(
            """
            SELECT
                COALESCE(site, '') AS site,
                SUM(CASE WHEN apply_status IN ('applied','handoff') THEN 1 ELSE 0 END) AS applied,
                SUM(CASE WHEN outcome IS NOT NULL AND outcome <> '' THEN 1 ELSE 0 END) AS responded
            FROM jobs
            GROUP BY COALESCE(site, '')
            ORDER BY COALESCE(site, '')
            """
        ).fetchall()
        by_source = []
        for row in source_rows:
            applied = row["applied"] or 0
            responded = row["responded"] or 0
            if applied == 0 and responded == 0:
                continue
            by_source.append({
                "site": row["site"] or "unknown",
                "applied": applied,
                "responded": responded,
                "response_rate": _response_rate(responded, applied),
            })
    finally:
        conn.close()

    return {"funnel": funnel, "by_score": by_score, "by_source": by_source}


def _outcome_events(limit: int) -> list[dict]:
    rows = _fetch_rows(
        """
        SELECT
            e.id, e.message_id, e.thread_id, e.job_url,
            e.company AS event_company, e.role, e.event_type, e.source,
            e.confidence, e.email_ts, e.subject, e.created_at,
            j.company AS job_company, j.title AS job_title
        FROM app_events e
        LEFT JOIN jobs j ON e.job_url = j.url
        ORDER BY COALESCE(e.created_at, '') DESC, e.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "message_id": row["message_id"],
            "thread_id": row["thread_id"],
            "job_url": row["job_url"],
            "company": row["job_company"] or row["event_company"],
            "job_company": row["job_company"],
            "role": row["role"],
            "title": row["job_title"] or row["role"],
            "job_title": row["job_title"],
            "event_type": row["event_type"],
            "source": row.get("source"),
            "confidence": row["confidence"],
            "email_ts": row["email_ts"],
            "subject": row["subject"],
            "created_at": row["created_at"],
        })
    return events


def _response_rate(responded: int, applied: int) -> float:
    if applied <= 0:
        return 0.0
    return responded / applied


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
        "outcome": row.get("outcome"),
        "outcome_at": row.get("outcome_at"),
        "outcome_source": row.get("outcome_source"),
        "flags": panel.job_flags(row),
        "docs_ready": has_resume and has_cover,
        "has_resume": has_resume,
        "has_cover": has_cover,
        "score_reasoning": row["score_reasoning"],
        "discovered_at": row["discovered_at"],
        "application_url": row["application_url"],
        "hidden": row["url"] in hidden_urls,
    }


def _document_response(
    url: str,
    path_field: str,
    kind: str,
    *,
    inline: bool = False,
    fmt: Literal["pdf", "docx"] = "pdf",
) -> FileResponse:
    row = _get_job(url)
    if row is None or not row[path_field]:
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    if fmt == "docx":
        docx = _ensure_docx(row, path_field, kind)
        return _file_response(
            docx,
            _clean_filename(row, kind, ext=".docx"),
            inline=False,
            media_type=DOCX_MEDIA_TYPE,
        )
    pdf = Path(row[path_field]).with_suffix(".pdf")
    if not pdf.exists():
        raise HTTPException(status_code=404, detail=f"{kind} not found")
    return _file_response(pdf, _clean_filename(row, kind), inline=inline)


def _ensure_docx(row: dict, path_field: str, kind: str) -> Path:
    base = Path(row[path_field])
    docx = base.with_suffix(".docx")
    if docx.exists():
        return docx

    txt = base if base.suffix.lower() == ".txt" else base.with_suffix(".txt")
    if not txt.exists():
        raise HTTPException(status_code=404, detail=f"{kind} docx source not found")
    text = txt.read_text(encoding="utf-8")
    try:
        from applypilot.scoring.docx_render import cover_to_docx, resume_to_docx

        if kind == "cover":
            return cover_to_docx(text, docx)
        return resume_to_docx(text, docx)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{kind} docx generation failed") from e


def _file_response(
    path: Path,
    filename: str,
    inline: bool = False,
    media_type: str = "application/pdf",
) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
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
    # master_resume.pdf is a copy destination, not a library member: list it
    # only when no listed item already IS the master (e.g. it was set from the
    # CLI before the library existed) — otherwise two rows would wear the crown.
    if master.exists() and not any(i["is_master"] for i in items):
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


def _clean_filename(row: dict, kind: str, ext: str = ".pdf") -> str:
    stem = "_".join(
        part
        for part in (
            _filename_part(row["company"]),
            _filename_part(row["title"]),
            kind,
        )
        if part
    )
    return f"{stem or kind}{ext}"


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




async def _background_ticker(interval_s: float = 600) -> None:
    """Every 10 minutes: maybe send the daily digest; sync Gmail tracking when
    configured. Each tick is best-effort — a failure never kills the loop."""
    while True:
        try:
            await run_in_threadpool(ops.maybe_send_digest)
        except Exception:  # noqa: BLE001
            pass
        try:
            from applypilot.tracking import sync as _tracking_sync  # type: ignore

            await run_in_threadpool(_tracking_sync.auto_sync)
        except Exception:  # noqa: BLE001 — tracking optional/not configured
            pass
        await asyncio.sleep(interval_s)


def _settings_digest_hour_value(value: str) -> str | None:
    value = value.strip()
    if value == "":
        return None
    if not value.isdigit() or not (0 <= int(value) <= 23):
        raise HTTPException(status_code=422, detail="digest hour must be 0-23 or empty")
    return value


def _ensure_profile_skeleton(profile: dict) -> None:
    """Give a fresh profile every top-level key downstream code touches."""
    profile.setdefault("personal", {})
    profile.setdefault("work_authorization",
                       {"legally_authorized_to_work": "Yes", "require_sponsorship": "No"})
    profile.setdefault("availability", {})
    profile.setdefault("compensation", {"salary_expectation": "0"})
    profile.setdefault("experience", {})
    profile.setdefault("skills_boundary", {})
    profile.setdefault("resume_facts", {})
    profile.setdefault("eeo_voluntary", {})
    profile.setdefault("screening", {})


def _searches_path() -> Path:
    return panel.paths()["APP"] / "searches.yaml"


def _load_searches_raw() -> dict:
    import yaml
    try:
        data = yaml.safe_load(_searches_path().read_text()) or {}
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}


def _searches_payload() -> dict:
    raw = _load_searches_raw()
    titles = [q.get("query", "") for q in raw.get("queries", []) if isinstance(q, dict)]
    locs = [l.get("location", "") for l in raw.get("locations", [])
            if isinstance(l, dict) and not l.get("remote")]
    remote = any(isinstance(l, dict) and l.get("remote") for l in raw.get("locations", []))
    return {"titles": [s for s in titles if s], "locations": [s for s in locs if s],
            "remote": remote, "exists": _searches_path().exists()}


def _write_searches(body) -> None:
    """Map the simple wizard model onto searches.yaml, preserving unmodeled keys."""
    import yaml
    raw = _load_searches_raw()
    raw["queries"] = [{"query": s, "tier": 2} for s in body.titles]
    locations = [{"location": s, "remote": False} for s in body.locations]
    if body.remote:
        locations.append({"location": "Remote", "remote": True})
    raw["locations"] = locations or [{"location": "Remote", "remote": True}]
    raw.setdefault("location", {"accept_patterns": (["Remote"] if body.remote else []) + body.locations,
                                "reject_patterns": []})
    path = _searches_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))


def _onboarding_payload() -> dict:
    app_dir = panel.paths()["APP"]
    profile = panel.load_profile()
    personal = profile.get("personal", {}) if isinstance(profile, dict) else {}
    searches = _searches_payload()
    try:
        jobs_discovered = _fetch_rows("SELECT COUNT(*) AS n FROM jobs")[0]["n"]
    except Exception:  # noqa: BLE001 — fresh machine, no DB yet
        jobs_discovered = 0
    env = panel.read_env()
    return {
        "resume_ready": (app_dir / "master_resume.pdf").exists() or (app_dir / "resume.txt").exists(),
        "profile_ready": bool(personal.get("full_name") and personal.get("email")),
        "searches_ready": searches["exists"] and bool(searches["titles"]),
        "claude_cli": shutil.which("claude") is not None,
        "api_key_present": bool(env.get("GEMINI_API_KEY") or env.get("OPENAI_API_KEY")),
        "telegram_configured": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
        "tracking_configured": (app_dir / "google_token.json").exists(),
        "jobs_discovered": jobs_discovered,
    }


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
        "cover_provider": resolve_provider_name("cover", env=env, default="local"),
        "claude_cli_available": shutil.which("claude") is not None,
        "telegram_connected": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
        "ntfy_configured": bool(env.get("APPLYPILOT_NTFY_TOPIC")),
        "webhook_configured": bool(env.get("APPLYPILOT_WEBHOOK_URL")),
        "discord_configured": bool(env.get("DISCORD_WEBHOOK_URL")),
        "slack_configured": bool(env.get("SLACK_WEBHOOK_URL")),
        "platform_darwin": platform_darwin,
        "macos_banner": env.get("APPLYPILOT_MACOS_BANNER", "1") != "0",
        "digest_hour": env.get("APPLYPILOT_DIGEST_HOUR", ""),
        "master_resume_exists": (app_dir / "master_resume.pdf").exists(),
    }


def _settings_provider_value(value: str) -> str | None:
    value = value.strip().lower()
    if value == "":
        return None
    if value not in ALLOWED_PROVIDERS:
        allowed = ", ".join(ALLOWED_PROVIDERS)
        raise HTTPException(status_code=422, detail=f"cover_provider must be one of: {allowed}")
    return value


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
