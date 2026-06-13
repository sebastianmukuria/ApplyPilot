"""Two-pass email classifier for the job pipeline (ported from daily-os-bot).

1. prefilter(email) — cheap and deterministic: is this email job-related at
   all? GENEROUS by design — a false negative silently drops a real signal,
   while a false positive just gets classified as 'other' by the LLM.
2. classify_batch(emails) — one LLM call for up to BATCH emails, strict JSON
   array out. Anything unparseable degrades to event_type 'other'.
"""

from __future__ import annotations

import json
import logging
import re

from applypilot.llm import get_client

logger = logging.getLogger(__name__)

BATCH = 15

EVENT_TYPES = [
    "applied", "screen_invite", "interview_scheduled", "reschedule",
    "rejection", "offer", "recruiter_inbound", "other",
]

# --- Battle-tested prefilter knowledge (from the user's real inbox) ---------
NEGATIVE_DOMAINS = {"entrata.com", "github.com"}
NON_JOB_SUBJECT_HINTS = [
    "application payment", "oauth application", "third-party application",
]
ATS_DOMAINS = {
    "greenhouse-mail.io", "us.greenhouse-mail.io", "myworkday.com",
    "jobs.netflix.com", "jobs.lever.co", "jobs.ashbyhq.com", "icims.com",
    "smartrecruiters.com", "saashr.com", "successfactors.com",
    "jobvite.com", "bamboohr.com", "breezy.hr",
}
KNOWN_JOB_SENDERS = {
    "jobs-noreply@linkedin.com", "indeedapply@indeed.com",
    "donotreply@indeed.com",
}
INTERVIEW_DOMAINS = {
    "interviewplanner.com", "metaview.ai", "goodtime.io", "calendly.com",
}
RECRUITING_PHRASES = [
    "applying to", "for applying", "thanks for applying", "thank you for applying",
    "your application", "received your application", "received your resume",
    "we received your", "we've received your", "application received",
    "your candidacy", "move forward with", "next steps", "phone screen",
    "schedule a call", "schedule an interview", "interview with",
    "unfortunately", "not moving forward", "other candidates", "position has been filled",
    "your interest in", "talent acquisition", "recruiting team", "recruiter",
    "offer letter", "pleased to offer",
]


def _domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender or "")
    return m.group(1).lower() if m else ""


def prefilter(email: dict, known_companies: set[str] | None = None) -> bool:
    """True when an email is plausibly job-related.

    email: {sender, subject, snippet, ...}. known_companies: lowercase company
    names from OUR jobs table — any match in sender/subject passes.
    """
    sender = (email.get("sender") or "").lower()
    subject = (email.get("subject") or "").lower()
    snippet = (email.get("snippet") or "").lower()
    dom = _domain(sender)

    if dom in NEGATIVE_DOMAINS:
        return False
    if any(h in subject for h in NON_JOB_SUBJECT_HINTS):
        return False

    if dom in ATS_DOMAINS or any(dom.endswith("." + d) for d in ATS_DOMAINS):
        return True
    if sender in KNOWN_JOB_SENDERS:
        return True
    if dom in INTERVIEW_DOMAINS:
        return True
    if known_companies:
        hay = f"{sender} {subject}"
        if any(c and c in hay for c in known_companies):
            return True
    text = f"{subject} {snippet}"
    return any(p in text for p in RECRUITING_PHRASES)


_PROMPT = """You classify emails about job applications. For EACH email below,
return one JSON object: {"i": <number>, "company": "<employer name or empty>",
"role": "<role title or empty>", "event_type": "<one of: %s>",
"confidence": <0.0-1.0>}.

Rules: 'applied' = an application-received confirmation. 'rejection' = a
turn-down. 'recruiter_inbound' = cold outreach about a NEW opportunity.
'other' = not actually a job-application event (newsletters, job alerts,
promos). Company = the EMPLOYER, never the ATS or job board name.

Respond with ONLY a JSON array containing one object per email. No fences.
""" % ", ".join(EVENT_TYPES)


def classify_batch(emails: list[dict]) -> list[dict]:
    """Classify up to BATCH emails in one LLM call.

    Returns one dict per input email (same order): {company, role, event_type,
    confidence}. Failures degrade to event_type 'other', confidence 0.
    """
    fallback = [{"company": "", "role": "", "event_type": "other", "confidence": 0.0}
                for _ in emails]
    if not emails:
        return []

    blocks = []
    for i, e in enumerate(emails, 1):
        blocks.append(
            f"--- EMAIL {i} ---\n"
            f"From: {e.get('sender', '')}\n"
            f"Subject: {e.get('subject', '')}\n"
            f"Snippet: {(e.get('snippet') or '')[:300]}"
        )
    try:
        client = get_client(stage="track")
        response = client.chat(
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": "\n\n".join(blocks)}],
            max_tokens=120 * len(emails),
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001 — classification is best-effort
        logger.warning("Classifier LLM call failed: %s", e)
        return fallback

    m = re.search(r"\[.*\]", response, re.DOTALL)
    if not m:
        return fallback
    try:
        entries = json.loads(m.group(0))
    except json.JSONDecodeError:
        return fallback

    out = list(fallback)
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("i", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(emails)):
            continue
        event = str(entry.get("event_type", "other"))
        try:
            conf = max(0.0, min(1.0, float(entry.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0
        out[idx] = {
            "company": str(entry.get("company", ""))[:200],
            "role": str(entry.get("role", ""))[:200],
            "event_type": event if event in EVENT_TYPES else "other",
            "confidence": conf,
        }
    return out
