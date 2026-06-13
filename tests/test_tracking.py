"""Gmail tracking: prefilter, classifier parsing, matching, transitions, sync."""
import sqlite3

import pytest

from applypilot.tracking import classifier, sync


# ── prefilter ──────────────────────────────────────────────────────────────

FIXTURES = [
    # (email, expected_pass)
    ({"sender": "no-reply@us.greenhouse-mail.io", "subject": "Thank you for applying to Affirm",
      "snippet": "We received your application"}, True),
    ({"sender": "jobs-noreply@linkedin.com", "subject": "Your application was sent",
      "snippet": ""}, True),
    ({"sender": "recruiting@conti-federal.com", "subject": "Interview with Conti Federal",
      "snippet": "schedule a call"}, True),
    ({"sender": "noreply@goodtime.io", "subject": "Interview confirmed",
      "snippet": ""}, True),
    ({"sender": "leasing@entrata.com", "subject": "Your application payment",
      "snippet": "apartment application"}, False),
    ({"sender": "noreply@github.com", "subject": "A third-party OAuth application was added",
      "snippet": ""}, False),
    ({"sender": "deals@retailer.com", "subject": "50% off everything",
      "snippet": "sale ends tonight"}, False),
]


@pytest.mark.parametrize("email,expected", FIXTURES)
def test_prefilter_fixtures(email, expected):
    assert classifier.prefilter(email) is expected


def test_prefilter_known_company_passes():
    email = {"sender": "talent@obscurestartup.dev", "subject": "Obscure Startup — update",
             "snippet": "regarding your recent submission"}
    assert classifier.prefilter(email, known_companies={"obscure startup"}) is True


# ── classifier parsing ─────────────────────────────────────────────────────

def _emails(n):
    return [{"sender": f"s{i}@x.io", "subject": f"sub {i}", "snippet": ""} for i in range(n)]


def test_classify_batch_parses(monkeypatch):
    class Fake:
        def chat(self, messages, **kw):
            return ('[{"i":1,"company":"Affirm","role":"Analyst","event_type":"rejection","confidence":0.9},'
                    '{"i":2,"company":"X","role":"","event_type":"weird_type","confidence":2.5}]')

    monkeypatch.setattr(classifier, "get_client", lambda stage=None: Fake())
    out = classifier.classify_batch(_emails(2))
    assert out[0]["event_type"] == "rejection" and out[0]["confidence"] == 0.9
    assert out[1]["event_type"] == "other"  # unknown type degrades
    assert out[1]["confidence"] == 1.0  # clamped


def test_classify_batch_malformed_degrades(monkeypatch):
    class Fake:
        def chat(self, messages, **kw):
            return "I cannot help with that"

    monkeypatch.setattr(classifier, "get_client", lambda stage=None: Fake())
    out = classifier.classify_batch(_emails(2))
    assert all(c["event_type"] == "other" for c in out)


def test_classify_batch_llm_error_degrades(monkeypatch):
    class Boom:
        def chat(self, messages, **kw):
            raise RuntimeError("down")

    monkeypatch.setattr(classifier, "get_client", lambda stage=None: Boom())
    assert classifier.classify_batch(_emails(1))[0]["event_type"] == "other"


# ── matching + transitions ─────────────────────────────────────────────────

def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (url TEXT, company TEXT, apply_status TEXT, outcome TEXT, outcome_at TEXT, outcome_source TEXT)")
    conn.execute(
        "CREATE TABLE app_events (id INTEGER PRIMARY KEY, message_id TEXT UNIQUE,"
        " thread_id TEXT, job_url TEXT, company TEXT, role TEXT, event_type TEXT,"
        " confidence REAL, email_ts TEXT, subject TEXT, created_at TEXT)"
    )
    return conn


