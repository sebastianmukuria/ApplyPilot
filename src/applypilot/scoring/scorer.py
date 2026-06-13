"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import json
import os
import logging
import re
import time
from datetime import datetime, timezone

from applypilot.config import RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2-3 sentences explaining the score]"""


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        {"score": int | None, "keywords": str, "reasoning": str}.
        score is None when no parseable SCORE line was found (a parse failure,
        which must NOT be persisted as a permanent fit_score of 0).
    """
    score = None
    keywords = ""
    reasoning = response

    for raw in response.split("\n"):
        # Tolerate markdown decoration like "**SCORE:** 8" or "## Score: 7/10".
        line = raw.strip().lstrip("#*").strip()
        low = line.lower()
        if low.startswith("score"):
            m = re.search(r"\d+", line)
            score = max(1, min(10, int(m.group()))) if m else None
        elif low.startswith("keywords"):
            keywords = line.split(":", 1)[-1].strip().rstrip("*").strip()
        elif low.startswith("reasoning"):
            reasoning = line.split(":", 1)[-1].strip().rstrip("*").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning}


def score_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job.get('company') or job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client(stage="score")
        response = client.chat(messages, max_tokens=512, temperature=0.2)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        # score=None (not 0) so the job stays pending and is retried next run.
        return {"score": None, "keywords": "", "reasoning": f"LLM error: {e}"}


def _batch_size() -> int:
    """Jobs per LLM call. Batching keeps claude-cli (and Gemini free-tier RPM)
    viable at discovery volume; 1 disables batching."""
    try:
        return max(1, int(os.environ.get("APPLYPILOT_SCORE_BATCH", "8")))
    except ValueError:
        return 8


def score_jobs_batch(resume_text: str, jobs: list[dict]) -> dict[str, dict]:
    """Score several jobs in one LLM call.

    Returns {url: result} for every job the model covered with a parseable
    entry; callers fall back to score_job() for anything missing. Never
    raises -- a failed batch returns {}.
    """
    blocks = []
    for i, job in enumerate(jobs, 1):
        blocks.append(
            f"### JOB {i}\n"
            f"TITLE: {job['title']}\n"
            f"COMPANY: {job.get('company') or job['site']}\n"
            f"LOCATION: {job.get('location', 'N/A')}\n"
            f"DESCRIPTION:\n{(job.get('full_description') or '')[:1500]}"
        )
    system = (
        SCORE_PROMPT
        + "\n\nYou are scoring MULTIPLE jobs in one pass. Respond with ONLY a "
        "JSON array, one object per job, no markdown fences:\n"
        '[{"n": 1, "score": 7, "keywords": "...", "reasoning": "..."}]\n'
        "Every job number must appear exactly once."
    )
    user = f"RESUME:\n{resume_text}\n\n---\n\n" + "\n\n".join(blocks)

    try:
        client = get_client(stage="score")
        response = client.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=320 * len(jobs),
            temperature=0.2,
        )
    except Exception as e:
        log.warning("Batch scoring call failed (%s); falling back per-job", e)
        return {}

    m = re.search(r"\[.*\]", response, re.DOTALL)
    if not m:
        log.warning("Batch scoring response had no JSON array; falling back per-job")
        return {}
    try:
        entries = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.warning("Batch scoring response JSON was invalid; falling back per-job")
        return {}

    out: dict[str, dict] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("n", 0))
            score = int(entry.get("score", 0))
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= len(jobs)) or not (1 <= score <= 10):
            continue
        job = jobs[idx - 1]
        out[job["url"]] = {
            "score": score,
            "keywords": str(entry.get("keywords", "")),
            "reasoning": str(entry.get("reasoning", "")),
        }
    return out


def run_scoring(limit: int = 0, rescore: bool = False) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score", limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    # Convert sqlite3.Row to dicts if needed
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    results: list[dict] = []

    batch_n = _batch_size()
    pending: list[dict] = list(jobs)
    scored_map: dict[str, dict] = {}
    while pending:
        chunk, pending = pending[:batch_n], pending[batch_n:]
        if len(chunk) > 1:
            scored_map.update(score_jobs_batch(resume_text, chunk))
        for job in chunk:
            if job["url"] not in scored_map:
                scored_map[job["url"]] = score_job(resume_text, job)

    for job in jobs:
        result = dict(scored_map.get(job["url"]) or {"score": None, "keywords": "", "reasoning": "missing"})
        result["url"] = job["url"]
        completed += 1

        results.append(result)

        if result["score"] is None:
            # Parse/LLM failure -- leave fit_score NULL so it's retried, don't
            # burn the result by persisting a permanent 0.
            errors += 1
            log.warning(
                "[%d/%d] score failed (left pending)  %s",
                completed, len(jobs), job.get("title", "?")[:60],
            )
            continue

        # Commit each score as it lands so an interrupt doesn't discard the run.
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ? WHERE url = ?",
            (result["score"], f"{result['keywords']}\n{result['reasoning']}", now, result["url"]),
        )
        conn.commit()

        log.info(
            "[%d/%d] score=%s  %s",
            completed, len(jobs), result["score"], job.get("title", "?")[:60],
        )

    scored = len(results) - errors
    elapsed = time.time() - t0
    log.info("Done: %d scored, %d failed in %.1fs (%.1f jobs/sec)",
             scored, errors, elapsed, len(results) / elapsed if elapsed > 0 else 0)

    # Score distribution
    dist = conn.execute("""
        SELECT fit_score, COUNT(*) FROM jobs
        WHERE fit_score IS NOT NULL
        GROUP BY fit_score ORDER BY fit_score DESC
    """).fetchall()
    distribution = [(row[0], row[1]) for row in dist]

    return {
        "scored": scored,
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
    }
