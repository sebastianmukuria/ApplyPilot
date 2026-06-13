"""Pipeline-from-app, autopilot, digest, and onboarding surfaces."""
import json
import sqlite3
import time
from datetime import datetime

import pytest

import applypilot.ops as ops


# ── ops state files ────────────────────────────────────────────────────────

def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    ops.write_state("x.json", {"a": 1})
    assert ops.read_state("x.json") == {"a": 1}
    assert ops.read_state("missing.json") == {}


# ── autopilot job selection ────────────────────────────────────────────────

def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE jobs (url TEXT, company TEXT, fit_score INT, "
        "tailored_resume_path TEXT, apply_status TEXT, apply_attempts INT)"
    )
    return conn


def _seed(conn, url, score, status=None, attempts=0, docs="/r.txt"):
    conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                 (url, "Co", score, docs, status, attempts))


def test_next_autopilot_job_ordering_and_exclusions():
    conn = _mem_conn()
    _seed(conn, "u9", 9)
    _seed(conn, "u10", 10)
    _seed(conn, "u10-applied", 10, status="applied")
    _seed(conn, "u10-tries", 10, attempts=5)
    _seed(conn, "u10-nodocs", 10, docs=None)
    assert ops.next_autopilot_job(conn, set())["url"] == "u10"
    assert ops.next_autopilot_job(conn, {"u10"})["url"] == "u9"
    assert ops.next_autopilot_job(conn, {"u10", "u9"}) is None


# ── digest ─────────────────────────────────────────────────────────────────

def test_compose_digest_lines():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE jobs (url TEXT, fit_score INT, apply_status TEXT, "
        "tailored_resume_path TEXT, cover_letter_path TEXT, "
        "discovered_at TEXT, applied_at TEXT)"
    )
    conn.execute("INSERT INTO jobs VALUES ('a', 9, NULL, '/r', '/c', datetime('now'), NULL)")
    conn.execute("INSERT INTO jobs VALUES ('b', 8, NULL, NULL, NULL, datetime('now','-3 day'), NULL)")
    text = ops.compose_digest(conn, None)
    assert "1 new jobs found" in text
    assert "2 strong matches" in text
    assert "1 fully prepped" in text


