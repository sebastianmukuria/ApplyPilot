import importlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))

    import applypilot.panel as panel
    importlib.reload(panel)

    from applypilot.database import close_connection, init_db

    db_path = panel.paths()["DB"]
    conn = init_db(db_path=db_path)
    _seed_jobs(conn, tmp_path)

    import applypilot.server as server
    importlib.reload(server)

    with TestClient(server.create_app()) as client:
        yield client, tmp_path, panel

    close_connection(db_path)


def _seed_jobs(conn, app_dir):
    docs = app_dir / "docs"
    docs.mkdir()
    resume = docs / "resume.txt"
    cover = docs / "cover.txt"
    resume.write_text("resume preview")
    cover.write_text("cover preview")
    resume.with_suffix(".pdf").write_bytes(b"%PDF-1.4\nresume\n")
    cover.with_suffix(".pdf").write_bytes(b"%PDF-1.4\ncover\n")

    rows = [
        {
            "url": "https://jobs.test/strong",
            "company": "Alpha Co",
            "title": "Senior Platform Engineer",
            "salary": "$120,000 - $150,000/yr",
            "location": "Remote",
            "site": "linkedin",
            "fit_score": 10,
            "tailored_resume_path": str(resume),
            "cover_letter_path": str(cover),
            "apply_status": None,
            "discovered_at": "2026-01-10T00:00:00",
        },
        {
            "url": "https://jobs.test/failed",
            "company": "Beta Labs",
            "title": "Backend Engineer",
            "salary": None,
            "location": "New York",
            "site": "indeed",
            "fit_score": 9,
            "tailored_resume_path": str(resume),
            "cover_letter_path": None,
            "apply_status": "failed",
            "discovered_at": "2026-01-09T00:00:00",
        },
        {
            "url": "https://jobs.test/in-progress",
            "company": "Gamma Inc",
            "title": "Infrastructure Engineer",
            "salary": "$130,000/yr",
            "location": "Boston",
            "site": "linkedin",
            "fit_score": 8,
            "tailored_resume_path": str(resume),
            "cover_letter_path": str(cover),
            "apply_status": "in_progress",
            "discovered_at": "2026-01-08T00:00:00",
        },
        {
            "url": "https://jobs.test/applied",
            "company": "Delta Inc",
            "title": "Product Engineer",
            "salary": "$160,000/yr",
            "location": "Remote",
            "site": "indeed",
            "fit_score": 10,
            "tailored_resume_path": str(resume),
            "cover_letter_path": str(cover),
            "apply_status": "applied",
            "applied_at": "2026-01-03T12:00:00",
            "discovered_at": "2026-01-07T00:00:00",
        },
        {
            "url": "https://jobs.test/handoff",
            "company": "Epsilon Co",
            "title": "Staff Engineer",
            "salary": "$170,000/yr",
            "location": "Remote",
            "site": "linkedin",
            "fit_score": 9,
            "tailored_resume_path": str(resume),
            "cover_letter_path": str(cover),
            "apply_status": "handoff",
            "last_attempted_at": "2026-01-04T12:00:00",
            "discovered_at": "2026-01-06T00:00:00",
        },
        {
            "url": "https://jobs.test/low-score",
            "company": "Zeta Co",
            "title": "Junior Engineer",
            "salary": "$110,000/yr",
            "location": "Remote",
            "site": "linkedin",
            "fit_score": 5,
            "apply_status": None,
            "discovered_at": "2026-01-05T00:00:00",
        },
        {
            "url": "https://jobs.test/low-pay",
            "company": "Eta Co",
            "title": "Engineer",
            "salary": "$80,000 - $90,000/yr",
            "location": "Remote",
            "site": "other",
            "fit_score": 8,
            "apply_status": None,
            "discovered_at": "2026-01-04T00:00:00",
        },
        {
            "url": "https://jobs.test/staffing",
            "company": "Acme Staffing",
            "title": "Platform Engineer",
            "salary": "$150,000/yr",
            "location": "Remote",
            "site": "linkedin",
            "fit_score": 9,
            "tailored_resume_path": str(resume),
            "cover_letter_path": str(cover),
            "apply_status": None,
            "discovered_at": "2026-01-03T00:00:00",
        },
        {
            "url": "https://jobs.test/no-salary",
            "company": "Theta Inc",
            "title": "Data Engineer",
            "salary": None,
            "location": "Remote",
            "site": "indeed",
            "fit_score": 8,
            "apply_status": None,
            "discovered_at": "2026-01-02T00:00:00",
        },
    ]

    for r in rows:
        conn.execute(
            """
            INSERT INTO jobs (
                url, title, company, salary, location, site, discovered_at,
                application_url, fit_score, score_reasoning, tailored_resume_path,
                cover_letter_path, apply_status, applied_at, last_attempted_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                r["url"], r["title"], r["company"], r.get("salary"), r["location"], r["site"],
                r.get("discovered_at"), r["url"] + "/apply", r["fit_score"], "good fit",
                r.get("tailored_resume_path"), r.get("cover_letter_path"), r.get("apply_status"),
                r.get("applied_at"), r.get("last_attempted_at"),
            ),
        )
    conn.commit()


def _urls(data):
    return {j["url"] for j in data["jobs"]}


def _write_runs(panel, runs):
    path = panel.paths()["RUNS_FILE"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs))


def test_jobs_default_filters_statuses_and_min_score(api):
    client, _, _ = api
    data = client.get("/api/jobs").json()
    urls = _urls(data)

    assert "https://jobs.test/applied" not in urls
    assert "https://jobs.test/handoff" not in urls
    assert "https://jobs.test/failed" in urls
    assert "https://jobs.test/in-progress" in urls
    assert "https://jobs.test/low-score" not in urls
    assert all(j["fit_score"] >= 8 for j in data["jobs"])


def test_jobs_queue_filters(api):
    client, _, _ = api

    docs_ready = client.get("/api/jobs", params={"only_docs_ready": True, "hide_flagged": False}).json()
    assert docs_ready["jobs"]
    assert all(j["docs_ready"] for j in docs_ready["jobs"])

    with_salary = client.get("/api/jobs", params={"include_no_salary": False, "hide_flagged": False}).json()
    assert all(j["salary_num"] > 0 for j in with_salary["jobs"])

    salary_floor = client.get("/api/jobs", params={"min_salary_k": 140, "hide_flagged": False}).json()
    urls = _urls(salary_floor)
    assert "https://jobs.test/low-pay" not in urls
    assert "https://jobs.test/in-progress" not in urls
    assert "https://jobs.test/failed" in urls
    assert "https://jobs.test/no-salary" in urls

    default_urls = _urls(client.get("/api/jobs").json())
    assert "https://jobs.test/staffing" not in default_urls
    shown_flagged = _urls(client.get("/api/jobs", params={"hide_flagged": False}).json())
    assert "https://jobs.test/staffing" in shown_flagged

    title_match = client.get("/api/jobs", params={"search": "Platform", "hide_flagged": False}).json()
    assert {"https://jobs.test/strong", "https://jobs.test/staffing"} <= _urls(title_match)
    company_match = client.get("/api/jobs", params={"search": "Beta"}).json()
    assert "https://jobs.test/failed" in _urls(company_match)


def test_hide_and_unhide_job(api):
    client, _, _ = api
    url = "https://jobs.test/strong"

    assert url in _urls(client.get("/api/jobs").json())
    assert client.post("/api/jobs/hide", json={"url": url}).status_code == 200
    assert url not in _urls(client.get("/api/jobs").json())

    hidden = client.get("/api/jobs", params={"hidden": True}).json()
    assert url in _urls(hidden)
    assert next(j for j in hidden["jobs"] if j["url"] == url)["hidden"] is True

    assert client.post("/api/jobs/unhide", json={"url": url}).status_code == 200
    assert url in _urls(client.get("/api/jobs").json())


def test_mark_applied_sets_status_and_timestamp(api):
    client, app_dir, _ = api
    url = "https://jobs.test/strong"

    response = client.post("/api/jobs/mark-applied", json={"url": url})
    assert response.status_code == 200

    conn = sqlite3.connect(app_dir / "applypilot.db")
    status, applied_at = conn.execute(
        "SELECT apply_status, applied_at FROM jobs WHERE url=?", (url,)
    ).fetchone()
    conn.close()
    assert status == "applied"
    assert applied_at


def test_stats_and_spend(api):
    client, app_dir, panel = api
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": "gpt-4o-mini",
        "in": 1000,
        "out": 2000,
    }
    (app_dir / "llm_usage.jsonl").write_text(json.dumps(record) + "\n")

    data = client.get("/api/stats").json()
    assert data["funnel"] == {
        "total": 9,
        "scored": 9,
        "ge7": 8,
        "tailored": 6,
        "applied": 1,
        "handoff": 1,
        "failed": 1,
    }
    assert data["per_day"] == [
        {"date": "2026-01-03", "applications": 1},
        {"date": "2026-01-04", "applications": 1},
    ]
    expected = 1000 / 1e6 * panel.PRICES["gpt-4o-mini"][0] + 2000 / 1e6 * panel.PRICES["gpt-4o-mini"][1]
    assert data["spend"]["cost"] == pytest.approx(expected)
    assert data["spend"]["calls"] == 1


def test_run_slots_allocate_full_and_reuse_freed_slot(api, monkeypatch):
    client, _, panel = api
    monkeypatch.setenv("APPLYPILOT_MAX_RUNS", "3")

    live = set()
    pids_by_url = {}
    commands = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            self.pid = 100 + len(commands)
            commands.append(cmd)
            live.add(self.pid)

    monkeypatch.setattr(panel.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(panel, "_pid_alive", lambda pid: int(pid) in live)

    urls = [
        "https://jobs.test/strong",
        "https://jobs.test/failed",
        "https://jobs.test/in-progress",
    ]
    slots = []
    for url in urls:
        data = client.post("/api/run", json={"url": url, "model": "sonnet"}).json()
        slots.append(data["worker_slot"])
        pids_by_url[url] = 100 + len(commands) - 1

    assert slots == [0, 1, 2]
    assert all("--worker-slot" in cmd for cmd in commands)
    assert commands[1][commands[1].index("--worker-slot") + 1] == "1"
    assert client.get("/api/runs").json()["free"] == 0

    full = client.post("/api/run", json={"url": "https://jobs.test/no-salary"}).json()
    assert full["detail"] == "no free slot"

    live.remove(pids_by_url["https://jobs.test/failed"])
    reused = client.post("/api/run", json={"url": "https://jobs.test/no-salary"}).json()
    assert reused["worker_slot"] == 1


def test_run_rejects_duplicate_live_job(api, monkeypatch):
    client, _, panel = api
    live = {123}

    class FakePopen:
        pid = 123

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(panel.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(panel, "_pid_alive", lambda pid: int(pid) in live)

    assert client.post("/api/run", json={"url": "https://jobs.test/strong"}).status_code == 200
    response = client.post("/api/run", json={"url": "https://jobs.test/strong"})
    assert response.status_code == 409
    assert response.json()["detail"] == "job already running"


def test_targeted_stop_removes_one_registry_entry(api, monkeypatch):
    client, _, panel = api
    pids = iter([501, 502])

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = next(pids)

    monkeypatch.setattr(panel.subprocess, "Popen", FakePopen)
    run0 = panel.launch_apply_slot("https://jobs.test/strong", "Alpha Co", "sonnet", 0)
    run1 = panel.launch_apply_slot("https://jobs.test/failed", "Beta Labs", "sonnet", 1)

    killed_pids = []
    killed_slots = []
    monkeypatch.setattr(panel, "_terminate_process_group", lambda pid: killed_pids.append(pid) or True)
    monkeypatch.setattr(panel, "_kill_worker_port", lambda slot: killed_slots.append(slot))

    response = client.post("/api/run/stop", json={"run_id": run0})
    assert response.status_code == 200
    assert killed_pids == [501]
    assert killed_slots == [0]

    runs = panel.load_runs(prune=False)
    assert run0 not in runs
    assert run1 in runs


def test_clear_refuses_live_pid(api, monkeypatch):
    client, _, panel = api
    _write_runs(panel, {
        "20260613_101502_w0": {
            "url": "https://jobs.test/strong",
            "company": "Alpha Co",
            "model": "sonnet",
            "pid": 321,
            "worker_slot": 0,
            "log": "",
            "started": "20260613_101502",
        }
    })
    monkeypatch.setattr(panel, "_pid_alive", lambda pid: True)

    response = client.post("/api/run/clear", json={"run_id": "20260613_101502_w0"})
    assert response.status_code == 409
    assert response.json()["detail"] == "run is still active"


def test_legacy_run_alias_returns_first_alive_run(api, monkeypatch):
    client, app_dir, panel = api
    log = app_dir / "logs" / "run.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text("NEEDHUMAN pinged you\n")
    _write_runs(panel, {
        "20260613_101502_w0": {
            "url": "https://jobs.test/strong",
            "company": "Alpha Co",
            "model": "sonnet",
            "pid": 321,
            "worker_slot": 0,
            "log": str(log),
            "started": "20260613_101502",
        }
    })
    monkeypatch.setattr(panel, "_pid_alive", lambda pid: True)

    data = client.get("/api/run").json()
    assert data["active"] is True
    assert data["run_id"] == "20260613_101502_w0"
    assert data["needs_you"] is True


def test_run_registry_pruning_honors_grace_period(api, monkeypatch):
    _, _, panel = api
    _write_runs(panel, {
        "old": {"pid": 1, "started": "20260613_100502", "worker_slot": 0},
        "recent": {"pid": 2, "started": "20260613_100602", "worker_slot": 1},
        "live": {"pid": 3, "started": "20260613_090000", "worker_slot": 2},
    })
    monkeypatch.setattr(panel, "_pid_alive", lambda pid: int(pid) == 3)

    runs = panel.load_runs(now=datetime(2026, 6, 13, 10, 15, 2))
    assert set(runs) == {"recent", "live"}


def test_pdf_endpoints_support_inline_and_attachment(api):
    client, app_dir, _ = api
    (app_dir / "master_resume.pdf").write_bytes(b"%PDF-1.4\nmaster\n")

    resume_attachment = client.get(
        "/api/files/resume", params={"url": "https://jobs.test/strong"}
    )
    assert resume_attachment.status_code == 200
    assert resume_attachment.headers["content-disposition"].startswith("attachment")

    resume_inline = client.get(
        "/api/files/resume", params={"url": "https://jobs.test/strong", "inline": "1"}
    )
    cover_inline = client.get(
        "/api/files/cover", params={"url": "https://jobs.test/strong", "inline": "1"}
    )
    master_inline = client.get("/api/files/master", params={"inline": "1"})
    assert resume_inline.headers["content-disposition"].startswith("inline")
    assert cover_inline.headers["content-disposition"].startswith("inline")
    assert master_inline.headers["content-disposition"].startswith("inline")


def test_resume_library_lists_kinds_and_master_matches(api):
    client, app_dir, _ = api
    resumes = app_dir / "resumes"
    resumes.mkdir()
    (resumes / "library.pdf").write_bytes(b"%PDF-1.4\nsame\n")
    (app_dir / "resume.pdf").write_bytes(b"%PDF-1.4\nbase\n")
    (app_dir / "master_resume.pdf").write_bytes(b"%PDF-1.4\nsame\n")

    data = client.get("/api/resumes").json()
    assert data["master_exists"] is True
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id["lib:library.pdf"]["kind"] == "library"
    assert by_id["base:resume.pdf"]["kind"] == "base"
    # exactly ONE crown: the library item that IS the master wears it; the
    # redundant master_resume.pdf row is folded away
    assert by_id["lib:library.pdf"]["is_master"] is True
    assert "master:master_resume.pdf" not in by_id
    assert sum(1 for i in data["items"] if i["is_master"]) == 1


def test_resume_master_row_shown_when_unmatched(api):
    client, app_dir, _ = api
    (app_dir / "resumes").mkdir()
    (app_dir / "resumes" / "library.pdf").write_bytes(b"%PDF-1.4\nother\n")
    (app_dir / "master_resume.pdf").write_bytes(b"%PDF-1.4\ncli-set\n")

    data = client.get("/api/resumes").json()
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id["master:master_resume.pdf"]["is_master"] is True
    assert by_id["lib:library.pdf"]["is_master"] is False


def test_resume_id_path_traversal_is_rejected(api):
    client, app_dir, _ = api
    (app_dir / ".env").write_text("SECRET=do-not-return\n")

    for item_id in ("lib:../.env", "lib:..%2F.env"):
        response = client.get("/api/resumes/file", params={"id": item_id, "inline": "1"})
        assert response.status_code in (400, 404)
        assert b"do-not-return" not in response.content


def test_resume_upload_validation_and_dedupe(api):
    client, _, _ = api

    bad_ext = client.post(
        "/api/resumes/upload",
        files={"file": ("resume.txt", b"%PDF-1.4\n", "application/pdf")},
    )
    assert bad_ext.status_code == 400

    bad_magic = client.post(
        "/api/resumes/upload",
        files={"file": ("resume.pdf", b"not a pdf", "application/pdf")},
    )
    assert bad_magic.status_code == 400

    too_big = client.post(
        "/api/resumes/upload",
        files={"file": ("resume.pdf", b"%PDF-" + b"x" * (15 * 1024 * 1024), "application/pdf")},
    )
    assert too_big.status_code == 400

    first = client.post(
        "/api/resumes/upload",
        files={"file": ("My Resume!.pdf", b"%PDF-1.4\none\n", "application/pdf")},
    )
    second = client.post(
        "/api/resumes/upload",
        files={"file": ("My Resume!.pdf", b"%PDF-1.4\ntwo\n", "application/pdf")},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["name"] == "My_Resume.pdf"
    assert second.json()["name"] == "My_Resume-1.pdf"


def test_resume_select_copies_pdf_and_writes_extracted_text(api):
    client, app_dir, _ = api
    resumes = app_dir / "resumes"
    resumes.mkdir()
    pdf = resumes / "text.pdf"

    from pypdf import PdfWriter
    from pypdf._page import PageObject
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = PageObject.create_blank_page(width=200, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 120 Td (Hello ApplyPilot) Tj ET")
    page[NameObject("/Contents")] = stream
    writer.add_page(page)
    with pdf.open("wb") as fh:
        writer.write(fh)

    data = client.post("/api/resumes/select", json={"id": "lib:text.pdf"}).json()
    assert (app_dir / "master_resume.pdf").read_bytes() == pdf.read_bytes()
    assert "Hello ApplyPilot" in (app_dir / "master_resume.txt").read_text()
    assert data["master_exists"] is True


def test_settings_do_not_return_secrets_and_put_round_trips(api):
    client, app_dir, _ = api
    (app_dir / ".env").write_text(
        "\n".join([
            "TELEGRAM_BOT_TOKEN=secret-token",
            "TELEGRAM_CHAT_ID=123456",
            "LLM_MODEL=gpt-4o-mini",
            "APPLYPILOT_SUPERVISED=1",
            "APPLYPILOT_NTFY_TOPIC=secret-topic",
            "APPLYPILOT_WEBHOOK_URL=https://hooks.example/secret-generic",
            "DISCORD_WEBHOOK_URL=https://discord.example/secret-discord",
            "SLACK_WEBHOOK_URL=https://slack.example/secret-slack",
        ]) + "\n"
    )

    data = client.get("/api/settings").json()
    assert data["telegram_connected"] is True
    assert data["ntfy_configured"] is True
    assert data["webhook_configured"] is True
    assert data["discord_configured"] is True
    assert data["slack_configured"] is True
    assert isinstance(data["platform_darwin"], bool)
    assert data["macos_banner"] is True
    assert data["llm_model"] == "gpt-4o-mini"
    payload = json.dumps(data)
    for secret in [
        "secret-token",
        "123456",
        "secret-topic",
        "secret-generic",
        "secret-discord",
        "secret-slack",
        "hooks.example",
        "discord.example",
        "slack.example",
    ]:
        assert secret not in payload

    response = client.put(
        "/api/settings",
        json={
            "supervised": False,
            "salary_mode": "fixed",
            "salary_fixed": "150000",
            "ntfy_topic": "new-topic_1",
            "webhook_url": "https://hooks.example/new",
            "discord_webhook_url": "https://discord.example/new",
            "slack_webhook_url": "https://slack.example/new",
            "macos_banner": False,
        },
    )
    assert response.status_code == 200
    text = (app_dir / ".env").read_text()
    assert "APPLYPILOT_SUPERVISED=0" in text
    assert "APPLYPILOT_SALARY_MODE=fixed" in text
    assert "APPLYPILOT_SALARY_FIXED=150000" in text
    assert "APPLYPILOT_NTFY_TOPIC=new-topic_1" in text
    assert "APPLYPILOT_WEBHOOK_URL=https://hooks.example/new" in text
    assert "DISCORD_WEBHOOK_URL=https://discord.example/new" in text
    assert "SLACK_WEBHOOK_URL=https://slack.example/new" in text
    assert "APPLYPILOT_MACOS_BANNER=0" in text

    response = client.put(
        "/api/settings",
        json={
            "ntfy_topic": "",
            "webhook_url": "",
            "discord_webhook_url": "",
            "slack_webhook_url": "",
        },
    )
    assert response.status_code == 200
    text = (app_dir / ".env").read_text()
    assert "APPLYPILOT_NTFY_TOPIC=" not in text
    assert "APPLYPILOT_WEBHOOK_URL=" not in text
    assert "DISCORD_WEBHOOK_URL=" not in text
    assert "SLACK_WEBHOOK_URL=" not in text
    data = response.json()
    assert data["ntfy_configured"] is False
    assert data["webhook_configured"] is False
    assert data["discord_configured"] is False
    assert data["slack_configured"] is False
    assert data["macos_banner"] is False


@pytest.mark.parametrize("field", ["webhook_url", "discord_webhook_url", "slack_webhook_url"])
def test_settings_reject_invalid_notification_urls(api, field):
    client, _, _ = api
    response = client.put("/api/settings", json={field: "http://hooks.example/not-secure"})
    assert response.status_code == 422


def test_settings_reject_invalid_ntfy_topic(api):
    client, _, _ = api
    response = client.put("/api/settings", json={"ntfy_topic": "bad topic!"})
    assert response.status_code == 422


def test_answers_empty_question_is_400(api):
    client, _, _ = api
    response = client.post("/api/answers", json={"company": "Alpha", "question": " "})
    assert response.status_code == 400


def test_handoffs_returns_exact_handoff_rows(api):
    client, _, _ = api
    data = client.get("/api/jobs/handoffs").json()
    assert [j["url"] for j in data["jobs"]] == ["https://jobs.test/handoff"]


def test_sse_unknown_run_closes_promptly(api):
    client, _, _ = api
    with client.stream("GET", "/api/run/log/stream", params={"run_id": "missing"}) as response:
        assert response.status_code == 200
        assert list(response.iter_text()) == []


def test_settings_telegram_write_only_roundtrip(api):
    client, app_dir, _ = api
    # invalid token / chat id rejected
    assert client.put("/api/settings", json={"telegram_bot_token": "not-a-token"}).status_code == 422
    assert client.put("/api/settings", json={"telegram_chat_id": "abc"}).status_code == 422
    # valid pair round-trips into .env and flips the boolean
    r = client.put("/api/settings", json={
        "telegram_bot_token": "123456:" + "A" * 30,
        "telegram_chat_id": "987654321",
    })
    assert r.status_code == 200
    assert r.json()["telegram_connected"] is True
    env_text = (app_dir / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN=123456:" in env_text
    # the token value never appears in any GET payload
    payload = client.get("/api/settings").text
    assert "A" * 30 not in payload
    # empty string removes both
    client.put("/api/settings", json={"telegram_bot_token": "", "telegram_chat_id": ""})
    assert client.get("/api/settings").json()["telegram_connected"] is False
    assert "TELEGRAM_BOT_TOKEN" not in (app_dir / ".env").read_text()
