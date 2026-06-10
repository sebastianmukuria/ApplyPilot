"""F4: apply --url must select fresh (NULL) jobs and never re-apply."""
import applypilot.database as db
from applypilot.apply.launcher import acquire_job


def _seed(conn, url, status, applied_at=None):
    conn.execute(
        "INSERT INTO jobs (url, title, site, tailored_resume_path, fit_score, "
        "apply_status, applied_at) VALUES (?,?,?,?,?,?,?)",
        (url, "Engineer", "linkedin", "/tmp/r.txt", 8, status, applied_at),
    )
    conn.commit()


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    conn = db.init_db()
    return conn


def test_fresh_null_job_is_selected(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    url = "https://example.com/job-null"
    _seed(conn, url, None)
    assert acquire_job(target_url=url) is not None


def test_failed_job_is_selected(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    url = "https://example.com/job-failed"
    _seed(conn, url, "failed")
    assert acquire_job(target_url=url) is not None


def test_applied_job_is_not_reselected(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    url = "https://example.com/job-applied"
    _seed(conn, url, "applied", applied_at="2026-01-01T00:00:00Z")
    assert acquire_job(target_url=url) is None


def test_target_url_query_string_is_job_identity(tmp_path, monkeypatch):
    """The ?jk= query string IS the job on indeed -- targeting one indeed URL
    must never select a different indeed job (regression: the old LIKE
    pattern stripped the query string and matched every job on the board)."""
    conn = _setup(tmp_path, monkeypatch)
    _seed(conn, "https://www.indeed.com/viewjob?jk=aaaa1111", None)
    _seed(conn, "https://www.indeed.com/viewjob?jk=bbbb2222", None)
    job = acquire_job(target_url="https://www.indeed.com/viewjob?jk=bbbb2222")
    assert job is not None
    assert job["url"].endswith("jk=bbbb2222")


def test_target_url_unknown_job_returns_none(tmp_path, monkeypatch):
    """A target URL that matches nothing must not fall back to another job."""
    conn = _setup(tmp_path, monkeypatch)
    _seed(conn, "https://www.indeed.com/viewjob?jk=aaaa1111", None)
    assert acquire_job(target_url="https://www.indeed.com/viewjob?jk=zzzz9999") is None
