# Spec B: DOCX output + application-outcome schema

Work inside this repo; **do not commit**. No network; `python-docx` is already
installed in `.venv`. Suite green before you stop:
`.venv/bin/python -m pytest tests/ -q`.

Read first: `src/applypilot/scoring/pdf.py` (the resume/cover text formats it
parses and the convert_to_pdf entry points), `src/applypilot/scoring/tailor.py`
`assemble_resume_text` (the exact text shape: name line, contact line,
SUMMARY / TECHNICAL SKILLS / EXPERIENCE / EDUCATION headers, "- " bullets),
`src/applypilot/pipeline.py` (the `pdf` stage), `src/applypilot/database.py`
(schema + how columns/tables are created — follow the existing migration
pattern exactly), `src/applypilot/server.py` (files endpoints, stats),
`tests/test_server.py`.

## 1. DOCX rendering — `src/applypilot/scoring/docx_render.py`

Clean-room renderer (do NOT copy any external project's code) using
python-docx:

- `resume_to_docx(text: str, out_path: Path) -> Path` — parses the SAME
  structured text pdf.py consumes: first line = name (style: 16pt bold,
  small-caps feel via spacing is fine), second line = contact (9.5pt,
  centered), section headers in KNOWN_HEADERS (SUMMARY, TECHNICAL SKILLS,
  EXPERIENCE, EDUCATION, PROJECTS, SKILLS, CERTIFICATIONS) rendered as
  10.5pt bold with a bottom hairline border, "- " lines as proper Word
  bullets (List Bullet style or manual numbering), everything else as body
  paragraphs (10pt). Serif font: Georgia. Margins 0.7in. Single page-ish
  spacing (tight: 2-4pt after paragraphs). Keep it simple and robust —
  unknown lines are body text, never crash on odd input.
- `cover_to_docx(text: str, out_path: Path) -> Path` — date line, greeting,
  body paragraphs (11pt Georgia, 1.15 spacing), signature block. Plain and
  professional.
- Define all sizing/font constants at module top.

Generation points:
- The pipeline `pdf` stage: wherever a resume/cover PDF is (re)generated,
  also write the `.docx` sibling (same path, .docx suffix). Best-effort —
  a docx failure must not fail the stage (log warning).
- Server: `GET /api/files/resume|cover?url=&fmt=docx` (default fmt=pdf
  keeps current behavior). If the .docx sibling is missing but the .txt
  exists, render it on demand (then serve). Correct media type
  (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`),
  inline param keeps working for pdf; docx always attachment.

## 2. Outcome schema + endpoints

- Migration (database.py, existing pattern): jobs gains `outcome TEXT`,
  `outcome_at TEXT`, `outcome_source TEXT` (values 'manual'|'gmail').
  Outcome values: `responded`, `screen`, `interview`, `offer`, `rejected`
  (NULL = nothing yet).
- New table `app_events` (for the Gmail tracker landing next, and manual
  audit): `id INTEGER PK`, `message_id TEXT UNIQUE`, `thread_id TEXT`,
  `job_url TEXT NULL`, `company TEXT`, `role TEXT`, `event_type TEXT`,
  `confidence REAL`, `email_ts TEXT`, `subject TEXT`, `created_at TEXT`.
- Endpoints:
  - `POST /api/jobs/outcome` {url, outcome} — validate outcome value (422),
    set outcome/outcome_at(now)/outcome_source='manual'; outcome="" clears
    all three. Also insert an app_events row (message_id NULL is fine —
    relax UNIQUE to allow NULLs, sqlite does) with event_type=outcome,
    source manual.
  - `GET /api/outcomes/summary` — `{funnel: {applied, responded, screen,
    interview, offer, rejected}, by_score: [{band: "8", applied, responded,
    response_rate}], by_source: [{site, applied, responded, response_rate}]}`.
    "applied" = apply_status IN (applied, handoff); responded = any non-null
    outcome other than NULL; ordering of outcome severity: rejected counts
    as a response. Bands = fit_score values 7..10.
  - `GET /api/outcomes/events?limit=30` — newest app_events first, each with
    the matched job's company/title when job_url is set.
- `GET /api/jobs` rows gain `outcome` field (and the Job detail too).

## 3. Tests

docx: render a representative resume text + cover text to tmp paths, assert
files exist, open with python-docx and check name/sections/bullet counts;
odd input (empty text, no headers) doesn't crash. Server: fmt=docx serves
right media type and renders on demand; outcome POST round-trip + validation
+ clear; summary math against seeded rows (mixed scores/sites/outcomes);
events endpoint ordering + job join.

## Constraints

Don't touch web/, docs/, apply/, llm.py (another change may be in flight
there — if you find llm.py modified, leave it alone). `python-docx` goes in
pyproject main dependencies. Style matches neighbors; full suite green;
short summary; no commits.