def test_maybe_send_digest_gates(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(ops, "compose_digest", lambda conn, since: "brief")
    import applypilot.notify as notify_mod
    monkeypatch.setattr(notify_mod, "notify", lambda event, text, **kw: sent.append(event))
    import applypilot.database as db_mod
    monkeypatch.setattr(db_mod, "get_connection", lambda: None)

    # off by default
    monkeypatch.delenv("APPLYPILOT_DIGEST_HOUR", raising=False)
    assert ops.maybe_send_digest(datetime(2026, 6, 13, 9)) is False

    monkeypatch.setenv("APPLYPILOT_DIGEST_HOUR", "8")
    # too early
    assert ops.maybe_send_digest(datetime(2026, 6, 13, 7)) is False
    # fires at/after the hour
    assert ops.maybe_send_digest(datetime(2026, 6, 13, 9)) is True
    assert sent == ["digest"]
    # only once per day
    assert ops.maybe_send_digest(datetime(2026, 6, 13, 10)) is False
    # next day fires again
    assert ops.maybe_send_digest(datetime(2026, 6, 14, 8)) is True


# ── endpoints ──────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    import applypilot.database as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db", raising=False)
    db.init_db(db_path=tmp_path / "test.db")
    from fastapi.testclient import TestClient
    from applypilot.server import create_app
    return TestClient(create_app()), tmp_path


def test_pipeline_endpoints(client, monkeypatch):
    c, _ = client
    assert c.post("/api/pipeline/run", json={"stages": ["bogus"]}).status_code == 422
    monkeypatch.setattr(ops, "start_pipeline", lambda *a, **k: False)
    assert c.post("/api/pipeline/run", json={"stages": ["all"]}).status_code == 409
    monkeypatch.setattr(ops, "start_pipeline", lambda *a, **k: True)
    assert c.post("/api/pipeline/run", json={"stages": ["score"]}).status_code == 200
    assert "running" in c.get("/api/pipeline/status").json()


def test_autopilot_endpoints(client, monkeypatch):
    c, _ = client
    assert c.post("/api/autopilot", json={"count": 99}).status_code == 422
    monkeypatch.setattr(ops, "start_autopilot", lambda *a, **k: False)
    assert c.post("/api/autopilot", json={"count": 5}).status_code == 409
    monkeypatch.setattr(ops, "start_autopilot", lambda *a, **k: True)
    assert c.post("/api/autopilot", json={"count": 5}).status_code == 200
    assert c.post("/api/autopilot/stop").status_code == 200


def test_onboarding_truth_table(client):
    c, app_dir = client
    ob = c.get("/api/onboarding").json()
    assert ob["resume_ready"] is False
    assert ob["profile_ready"] is False
    assert ob["searches_ready"] is False

    (app_dir / "resume.txt").write_text("text")
    c.put("/api/profile/personal", json={"full_name": "Ada L", "email": "a@b.c"})
    c.put("/api/searches", json={"titles": ["Data Analyst"], "locations": [], "remote": True})

    ob = c.get("/api/onboarding").json()
    assert ob["resume_ready"] and ob["profile_ready"] and ob["searches_ready"]


def test_profile_personal_creates_skeleton(client):
    c, app_dir = client
    r = c.put("/api/profile/personal", json={"full_name": "Ada L", "email": "a@b.c"})
    assert r.status_code == 200
    profile = json.loads((app_dir / "profile.json").read_text())
    # downstream code must never KeyError on a wizard-created profile
    for key in ("personal", "work_authorization", "compensation", "experience",
                "skills_boundary", "resume_facts", "eeo_voluntary", "screening"):
        assert key in profile
    assert profile["personal"]["full_name"] == "Ada L"


def test_searches_roundtrip_preserves_unmodeled_keys(client):
    c, app_dir = client
    (app_dir / "searches.yaml").write_text(
        "queries:\n- query: old\n  tier: 1\nboards: [indeed]\ncustom_key: keepme\n"
    )
    c.put("/api/searches", json={"titles": ["Analyst"], "locations": ["LA"], "remote": True})
    import yaml
    raw = yaml.safe_load((app_dir / "searches.yaml").read_text())
    assert raw["custom_key"] == "keepme"
    assert raw["boards"] == ["indeed"]
    assert raw["queries"] == [{"query": "Analyst", "tier": 2}]
    data = c.get("/api/searches").json()
    assert data["titles"] == ["Analyst"]
    assert data["locations"] == ["LA"]
    assert data["remote"] is True


def test_settings_digest_hour_and_gemini_key(client):
    c, app_dir = client
    assert c.put("/api/settings", json={"digest_hour": "99"}).status_code == 422
    assert c.put("/api/settings", json={"digest_hour": "8"}).status_code == 200
    assert c.get("/api/settings").json()["digest_hour"] == "8"
    # gemini key is write-only
    r = c.put("/api/settings", json={"gemini_api_key": "sk-test-123"})
    assert r.status_code == 200
    assert "sk-test-123" not in r.text
    assert "GEMINI_API_KEY=sk-test-123" in (app_dir / ".env").read_text()
    c.put("/api/settings", json={"gemini_api_key": ""})
    assert "GEMINI_API_KEY" not in (app_dir / ".env").read_text()


# ── regression: review-gauntlet fixes ──────────────────────────────────────

def test_start_pipeline_double_start_is_locked(tmp_path, monkeypatch):
    """TOCTOU: concurrent starts must not both spawn (review finding #2)."""
    import threading
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    barrier = threading.Barrier(8)
    started = []
    release = threading.Event()

    def fake_pipeline(**kw):
        started.append(1)
        release.wait(timeout=5)  # stay alive while all 8 contend for the lock
        return {}

    monkeypatch.setattr("applypilot.pipeline.run_pipeline", fake_pipeline)
    ops._pipeline_thread = None

    results = []

    def go():
        barrier.wait()
        results.append(ops.start_pipeline(["score"]))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1  # exactly one winner while a run is live
    release.set()
    time.sleep(0.1)
    assert sum(started) == 1  # only one pipeline actually ran


def test_start_pipeline_internal_typeerror_does_not_rerun(tmp_path, monkeypatch):
    """A TypeError from inside run_pipeline must surface, not trigger a 2nd run."""
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    ops._pipeline_thread = None
    calls = []

    def boom(**kw):
        calls.append(1)
        raise TypeError("internal bug, not a signature mismatch")

    monkeypatch.setattr("applypilot.pipeline.run_pipeline", boom)
    assert ops.start_pipeline(["score"]) is True
    ops._pipeline_thread.join(timeout=2)
    assert sum(calls) == 1  # ran once, not twice
    assert "internal bug" in (ops.read_state(ops.PIPELINE_STATE).get("error") or "")


def test_write_env_mirrors_into_os_environ(tmp_path, monkeypatch):
    """Settings changed in-app must reach in-process readers (review finding #3)."""
    import os as _os
    from applypilot import panel
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    panel.write_env({"APPLYPILOT_DIGEST_HOUR": "9"})
    assert _os.environ["APPLYPILOT_DIGEST_HOUR"] == "9"
    panel.write_env({"APPLYPILOT_DIGEST_HOUR": None})  # clear
    assert "APPLYPILOT_DIGEST_HOUR" not in _os.environ
