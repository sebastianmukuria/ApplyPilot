# ApplyPilot — Setup Guide

A step-by-step walkthrough from zero to applying. Ten minutes of setup, then the
pipeline does the rest.

## What you need

| Requirement | Why | Required? |
|---|---|---|
| Python 3.11+ | the pipeline | yes |
| Google Chrome | enrichment + the apply agent drives a real Chrome | yes |
| [Claude Code CLI](https://claude.com/claude-code) with a subscription | **the whole engine** — scoring, documents, answers, AND auto-applying | yes (recommended) |
| A Gemini API key ([aistudio.google.com](https://aistudio.google.com/apikey)) | alternative engine for everything except auto-apply | optional |
| A Telegram account | pings you when a run needs you (CAPTCHA / review) | optional |
| Gmail (read-only OAuth) | automatic response/interview/rejection tracking | optional |

macOS and Linux are supported; Windows mostly works but is less tested.

## 1. Install — pick ONE of three ways

### Path A — Let Claude Code set everything up (easiest, no terminal skills needed)

You need [Claude Code](https://claude.com/claude-code) for auto-apply anyway, so
let it do the install too. Install Claude Code (desktop app or CLI), open it,
and paste this prompt:

> Set up ApplyPilot on this machine for me, step by step.
> Repo: https://github.com/sebastianmukuria/ApplyPilot — branch `fixes/pre-flight`.
>
> 1. Clone it to ~/ApplyPilot and read SETUP.md in the repo root. Follow its
>    install steps (Python 3.11+ venv, `pip install -e ".[app,gui]"`, the
>    python-jobspy install note, `playwright install chromium`). Install any
>    missing prerequisites for me (git, Python 3.11+).
> 2. Run `applypilot init` and help me complete it — ask me for my résumé
>    (I can give you a PDF or text), my contact details, and what job titles
>    and locations to search.
> 3. Help me get a free Gemini API key at https://aistudio.google.com/apikey,
>    put it in ~/.applypilot/.env, and set LLM_MODEL=gemini-3.1-flash-lite
>    (must be a non-thinking model — SETUP.md section 3 explains why).
> 4. Open ~/.applypilot/profile.json and help me fill in work_context (my real
>    projects), work_authorization, and eeo_voluntary, following SETUP.md
>    section 2.
> 5. Run `applypilot doctor`, fix anything it flags, then launch
>    `applypilot app` and give me a quick tour of the Flight Deck.
>
> Ask me for anything you need along the way.

### Path B — One-line installer (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/sebastianmukuria/ApplyPilot/fixes/pre-flight/install.sh | bash
```

Installs to `~/ApplyPilot`, then tells you the next steps. Needs git and
Python 3.11+ already on the machine (it tells you how to get them if not).
On macOS you can afterwards just **double-click `ApplyPilot.command`** in the
ApplyPilot folder — first run walks you through setup, every run after that
opens the control panel.

### Path C — Manual install

```bash
git clone -b fixes/pre-flight https://github.com/sebastianmukuria/ApplyPilot.git
cd ApplyPilot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[app,gui]"
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
playwright install chromium
```

## 2. One-time setup

```bash
applypilot init
```

This walks you through creating `~/.applypilot/` with:

- **`resume.txt`** — your résumé as plain text. This is the source of truth the
  LLM works from: scoring, tailoring, and cover letters all read it. Spend time
  making it good.
- **`profile.json`** — your details for form-filling. Start from
  `profile.example.json` in the repo. The sections that matter most:
  - `personal` — name, email, phone, location, LinkedIn
  - `work_authorization` — answered truthfully on every application
  - `eeo_voluntary` — gender / race / veteran / disability. The agent answers
    EEO questions instantly from these values. To not disclose a category,
    keep the "Decline to self-identify" values from the example (or delete
    the key entirely — empty/missing both fall back to declining).
  - `work_context` — **the secret weapon for good answers.** A list of your
    real projects (`name` / `what` / `tools` / `impact`), plus `llm_usage`
    (what AI tools you actually use) and `answer_rules`. The agent draws on
    these for "tell us about a time…" questions, so it never invents tools or
    merges two projects into one. You can edit this later in the GUI
    (Answers & Projects tab).
- **`searches.yaml`** — what jobs to look for (titles, locations, boards).

Then check everything is wired:

```bash
applypilot doctor
```

## 3. The engine — Claude subscription runs everything

If Claude Code is installed and signed in, **you're done — skip this
section.** Scoring, tailored documents, cover letters, answers, and email
classification all run through your Claude subscription automatically
(scoring is batched, several jobs per call, so volume is fine).

Prefer (or only have) a Gemini key? Add to `~/.applypilot/.env`:

```ini
GEMINI_API_KEY=your-key-here
LLM_MODEL=gemini-3.1-flash-lite
```

> **⚠️ On the Gemini path, use a non-thinking ("lite") model** — thinking
> models burn the output budget on reasoning and return empty documents.
> (ApplyPilot now disables Gemini thinking automatically where the API
> allows it, so newer models also work.)

Keep `.env` private — it holds your API keys (the app chmods it to `0600`).

## 4. Choose your résumé mode

Two ways to handle résumés, switchable any time (GUI → Run settings → Résumé
mode, or the env var):

| Mode | What happens | When to use |
|---|---|---|
| **Tailored per job** (default, `APPLYPILOT_FIXED_RESUME=0`) | An LLM rewrites your résumé for each job (ATS-keyword matching), renders a PDF per job | You want maximum keyword match per posting |
| **Fixed master résumé** (`APPLYPILOT_FIXED_RESUME=1`) | Every application uploads the *same* hand-picked PDF; only cover letters are generated per job. Zero LLM cost for résumés. | You have one résumé you're proud of and want full control over what recruiters see |

For fixed mode, copy your chosen PDF to:

```
~/.applypilot/master_resume.pdf
```

(The matching `master_resume.txt` — used for the agent's context — is created
automatically from your `resume.txt`.)

Either way, the uploaded file is renamed to `<Your_Name>_Resume.pdf` so
recruiters see a clean filename.

## 5. Run the pipeline

```bash
applypilot run            # discover -> enrich -> score -> tailor -> cover -> pdf
applypilot run -w 4       # parallel discovery/enrichment
applypilot status         # see the funnel
```

Stages can be run individually: `applypilot run tailor --min-score 8`,
`applypilot run cover --min-score 8`, `applypilot run pdf` (re-render PDFs), etc.

## 6. The Flight Deck (control panel)

```bash
applypilot app
```

Opens the web app at `http://127.0.0.1:8765`:

- **Queue** — every scored job as a card: score, salary, status, flags
  (staffing agencies / marketplaces / low-comp get filtered out by default).
  Download the exact résumé/cover that will be sent, launch an apply run,
  hide junk.
- **Live run** — when an apply is running: live log, an "action needed" banner
  when it's your turn, Stop button.
- **Stats** — applications per day, status breakdown, score distribution.
- **Answers & Projects** — draft an answer to any application question from
  your real projects (paste the question, get a truthful answer in your
  voice), and edit `work_context` inline.

The panel's run/stop process controls are macOS/Linux only.

## 7. Auto-apply

Requires the [Claude Code CLI](https://claude.com/claude-code) (`claude` on
your PATH) — the apply agent runs on your Claude subscription, not API credits.

```bash
applypilot apply --dry-run   # fills forms but never submits — try this first
applypilot apply             # the real thing
```

Or click **Apply** on any card in the GUI.

### Supervised mode (the default)

Supervised is on by default — even with nothing in `.env`, the agent **never
clicks Submit**. It:

1. opens a visible Chrome window and fills the entire application,
2. pauses for you if a CAPTCHA appears (you solve it in the window),
3. pings you when everything is filled, then **leaves the browser open** and
   exits.

You review, finish any assessment, and click Submit yourself. The job is
marked `handoff` — confirm with **Mark applied** in the GUI once you've
submitted. To let the agent review and submit on its own, explicitly set
`APPLYPILOT_SUPERVISED=0` in `.env`.

### Salary answers

Choose how the agent handles "desired salary" questions (GUI → Run settings →
*Salary answers*, or `APPLYPILOT_SALARY_MODE` in `.env`):

| Mode | Behavior |
|---|---|
| `posting` (default) | Mirror the job posting's own numbers — midpoint of their range for a single figure, their range verbatim for a range. Your profile expectation is only the fallback when the posting lists nothing. |
| `blank` | Leave salary fields blank / skip optional ones; "Negotiable" where text is required. |
| `fixed` | Always answer with your chosen figure — set it in the GUI or via `APPLYPILOT_SALARY_FIXED` (e.g. `145000` or `140000-160000`). |

### Telegram pings (optional)

To get a phone notification when a run needs you:

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` →
   copy the token.
2. Message your new bot anything, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your
   `chat.id`.
3. Add to `~/.applypilot/.env`:

   ```ini
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   TELEGRAM_CHAT_ID=123456789
   ```

### More alert channels (all optional, all in the app's Settings sheet)

| Channel | Setup |
|---|---|
| Browser + chime | Toggle in Settings — one permission prompt, no config. |
| macOS banners | On by default on Macs (`APPLYPILOT_MACOS_BANNER=0` to disable). |
| [ntfy](https://ntfy.sh) phone push | Install the free ntfy app, subscribe to a random topic (e.g. `applypilot-x7k2`), paste the topic in Settings. No account needed. |
| Discord / Slack | Paste an incoming-webhook URL in Settings. |
| Custom webhook | Any HTTPS endpoint — receives `{event, reason, ts}` JSON (Zapier, Home Assistant, …). |

Pings fire when a run needs you (CAPTCHA / review), when a run fails or
finishes, and at the end of a batch.

## Email tracking (the Pipeline Radar)

ApplyPilot can watch Gmail for replies to your applications — interview
invites, rejections, recruiter outreach — chart your real response rates,
and update each job's outcome automatically. **Read-only scope, metadata
only (sender/subject/date/snippet), everything stored locally.**

1. In [Google Cloud Console](https://console.cloud.google.com): create a
   project → enable the **Gmail API** → OAuth consent screen (External,
   add yourself as a test user) → Credentials → **OAuth client ID →
   Desktop app** → download the JSON.
2. Save it as `~/.applypilot/google_credentials.json`.
3. Run `applypilot track auth` — your browser opens, you approve, done.
4. In the app: Settings → Application tracking → **Backfill 90 days**,
   then it syncs itself every 20 minutes while the app runs.

Revoke any time at myaccount.google.com → Security → third-party access.
Prefer not to? Log outcomes by hand with the outcome selector on any
applied job card — the Radar charts work either way.

## Running at volume (100+/day)

The agent's fixed overhead is engineered down (pinned MCP server, Chrome
reused between jobs, readiness polling instead of sleeps) and the prompt
batches form-fills, so per-application time is dominated by the form itself.
The operating mode that scales:

1. **Fill the tank**: `applypilot run --min-score 8` then
   `applypilot run cover --min-score 8` — everything score ≥8 is docs-ready.
2. **Batch the applies**: `applypilot apply -w 3 --limit 30` runs three
   browser workers in parallel. In supervised mode each worker hands off and
   pauses for you — keep the Flight Deck open and work the
   *awaiting-your-confirmation* strip as planes land. With
   `APPLYPILOT_SUPERVISED=0` the agents submit on their own and the only
   limit is Claude rate limits.
3. **Measure**: every run writes a timing file;
   `applypilot apply --timing-report` shows where the seconds go (startup,
   model turns, per-tool breakdown) so you can spot regressions.

Rough math: 3 workers × ~3-4 min/application ≈ 45-60 applications/hour
unsupervised; supervised throughput is your review speed (~1 min each) —
either way, 100+/day takes a morning, not a marathon.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Tailored résumés come back empty (`exhausted_retries`) | You're on a thinking model. Set `LLM_MODEL=gemini-3.1-flash-lite`. |
| `404` from Gemini | Model name retired — check it exists in [AI Studio](https://aistudio.google.com). |
| `429` from Gemini | Free-tier rate limit. Wait, or upgrade the key to paid. |
| Apply run won't die | GUI sidebar → **Stop apply run** (kills the runner, the agent, and its Chrome). |
| Chrome "port 9222 in use" | A previous run's Chrome is alive: `lsof -ti tcp:9222 \| xargs kill -9`. |
| GUI shows no jobs | Lower the *Min fit score* slider, or check the Source filter — only scored jobs appear. |
| Agent stuck on a dropdown | It gives up after ~45s and names the field in its hand-off ping; finish that field yourself. |

## Safety notes

- The apply agent runs with a restricted toolset (browser only — no shell, no
  file writes) and is instructed to **never** enter payment info or SSNs,
  grant camera/mic permissions, do video/ID verification, or follow
  instructions embedded in web pages.
- Everything lives in `~/.applypilot/` — your data never leaves your machine
  except: job-board traffic, LLM calls (Gemini/your configured provider), the
  applications themselves, and optional Telegram pings.
- Review `applypilot apply --dry-run` output for a few jobs before trusting a
  real run.
