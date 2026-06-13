import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient


RESUME_TEXT = """Jane Doe
jane@example.com | 555-0100

SUMMARY
Backend engineer.

EXPERIENCE
Engineer at Alpha Co
Python | 2024
- Built reliable systems.
"""

COVER_TEXT = """June 13, 2026

Dear Hiring Manager,

I am interested in the role.

Sincerely,
Jane Doe
"""


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
    resume.write_text(RESUME_TEXT)
    cover.write_text(COVER_TEXT)
    resume.with_suffix(".pdf").write_bytes(b"%PDF-1.4\nresume\n")
    cover.with_suffix(".pdf").write_bytes(b"%PDF-1.4\ncover\n")

    rows = [
        ("https://jobs.test/score8-empty", "Alpha Co", "Senior Engineer", "linkedin", 8, "applied", None),
        ("https://jobs.test/score8-response", "Beta Co", "Backend Engineer", "linkedin", 8, "applied", "responded"),
        ("https://jobs.test/score9-interview", "Gamma Co", "Platform Engineer", "indeed", 9, "handoff", "interview"),
        ("https://jobs.test/score10-rejected", "Delta Co", "Staff Engineer", "indeed", 10, "applied", "rejected"),
        ("https://jobs.test/score7-offer", "Epsilon Co", "Product Engineer", "other", 7, "applied", "offer"),
    ]
    for url, company, title, site, score, status, outcome in rows:
        conn.execute(
            """
            INSERT INTO jobs (
                url, title, company, location, site, discovered_at, application_url,
                fit_score, score_reasoning, tailored_resume_path, cover_letter_path,
                apply_status, outcome, outcome_at, outcome_source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                url,
                title,
                company,
                "Remote",
                site,
                "2026-01-01T00:00:00",
                url + "/apply",
                score,
                "good fit",
                str(resume),
                str(cover),
                status,
                outcome,
                "2026-01-02T00:00:00" if outcome else None,
                "gmail" if outcome else None,
            ),
        )
    conn.commit()


def test_docx_file_endpoint_renders_on_demand_and_forces_attachment(api):
    client, app_dir, _ = api
    resume_docx = app_dir / "docs" / "resume.docx"
    cover_docx = app_dir / "docs" / "cover.docx"
    assert not resume_docx.exists()
    assert not cover_docx.exists()

    response = client.get(
        "/api/files/resume",
        params={"url": "https://jobs.test/score8-empty", "fmt": "docx", "inline": "1"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert response.headers["content-disposition"].startswith("attachment")
    assert resume_docx.exists()

    cover_response = client.get(
        "/api/files/cover",
        params={"url": "https://jobs.test/score8-empty", "fmt": "docx"},
    )
    assert cover_response.status_code == 200
    assert cover_docx.exists()


def test_outcome_post_round_trips_validates_and_clears(api):
    client, app_dir, _ = api
    url = "https://jobs.test/score8-empty"

    invalid = client.post("/api/jobs/outcome", json={"url": url, "outcome": "maybe"})
    assert invalid.status_code == 422

    response = client.post("/api/jobs/outcome", json={"url": url, "outcome": "screen"})
    assert response.status_code == 200
    assert response.json()["outcome"] == "screen"
    detail = client.get("/api/jobs/detail", params={"url": url}).json()
    assert detail["outcome"] == "screen"

    conn = sqlite3.connect(app_dir / "applypilot.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT outcome, outcome_at, outcome_source FROM jobs WHERE url=?", (url,)
    ).fetchone()
    event = conn.execute(
        "SELECT event_type, source FROM app_events WHERE job_url=? ORDER BY id DESC LIMIT 1",
        (url,),
    ).fetchone()
    assert row["outcome"] == "screen"
    assert row["outcome_at"]
    assert row["outcome_source"] == "manual"
    assert event["event_type"] == "screen"
    assert event["source"] == "manual"
    conn.close()

    cleared = client.post("/api/jobs/outcome", json={"url": url, "outcome": ""})
    assert cleared.status_code == 200
    assert cleared.json()["outcome"] is None
    detail = client.get("/api/jobs/detail", params={"url": url}).json()
    assert detail["outcome"] is None


def test_outcomes_summary_math(api):
    client, _, _ = api

    data = client.get("/api/outcomes/summary").json()
    assert data["funnel"] == {
        "applied": 5,
        "responded": 4,
        "screen": 2,
        "interview": 2,
        "offer": 1,
        "rejected": 1,
    }

    by_score = {row["band"]: row for row in data["by_score"]}
    assert by_score["7"] == {"band": "7", "applied": 1, "responded": 1, "response_rate": 1.0}
    assert by_score["8"] == {"band": "8", "applied": 2, "responded": 1, "response_rate": 0.5}
    assert by_score["9"] == {"band": "9", "applied": 1, "responded": 1, "response_rate": 1.0}
    assert by_score["10"] == {"band": "10", "applied": 1, "responded": 1, "response_rate": 1.0}

    by_source = {row["site"]: row for row in data["by_source"]}
    assert by_source["linkedin"] == {"site": "linkedin", "applied": 2, "responded": 1, "response_rate": 0.5}
    assert by_source["indeed"] == {"site": "indeed", "applied": 2, "responded": 2, "response_rate": 1.0}
    assert by_source["other"] == {"site": "other", "applied": 1, "responded": 1, "response_rate": 1.0}


def test_outcome_events_ordering_and_job_join(api):
    client, app_dir, _ = api
    conn = sqlite3.connect(app_dir / "applypilot.db")
    conn.execute(
        """
        INSERT INTO app_events (
            message_id, thread_id, job_url, company, role, event_type, source,
            confidence, email_ts, subject, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "m-old",
            "t-old",
            "https://jobs.test/score9-interview",
            "Fallback Co",
            "Fallback Role",
            "interview",
            "gmail",
            0.9,
            "2026-01-03T00:00:00",
            "Old subject",
            "2026-01-03T00:00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO app_events (
            message_id, thread_id, job_url, company, role, event_type, source,
            confidence, email_ts, subject, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "m-new",
            "t-new",
            None,
            "External Co",
            "External Role",
            "recruiter_inbound",
            "gmail",
            0.8,
            "2026-01-04T00:00:00",
            "New subject",
            "2026-01-04T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    events = client.get("/api/outcomes/events", params={"limit": 2}).json()["events"]
    assert [e["message_id"] for e in events] == ["m-new", "m-old"]
    assert events[0]["company"] == "External Co"
    assert events[0]["title"] == "External Role"
    assert events[1]["company"] == "Gamma Co"
    assert events[1]["title"] == "Platform Engineer"


def test_outcome_schema_migrates_old_database(tmp_path):
    import applypilot.database as db

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT, site TEXT)")
    old.commit()
    old.close()

    conn = db.init_db(db_path=path)
    job_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    event_cols = {r[1] for r in conn.execute("PRAGMA table_info(app_events)").fetchall()}

    assert {"outcome", "outcome_at", "outcome_source"} <= job_cols
    assert {
        "id",
        "message_id",
        "thread_id",
        "job_url",
        "company",
        "role",
        "event_type",
        "source",
        "confidence",
        "email_ts",
        "subject",
        "created_at",
    } <= event_cols
    db.close_connection(path)


def test_signals_feed_excludes_receipts_and_manual_churn(api):
    """The feed shows only meaningful signals; manual set/clear leaves at most
    one row and never 'applied'/'responded'/'cleared' noise."""
    client, _, _ = api
    url = "https://jobs.test/score8-empty"

    # manual 'responded' is a valid outcome but filtered from the feed as noise
    client.post("/api/jobs/outcome", json={"url": url, "outcome": "responded"})
    feed = client.get("/api/outcomes/events").json()
    events = feed["events"] if isinstance(feed, dict) else feed
    assert all(e["job_url"] != url for e in events)

    # a meaningful manual outcome DOES surface, and replaces the prior row
    client.post("/api/jobs/outcome", json={"url": url, "outcome": "interview"})
    feed = client.get("/api/outcomes/events").json()
    events = feed["events"] if isinstance(feed, dict) else feed
    mine = [e for e in events if e["job_url"] == url]
    assert len(mine) == 1 and mine[0]["event_type"] == "interview"

    # clearing removes the manual row entirely
    client.post("/api/jobs/outcome", json={"url": url, "outcome": ""})
    feed = client.get("/api/outcomes/events").json()
    events = feed["events"] if isinstance(feed, dict) else feed
    assert all(e["job_url"] != url for e in events)