def test_match_job_fuzzy_and_ambiguous():
    conn = _conn()
    conn.execute("INSERT INTO jobs (url, company, apply_status, outcome) VALUES ('u1', 'Conti Federal Services, Inc.', 'applied', NULL)")
    conn.execute("INSERT INTO jobs (url, company, apply_status, outcome) VALUES ('u2', 'Affirm', 'handoff', NULL)")
    conn.execute("INSERT INTO jobs (url, company, apply_status, outcome) VALUES ('u3', 'Not Applied Co', NULL, NULL)")
    assert sync.match_job("Conti Federal", conn) == "u1"
    assert sync.match_job("Affirm Inc", conn) == "u2"
    assert sync.match_job("Not Applied Co", conn) is None  # not applied → no match
    conn.execute("INSERT INTO jobs (url, company, apply_status, outcome) VALUES ('u4', 'Affirm Holdings', 'applied', NULL)")
    assert sync.match_job("Affirm", conn) is None  # ambiguous → no auto-match


def test_plan_transition_forward_only_and_gated():
    assert sync.plan_transition(None, "screen_invite", 0.9) == "screen"
    assert sync.plan_transition(None, "screen_invite", 0.5) is None  # confidence gate
    assert sync.plan_transition("interview", "screen_invite", 0.9) is None  # no downgrade
    assert sync.plan_transition("interview", "rejection", 0.9) == "rejected"
    assert sync.plan_transition("rejected", "offer", 0.9) == "offer"  # offer beats rejected
    assert sync.plan_transition("offer", "rejection", 0.9) is None
    assert sync.plan_transition(None, "recruiter_inbound", 0.99) is None  # log-only event


# ── apply + dedupe ─────────────────────────────────────────────────────────

def test_apply_events_inserts_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    conn = _conn()
    conn.execute("INSERT INTO jobs (url, company, apply_status, outcome) VALUES ('u1', 'Affirm', 'applied', NULL)")
    emails = [{"message_id": "m1", "thread_id": "t1", "date": "", "subject": "re: app"}]
    cls = [{"company": "Affirm", "role": "Analyst", "event_type": "rejection", "confidence": 0.95}]

    r1 = sync._apply_events(conn, emails, cls)
    assert r1 == {"events": 1, "outcomes_set": 1}
    assert conn.execute("SELECT outcome FROM jobs WHERE url='u1'").fetchone()["outcome"] == "rejected"

    # exact rerun: UNIQUE(message_id) dedupes, nothing changes
    r2 = sync._apply_events(conn, emails, cls)
    assert r2 == {"events": 0, "outcomes_set": 0}


def test_apply_events_unmatched_company_logs_event_only(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    conn = _conn()
    emails = [{"message_id": "m2", "thread_id": None, "date": "", "subject": "s"}]
    cls = [{"company": "Mystery Corp", "role": "", "event_type": "interview_scheduled", "confidence": 0.9}]
    r = sync._apply_events(conn, emails, cls)
    assert r["events"] == 1 and r["outcomes_set"] == 0
    row = conn.execute("SELECT job_url FROM app_events").fetchone()
    assert row["job_url"] is None


# ── auto_sync gating ───────────────────────────────────────────────────────

def test_auto_sync_skips_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    from applypilot.tracking import auth
    monkeypatch.setattr(auth, "is_configured", lambda: False)
    assert sync.auto_sync() is None


def test_auto_sync_respects_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    from applypilot.tracking import auth
    from datetime import datetime, timezone
    monkeypatch.setattr(auth, "is_configured", lambda: True)
    sync._write_state({"last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    called = []
    monkeypatch.setattr(sync, "sync", lambda: called.append(1))
    assert sync.auto_sync(min_interval_s=1200) is None
    assert called == []


def test_seen_ledger_prevents_reclassifying_other(tmp_path, monkeypatch):
    """'other' emails must be recorded so the LLM never re-classifies them
    on the next sync (review finding #4)."""
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    conn = _conn()
    emails = [{"message_id": "mo", "thread_id": None, "date": "", "subject": "newsletter"}]
    cls = [{"company": "", "role": "", "event_type": "other", "confidence": 0.1}]
    sync._apply_events(conn, emails, cls)
    # no app_events row (non-actionable) but it IS in the seen-ledger
    assert conn.execute("SELECT COUNT(*) FROM app_events").fetchone()[0] == 0
    assert "mo" in sync._known_message_ids(conn)


def test_job_query_is_narrowed():
    q = sync._job_query("90d")
    assert "newer_than:90d" in q
    assert "-category:promotions" in q
    assert "interview" in q  # job-term narrowing present
