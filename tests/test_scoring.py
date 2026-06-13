"""F8: robust score parsing + never persist permanent zero on failure."""
import applypilot.database as db
import applypilot.scoring.scorer as scorer
from applypilot.scoring.scorer import _parse_score_response, run_scoring


def test_markdown_score_line():
    assert _parse_score_response("**SCORE:** 8\nKEYWORDS: a\nREASONING: b")["score"] == 8


def test_score_with_slash():
    assert _parse_score_response("Score: 7/10")["score"] == 7


def test_no_score_line_is_none():
    assert _parse_score_response("the model rambled with no verdict")["score"] is None


def test_unparseable_score_is_none():
    assert _parse_score_response("SCORE: N/A")["score"] is None


def test_plain_score():
    assert _parse_score_response("SCORE: 10")["score"] == 10


def _seed(conn, url):
    conn.execute(
        "INSERT INTO jobs (url, title, full_description) VALUES (?,?,?)",
        (url, "Engineer", "a long enough description " * 20),
    )
    conn.commit()


def test_failed_score_left_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(scorer, "RESUME_PATH", tmp_path / "resume.txt")
    (tmp_path / "resume.txt").write_text("my resume")
    conn = db.init_db()
    _seed(conn, "https://example.com/ok")
    _seed(conn, "https://example.com/fail")

    def fake_score(resume, job):
        if job["url"].endswith("fail"):
            return {"score": None, "keywords": "", "reasoning": "boom"}
        return {"score": 9, "keywords": "k", "reasoning": "r"}

    monkeypatch.setattr(scorer, "score_job", fake_score)
    out = run_scoring()

    assert out["scored"] == 1 and out["errors"] == 1
    ok = conn.execute("SELECT fit_score FROM jobs WHERE url=?",
                      ("https://example.com/ok",)).fetchone()
    bad = conn.execute("SELECT fit_score FROM jobs WHERE url=?",
                       ("https://example.com/fail",)).fetchone()
    assert ok["fit_score"] == 9          # committed
    assert bad["fit_score"] is None      # left pending, not a permanent 0


def test_pending_score_stage_still_includes_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(scorer, "RESUME_PATH", tmp_path / "resume.txt")
    (tmp_path / "resume.txt").write_text("my resume")
    conn = db.init_db()
    _seed(conn, "https://example.com/fail")
    monkeypatch.setattr(scorer, "score_job",
                        lambda r, j: {"score": None, "keywords": "", "reasoning": "x"})
    run_scoring()
    pending = db.get_jobs_by_stage(conn=conn, stage="pending_score", limit=0)
    assert any(j["url"] == "https://example.com/fail" for j in pending)


# ── Batched scoring ────────────────────────────────────────────────────────

def _batch_jobs(n):
    return [{"url": f"https://j.test/{i}", "title": f"Role {i}", "site": "indeed",
             "company": f"Co{i}", "location": "Remote", "full_description": "desc"}
            for i in range(1, n + 1)]


def test_score_jobs_batch_parses_full_response(monkeypatch):
    from applypilot.scoring import scorer

    class FakeClient:
        def chat(self, messages, **kw):
            return '[{"n":1,"score":8,"keywords":"sql","reasoning":"fit"},' \
                   '{"n":2,"score":5,"keywords":"","reasoning":"meh"}]'

    monkeypatch.setattr(scorer, "get_client", lambda stage=None: FakeClient())
    jobs = _batch_jobs(2)
    out = scorer.score_jobs_batch("resume", jobs)
    assert out[jobs[0]["url"]]["score"] == 8
    assert out[jobs[1]["url"]]["score"] == 5


def test_score_jobs_batch_partial_and_garbage_entries(monkeypatch):
    from applypilot.scoring import scorer

    class FakeClient:
        def chat(self, messages, **kw):
            # job 2 missing; one entry out of range; one malformed
            return 'noise [{"n":1,"score":9,"keywords":"","reasoning":"r"},' \
                   '{"n":7,"score":8},{"n":"x"}] trailing'

    monkeypatch.setattr(scorer, "get_client", lambda stage=None: FakeClient())
    jobs = _batch_jobs(2)
    out = scorer.score_jobs_batch("resume", jobs)
    assert jobs[0]["url"] in out and out[jobs[0]["url"]]["score"] == 9
    assert jobs[1]["url"] not in out  # caller falls back per-job


def test_score_jobs_batch_failure_returns_empty(monkeypatch):
    from applypilot.scoring import scorer

    class Boom:
        def chat(self, messages, **kw):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(scorer, "get_client", lambda stage=None: Boom())
    assert scorer.score_jobs_batch("resume", _batch_jobs(3)) == {}


def test_batch_size_env(monkeypatch):
    from applypilot.scoring import scorer
    monkeypatch.setenv("APPLYPILOT_SCORE_BATCH", "3")
    assert scorer._batch_size() == 3
    monkeypatch.setenv("APPLYPILOT_SCORE_BATCH", "junk")
    assert scorer._batch_size() == 8
    monkeypatch.setenv("APPLYPILOT_SCORE_BATCH", "0")
    assert scorer._batch_size() == 1
