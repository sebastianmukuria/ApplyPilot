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


def test_run_empty_and_stop_safe(api):
    client, _, _ = api
    data = client.get("/api/run").json()
    assert data["active"] is False
    assert client.post("/api/run/stop").json()["stopped"]


def test_settings_do_not_return_secrets_and_put_round_trips(api):
    client, app_dir, _ = api
    (app_dir / ".env").write_text(
        "\n".join([
            "TELEGRAM_BOT_TOKEN=secret-token",
            "TELEGRAM_CHAT_ID=123456",
            "LLM_MODEL=gpt-4o-mini",
            "APPLYPILOT_SUPERVISED=1",
        ]) + "\n"
    )

    data = client.get("/api/settings").json()
    assert data["telegram_connected"] is True
    assert data["llm_model"] == "gpt-4o-mini"
    payload = json.dumps(data)
    assert "secret-token" not in payload
    assert "123456" not in payload

    response = client.put(
        "/api/settings",
        json={"supervised": False, "salary_mode": "fixed", "salary_fixed": "150000"},
    )
    assert response.status_code == 200
    text = (app_dir / ".env").read_text()
    assert "APPLYPILOT_SUPERVISED=0" in text
    assert "APPLYPILOT_SALARY_MODE=fixed" in text
    assert "APPLYPILOT_SALARY_FIXED=150000" in text


def test_answers_empty_question_is_400(api):
    client, _, _ = api
    response = client.post("/api/answers", json={"company": "Alpha", "question": " "})
    assert response.status_code == 400


def test_handoffs_returns_exact_handoff_rows(api):
    client, _, _ = api
    data = client.get("/api/jobs/handoffs").json()
    assert [j["url"] for j in data["jobs"]] == ["https://jobs.test/handoff"]


def test_sse_without_active_file_closes_promptly(api):
    client, _, _ = api
    with client.stream("GET", "/api/run/log/stream") as response:
        assert response.status_code == 200
        assert list(response.iter_text()) == []
