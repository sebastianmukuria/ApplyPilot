# ApplyPilot

**An autonomous job-application pipeline with a human-in-the-loop control panel.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/sebastianmukuria/ApplyPilot?style=social)](https://github.com/sebastianmukuria/ApplyPilot)

Discovers jobs across five boards plus Workday and direct career sites, scores
them against your résumé, writes tailored cover letters, and fills out the
applications in a real Chrome — **pinging your phone (Telegram) when it needs
you** and handing you the open browser for the final Submit.

![ApplyPilot Flight Deck — bento deck, animated queue, live charts, and the paper-plane easter egg](docs/demo.gif)

*([higher-quality video](docs/demo.mp4))*

> Forked from [Pickle-Pixel/ApplyPilot](https://github.com/Pickle-Pixel/ApplyPilot).
> This fork adds the Flight Deck web app above, a first-run setup wizard for
> non-developers, supervised applies with hand-off + phone pings, **autopilot
> batch applying**, in-app pipeline runs, **Gmail response tracking with a
> pipeline radar**, DOCX output, a résumé library, selectable salary
> strategies, live cost tracking, and a long list of speed and safety fixes.
> **A Claude Code subscription alone runs the entire thing** — no API keys
> required.

---

## What It Does

ApplyPilot is a 6-stage autonomous job application pipeline. It discovers jobs across 5+ boards, scores them against your resume with AI, tailors your resume per job, writes cover letters, and **submits applications for you**. It navigates forms, uploads documents, answers screening questions, all hands-free.

Install with one line (macOS / Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/sebastianmukuria/ApplyPilot/fixes/pre-flight/install.sh | bash
```

Or from a clone:

```bash
git clone https://github.com/sebastianmukuria/ApplyPilot.git && cd ApplyPilot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[app,gui]"
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
playwright install chromium   # the headless browser used by enrich, smart-extract, and PDF rendering
```

Then:

```bash
applypilot init          # one-time setup: resume, profile, preferences, API keys
applypilot doctor        # verify your setup — shows what's installed and what's missing
applypilot run           # discover > enrich > score > tailor > cover letters
applypilot run -w 4      # same but parallel (4 threads for discovery/enrichment)
applypilot app           # the Flight Deck web app (queue, live runs, intel, answers)
applypilot apply         # browser-driven applications (supervised by default)
applypilot apply -w 3    # parallel apply (3 Chrome instances)
applypilot apply --dry-run  # fill forms without submitting
```

> **Why two install commands?** `python-jobspy` pins an exact numpy version in its metadata that conflicts with pip's resolver, but works fine at runtime with any modern numpy. The `--no-deps` flag bypasses the resolver; the second command installs jobspy's actual runtime dependencies. Everything except `python-jobspy` installs normally.

> **New here?** [SETUP.md](SETUP.md) is the full step-by-step walkthrough — install,
> config, résumé modes, the control panel, supervised auto-apply, and Telegram pings.
> **Not a developer?** It starts with a copy-paste prompt that has Claude Code do the
> entire install and configuration for you, and a one-line installer with a
> double-clickable launcher for macOS.

### The Flight Deck (web app)

```bash
pip install "applypilot[app]"     # from a source checkout: pip install -e ".[app]"
applypilot app                    # → http://127.0.0.1:8765
```

A local web app (FastAPI + React, no Node needed — assets ship prebuilt):
an animated bento **deck** with the funnel, live-run terminal (SSE) and an
awaiting-your-confirmation strip; a **queue** of scored job cards with
in-place dossier previews, filters, and one-click apply; **intel** charts
with live LLM cost tracking; and an **answers** studio grounded in your real
projects. Supports two résumé modes — per-job AI tailoring or a **fixed
master résumé** — and three salary-answer strategies (match the posting /
leave blank / fixed amount). There's also a hidden easter egg for the
observant. Run/stop process controls are macOS/Linux only.

The previous Streamlit panel remains available as `applypilot gui`
(`pip install "applypilot[gui]"`).

### Telegram alerts

Hook up a free Telegram bot (two-minute setup, [SETUP.md §7](SETUP.md)) and the
apply agent pings your phone at the two moments it needs a human: when a
CAPTCHA appears in the visible Chrome window, and when an application is fully
filled and ready for your review. In supervised mode (the default) the agent
**never clicks Submit** — it hands you the open browser and you finish at your
own pace.

---

## Two Paths

### Full Pipeline (recommended)
**Requires:** Python 3.11+, Node.js (for npx), Gemini API key (free), Claude Code CLI, Chrome

Runs all 6 stages, from job discovery to autonomous application submission. This is the full power of ApplyPilot.

### Discovery + Tailoring Only
**Requires:** Python 3.11+, Gemini API key (free)

Runs stages 1-5: discovers jobs, scores them, tailors your resume, generates cover letters. You submit applications manually with the AI-prepared materials.

---

## The Pipeline

| Stage | What Happens |
|-------|-------------|
| **1. Discover** | Scrapes 5 job boards (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs) + 48 Workday employer portals + 30 direct career sites |
| **2. Enrich** | Fetches full job descriptions via JSON-LD, CSS selectors, or AI-powered extraction |
| **3. Score** | AI rates every job 1-10 based on your resume and preferences. Only high-fit jobs proceed |
| **4. Tailor** | AI rewrites your resume per job: reorganizes, emphasizes relevant experience, adds keywords. Never fabricates |
| **5. Cover Letter** | AI generates a targeted cover letter per job |
| **6. Auto-Apply** | Claude Code navigates application forms, fills fields, uploads documents, answers questions, and submits |

Each stage is independent. Run them all or pick what you need.

---

## ApplyPilot vs The Alternatives

| Feature | ApplyPilot | AIHawk | Manual |
|---------|-----------|--------|--------|
| Job discovery | 5 boards + Workday + direct sites | LinkedIn only | One board at a time |
| AI scoring | 1-10 fit score per job | Basic filtering | Your gut feeling |
| Resume tailoring | Per-job AI rewrite | Template-based | Hours per application |
| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |
| Supported sites | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs, 48 Workday portals, 30 direct sites | LinkedIn | Whatever you open |
| License | AGPL-3.0 | MIT | N/A |

---

## Requirements

| Component | Required For | Details |
|-----------|-------------|---------|
| Python 3.11+ | Everything | Core runtime |
| Playwright Chromium | Enrich, smart-extract, PDF | `playwright install chromium` after pip install (not bundled) |
| Node.js 18+ | Auto-apply | Needed for `npx` to run Playwright MCP server |
| Gemini API key | Scoring, tailoring, cover letters | Free tier (15 RPM / 1M tokens/day) is enough |
| Chrome/Chromium | Auto-apply | Auto-detected on most systems |
| Claude Code CLI | Auto-apply | Install from [claude.ai/code](https://claude.ai/code) |

**Gemini API key is free.** Get one at [aistudio.google.com](https://aistudio.google.com). OpenAI and local models (Ollama/llama.cpp) are also supported.

### Optional

| Component | What It Does |
|-----------|-------------|
| Telegram bot | Pings your phone when a run needs you: CAPTCHA in the window, or application filled and ready for review. Free; see [SETUP.md](SETUP.md) for the 2-minute setup |
| CapSolver API key | Solves CAPTCHAs during auto-apply (hCaptcha, reCAPTCHA, Turnstile, FunCaptcha). Without it, you solve the CAPTCHA yourself in the visible window (the agent waits and pings you) |

> **Note:** python-jobspy is installed separately with `--no-deps` because it pins an exact numpy version in its metadata that conflicts with pip's resolver. It works fine with modern numpy at runtime.

---

## Configuration

All generated by `applypilot init`:

### `profile.json`
Your personal data in one structured file: contact info, work authorization, compensation, experience, skills, resume facts (preserved during tailoring), and EEO defaults. Powers scoring, tailoring, and form auto-fill.

### `searches.yaml`
Job search queries, target titles, locations, boards. Run multiple searches with different parameters.

### `.env`
API keys and runtime config: `GEMINI_API_KEY`, `LLM_MODEL`, `CAPSOLVER_API_KEY` (optional).

### Package configs (shipped with ApplyPilot)
- `config/employers.yaml` - Workday employer registry (48 preconfigured)
- `config/sites.yaml` - Direct career sites (30+), blocked sites, base URLs, manual ATS domains
- `config/searches.example.yaml` - Example search configuration

---

## How Stages Work

### Discover
Queries Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs via JobSpy. Scrapes 48 Workday employer portals (configurable in `employers.yaml`). Hits 30 direct career sites with custom extractors. Deduplicates by URL.

### Enrich
Visits each job URL and extracts the full description. 3-tier cascade: JSON-LD structured data, then CSS selector patterns, then AI-powered extraction for unknown layouts.

### Score
AI scores every job 1-10 against your profile. 9-10 = strong match, 7-8 = good, 5-6 = moderate, 1-4 = skip. Only jobs above your threshold proceed to tailoring.

### Tailor
Generates a custom resume per job: reorders experience, emphasizes relevant skills, incorporates keywords from the job description. Your `resume_facts` (companies, projects, metrics) are preserved exactly. The AI reorganizes but never fabricates.

### Cover Letter
Writes a targeted cover letter per job referencing the specific company, role, and how your experience maps to their requirements.

### Auto-Apply
Claude Code launches a Chrome instance, navigates to each application page, detects the form type, fills personal information and work history, uploads the resume and cover letter, and answers screening questions with AI. A live dashboard shows progress in real-time.

**Supervised by default:** the agent fills everything, pings you on Telegram, then leaves the browser open and steps away — you review and click Submit yourself (the job is tracked as `handoff` until you confirm). Set `APPLYPILOT_SUPERVISED=0` to let it submit on its own.

The Playwright MCP server is configured automatically at runtime per worker. No manual MCP setup needed.

```bash
# Utility modes (no Chrome/Claude needed)
applypilot apply --mark-applied URL    # manually mark a job as applied
applypilot apply --mark-failed URL     # manually mark a job as failed
applypilot apply --reset-failed        # reset all failed jobs for retry
applypilot apply --gen --url URL       # generate prompt file for manual debugging
```

---

## CLI Reference

```
applypilot init                         # First-time setup wizard
applypilot doctor                       # Verify setup, diagnose missing requirements
applypilot run [stages...]              # Run pipeline stages (or 'all')
applypilot run --workers 4              # Parallel discovery/enrichment
applypilot run --stream                 # Concurrent stages (streaming mode)
applypilot run --min-score 8            # Override score threshold
applypilot run --dry-run                # Preview without executing
applypilot run --validation lenient     # Relax validation: skip the LLM judge (fastest, fewest API calls)
applypilot run --validation strict      # Strictest validation (retries on any banned word)
applypilot apply                        # Launch auto-apply
applypilot apply --workers 3            # Parallel browser workers
applypilot apply --dry-run              # Fill forms without submitting
applypilot apply --continuous           # Run forever, polling for new jobs
applypilot apply --headless             # Headless browser mode
applypilot apply --url URL              # Apply to a specific job
applypilot status                       # Pipeline statistics
applypilot app                          # Flight Deck web app (queue, runs, intel)
applypilot gui                          # legacy Streamlit panel
applypilot dashboard                    # Open HTML results dashboard
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR guidelines.

---

## License

ApplyPilot is licensed under the [GNU Affero General Public License v3.0](LICENSE).

You are free to use, modify, and distribute this software. If you deploy a modified version as a service, you must release your source code under the same license.
