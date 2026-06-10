"""ApplyPilot control panel — "Editorial Instrument" (Anthropic brand kit).

Run:  applypilot gui          (requires the gui extra: pip install "applypilot[gui]")

Design notes:
- Palette is the Anthropic kit only: ivory #faf9f5, ink #141413, light-gray
  #e8e6dc, mid-gray #b0aea5; accents clay #d97757 / slate-blue #6a9bcc /
  sage #788c5d. Status semantics are FIXED (sage=success, blue=in flight,
  clay=needs a human) and ignore the accent picker.
- Poppins (UI) + Lora (display/prose) are the only typefaces per the kit;
  code/log wells render in Poppins. Swap to `ui-monospace, Menlo` if the
  two-typeface rule is ever relaxed.
- No emojis anywhere; Material Symbols via Streamlit's icon= params.
"""
import html
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

APP = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))
DB = APP / "applypilot.db"
LOGDIR = APP / "logs"
ENV = APP / ".env"
PROFILE = APP / "profile.json"
HIDDEN_FILE = APP / "gui_hidden.json"
ACTIVE_FILE = APP / "gui_active_run.json"
PREFS_FILE = APP / "gui_prefs.json"

# First-run safety: `applypilot gui` bootstraps this, but a direct
# `streamlit run dashboard.py` must not crash on a fresh machine.
APP.mkdir(parents=True, exist_ok=True)
try:
    from applypilot.config import ensure_dirs as _ensure_dirs, load_env as _load_env
    from applypilot.database import init_db as _init_db
    _load_env(); _ensure_dirs(); _init_db()
except Exception:  # GUI still renders; pipeline tabs will show empty states
    pass

st.set_page_config(page_title="ApplyPilot", layout="wide", page_icon=":material/flight_takeoff:")

STAFFING = ["robert half", "fitt talent", "why hiring", "thecorporate", "crossing hurdles", "recruit", "staffing"]
MARKETPLACE = ["turing", "toptal", "upwork", "mercor", "fiverr", "gun.io"]
ACCENTS = {"Clay": "#d97757", "Slate Blue": "#6a9bcc", "Sage": "#788c5d"}

USAGE_FILE = APP / "llm_usage.jsonl"
# $ per 1M tokens (input, output). Estimates — edit to match your provider's
# current pricing. Longest-prefix match against the logged model name.
PRICES = {
    "gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
DEFAULT_PRICE = (0.10, 0.40)

# status -> (pill css modifier, label)
PILLS = {
    None: ("pending", "Pending"), "": ("pending", "Pending"),
    "applied": ("applied", "Applied"), "handoff": ("handoff", "Handed off"),
    "in_progress": ("inprogress", "In progress"), "failed": ("failed", "Failed"),
    "manual": ("manual", "Manual ATS"),
}


# ----------------------------- data helpers -----------------------------
def db():
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    return c


def read_env() -> dict:
    d = {}
    if ENV.exists():
        for ln in ENV.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k] = v
    return d


def write_env(updates: dict):
    d = read_env(); d.update(updates)
    ENV.write_text("\n".join(["# ApplyPilot configuration"] + [f"{k}={v}" for k, v in d.items()]) + "\n")
    try:
        os.chmod(ENV, 0o600)
    except OSError:
        pass


def load_prefs() -> dict:
    return json.loads(PREFS_FILE.read_text()) if PREFS_FILE.exists() else {}


def save_prefs(p):
    PREFS_FILE.write_text(json.dumps(p))


def load_hidden() -> set:
    return set(json.loads(HIDDEN_FILE.read_text())) if HIDDEN_FILE.exists() else set()


def save_hidden(s):
    HIDDEN_FILE.write_text(json.dumps(sorted(s)))


def load_profile() -> dict:
    return json.loads(PROFILE.read_text()) if PROFILE.exists() else {}


def save_profile(p):
    PROFILE.write_text(json.dumps(p, indent=2))


def counts() -> dict:
    c = db(); q = lambda s: c.execute(s).fetchone()[0]
    return {k: q(v) for k, v in {
        "total": "SELECT COUNT(*) FROM jobs",
        "scored": "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL",
        "ge7": "SELECT COUNT(*) FROM jobs WHERE fit_score>=7",
        "tailored": "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL",
        "applied": "SELECT COUNT(*) FROM jobs WHERE apply_status='applied'",
        "handoff": "SELECT COUNT(*) FROM jobs WHERE apply_status='handoff'",
        "failed": "SELECT COUNT(*) FROM jobs WHERE apply_status='failed'",
    }.items()}


def llm_spend() -> dict:
    """Aggregate llm_usage.jsonl into estimated cost (lifetime + today, by model)."""
    out = {"cost": 0.0, "today": 0.0, "tok_in": 0, "tok_out": 0, "calls": 0, "by_model": {}}
    if not USAGE_FILE.exists():
        return out
    today = datetime.now(timezone.utc).date().isoformat()  # usage is logged in UTC
    for ln in USAGE_FILE.read_text(errors="ignore").splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        model = r.get("model", "?")
        p_in, p_out = max(((k, v) for k, v in PRICES.items() if model.startswith(k)),
                          key=lambda kv: len(kv[0]), default=("", DEFAULT_PRICE))[1]
        cost = r.get("in", 0) / 1e6 * p_in + r.get("out", 0) / 1e6 * p_out
        out["cost"] += cost
        out["tok_in"] += r.get("in", 0); out["tok_out"] += r.get("out", 0); out["calls"] += 1
        if r.get("ts", "").startswith(today):
            out["today"] += cost
        m = out["by_model"].setdefault(model, {"cost": 0.0, "in": 0, "out": 0, "calls": 0})
        m["cost"] += cost; m["in"] += r.get("in", 0); m["out"] += r.get("out", 0); m["calls"] += 1
    return out


def salary_num(s) -> int:
    nums = [int(n.replace(",", "")) for n in re.findall(r"([0-9]{2,3},[0-9]{3})", s or "")]
    return max(nums) if nums else 0


def job_flags(r) -> list:
    f = []
    comp = (r["company"] or "").lower(); title = (r["title"] or "").lower()
    if any(s in comp for s in STAFFING):
        f.append("Staffing")
    if any(m in comp or m in title for m in MARKETPLACE):
        f.append("Marketplace")
    sn = salary_num(r["salary"])
    if (sn and sn < 100000) or "/hr" in title or "/hour" in title:
        f.append("Low comp")
    return f


def mark_applied(url):
    c = db(); c.execute("UPDATE jobs SET apply_status='applied', applied_at=? WHERE url=?",
                        (datetime.now().isoformat(), url)); c.commit()


def reset_job(url):
    c = db(); c.execute("UPDATE jobs SET apply_status=NULL, apply_error=NULL, apply_attempts=0, applied_at=NULL WHERE url=?",
                        (url,)); c.commit()


def launch_apply(url, company, model):
    LOGDIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logf = LOGDIR / f"gui_apply_{ts}.log"
    reset_job(url)
    # 0600: agent stderr can include URLs carrying the Telegram bot token
    fh = open(os.open(str(logf), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w")
    # -m applypilot resolves inside this interpreter's env even when the venv
    # isn't on PATH (e.g. launched via .venv/bin/applypilot gui)
    p = subprocess.Popen([sys.executable, "-m", "applypilot", "apply", "--url", url, "--model", model],
                         stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    ACTIVE_FILE.write_text(json.dumps(
        {"url": url, "company": company, "log": str(logf), "pid": p.pid, "started": ts, "model": model}))


def stop_run() -> str:
    """Kill the apply run: its process group (launched detached) + the agent
    subprocess + the Chrome it's driving. Works even if the marker was cleared."""
    killed = []
    # tracked process group (apply was started with start_new_session=True)
    if ACTIVE_FILE.exists():
        try:
            pid = json.loads(ACTIVE_FILE.read_text()).get("pid")
            if pid:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                time.sleep(1)
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except OSError:
                    pass
                killed.append(f"pgid {pid}")
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    # belt-and-suspenders: any apply process / agent / its Chrome (CDP 9222)
    subprocess.run(["pkill", "-9", "-f", "applypilot apply"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "-f", "mcp-apply"], stderr=subprocess.DEVNULL)
    subprocess.run("lsof -ti tcp:9222 | xargs -r kill -9", shell=True, stderr=subprocess.DEVNULL)
    ACTIVE_FILE.unlink(missing_ok=True)
    # the explicit nuke also forgets handed-off browsers (they're fair game here)
    (APP / "detached_ports.json").unlink(missing_ok=True)
    # nothing is running anymore, so any in_progress lock is stale -- release
    # them or the locked jobs stay invisible in the queue
    c = db(); c.execute("UPDATE jobs SET apply_status=NULL WHERE apply_status='in_progress'"); c.commit()
    return ", ".join(killed) or "any orphaned apply processes"


def tail(path, n=40) -> str:
    try:
        lines = Path(path).read_text(errors="ignore").splitlines()
    except OSError:
        return ""
    return "\n".join(
        re.sub(r"chat_id=\d+", "chat_id=***", re.sub(r"bot\d+:[\w-]+", "bot***", ln))
        for ln in lines[-n:])


def read_text_sibling(path, suffix=".txt"):
    if not path:
        return ""
    p = Path(path).with_suffix(suffix)
    return p.read_text(errors="ignore") if p.exists() else ""


def gen_answer(profile, company, question, length, prev) -> str:
    from applypilot.config import load_env
    load_env()
    from applypilot.llm import get_client
    wc = profile.get("work_context", {})
    projs = "\n".join(
        f"{i}. {p['name']}\n   What: {p.get('what','')}\n   Tools: {p.get('tools','')}\n   Impact: {p.get('impact','')}"
        for i, p in enumerate(wc.get("projects", []), 1))
    name = profile.get("personal", {}).get("full_name", "the candidate")
    sys = (
        f"You write ONE strong, truthful job-application answer for {name}, in their voice: "
        "first person, concise, specific, non-emotive, no em dashes, no 'I am passionate about', no fluff.\n\n"
        f"MY PROJECTS (each DISTINCT - never merge, use only the listed tools/impact):\n{projs}\n\n"
        f"TOOLING FACTS: {wc.get('llm_usage','')}\n"
        f"RULES: {wc.get('answer_rules','')} Truthful only; if an example is asked, pick ONE project. "
        "Match the requested length. Output the answer only.")
    user = f"Company/role: {company}\nQuestion: {question}\nDesired length: {length}"
    if prev:
        user += f"\nPrevious answer to improve (fix what's wrong): {prev}"
    return get_client().chat([{"role": "system", "content": sys}, {"role": "user", "content": user}],
                             max_tokens=600, temperature=0.5)


# ----------------------------- theme -----------------------------
def _mix(c1: str, c2: str, t: float) -> str:
    """Channel-mix two #rrggbb hexes: (1-t) of c1 + t of c2."""
    a, b = int(c1[1:], 16), int(c2[1:], 16)
    return "#" + "".join(f"{round((a >> s & 255) * (1 - t) + (b >> s & 255) * t):02x}" for s in (16, 8, 0))


def _lum(hex_: str) -> float:
    v = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    v = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in v]
    return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + .05) / (min(la, lb) + .05)


def pill(status) -> str:
    mod, label = PILLS.get(status, ("pending", str(status or "pending")))
    return f'<span class="ap-pill ap-pill--{mod}"><span class="ap-dot"></span>{html.escape(label)}</span>'


def chip(label) -> str:
    return f'<span class="ap-chip">{html.escape(label)}</span>'


def eyebrow(text) -> str:
    return f'<div class="ap-eyebrow">{html.escape(text)}</div>'


# Everything below references :root custom properties only, so the same
# stylesheet serves both modes. No blank lines (markdown would split the
# HTML block); no f-string (so braces stay plain CSS).
_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lora:ital,wght@0,400..700;1,400..700&display=swap');
.stApp { background: var(--ap-bg); font-family: 'Poppins', Arial, sans-serif; }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stHeaderActionElements"] { display: none !important; }
[data-testid="stHeader"] { background: transparent !important; }
.block-container { padding-top: 2.4rem; padding-bottom: 5rem; }
/* ---- base ink (broad, then carve out) ---- */
.stApp p, .stApp span, .stApp label, .stApp li, .stApp td, .stApp th, .stApp div { color: var(--ap-ink); }
.stApp p, .stApp span, .stApp label, .stApp li, .stApp div, .stApp button, .stApp input, .stApp textarea { font-family: 'Poppins', Arial, sans-serif; }
/* Material icons must escape the font override or they render as ligature text */
[data-testid="stIconMaterial"], [data-testid="stAlertDynamicIcon"], [data-testid="stToastDynamicIcon"], .material-icons, .material-symbols-outlined, .material-symbols-rounded, [class^="material-symbols"] {
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important; color: inherit !important;
  letter-spacing: 0 !important; font-feature-settings: 'liga' 1 !important; font-weight: 400 !important; }
/* ---- prose is Lora; widget chrome stays Poppins (ordered after) ---- */
[data-testid="stMarkdownContainer"] p { font-family: 'Lora', Georgia, serif; font-size: 15px; line-height: 1.6; }
[data-testid="stWidgetLabel"] p { font-family: 'Poppins', Arial, sans-serif !important; font-size: 12.5px !important; font-weight: 500; letter-spacing: .2px; color: var(--ap-muted) !important; }
[data-testid="stCaptionContainer"] p { font-family: 'Lora', Georgia, serif !important; font-style: italic; font-size: 12.5px !important; color: var(--ap-muted) !important; }
[data-testid="stCheckbox"] p, [data-testid="stRadio"] p { font-family: 'Poppins', Arial, sans-serif !important; font-size: 13px !important; }
.stApp h1, .stApp h2, .stApp h1 span, .stApp h2 span { font-family: 'Lora', Georgia, serif !important; font-weight: 500; color: var(--ap-ink); }
.stApp h3, .stApp h3 span { font-family: 'Lora', Georgia, serif !important; font-size: 22px !important; font-weight: 500 !important; letter-spacing: -.2px; color: var(--ap-ink) !important; }
/* ---- signature elements ---- */
.ap-eyebrow { font-family: 'Poppins', Arial, sans-serif !important; font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--ap-muted) !important; line-height: 1.2; }
.ap-masthead { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin: 2px 0 10px; flex-wrap: wrap; }
.ap-title { font-family: 'Lora', Georgia, serif !important; font-size: 32px; font-weight: 500; letter-spacing: -.3px; line-height: 1.1; color: var(--ap-ink) !important; margin-top: 4px; }
.ap-dateline { font-family: 'Lora', Georgia, serif !important; font-style: italic; font-size: 13px; color: var(--ap-muted) !important; padding-bottom: 1px; }
.ap-rule { height: 2px; background: var(--ap-rule); margin: 0 0 22px; }
.ap-stats { display: grid; grid-template-columns: repeat(8, 1fr); border-top: 1px solid var(--ap-hairline); border-bottom: 1px solid var(--ap-hairline); margin-bottom: 6px; }
.ap-stat { padding: 14px 18px; }
.ap-stat + .ap-stat { border-left: 1px solid var(--ap-hairline); }
.ap-stat-label { font-family: 'Poppins', Arial, sans-serif !important; font-size: 10.5px; font-weight: 600; letter-spacing: .11em; text-transform: uppercase; color: var(--ap-muted) !important; margin-bottom: 6px; }
.ap-stat-num { font-family: 'Lora', Georgia, serif !important; font-size: 32px; font-weight: 500; letter-spacing: -.3px; line-height: 1.1; color: var(--ap-ink) !important; font-feature-settings: "tnum"; }
.ap-stat-num.is-bad { color: var(--ap-clay-tx) !important; }
/* ---- pills / chips (fixed semantics, ignore accent picker) ---- */
.ap-pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px 4px 8px; border-radius: 4px; font-family: 'Poppins', Arial, sans-serif !important; font-size: 10.5px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; line-height: 1; white-space: nowrap; }
.ap-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.ap-pill--pending { border: 1px solid var(--ap-hairline); color: var(--ap-muted) !important; }
.ap-pill--pending .ap-dot { background: var(--ap-faint); }
.ap-pill--inprogress { background: var(--ap-blue-bg); color: var(--ap-blue-tx) !important; }
.ap-pill--inprogress .ap-dot { background: #6a9bcc; }
.ap-pill--applied { background: var(--ap-sage-bg); color: var(--ap-sage-tx) !important; }
.ap-pill--applied .ap-dot { background: #788c5d; }
.ap-pill--handoff { border: 1px solid var(--ap-sage-bd); color: var(--ap-sage-tx) !important; }
.ap-pill--handoff .ap-dot { background: #788c5d; }
.ap-pill--failed { background: var(--ap-clay-bg); color: var(--ap-clay-tx) !important; }
.ap-pill--failed .ap-dot { background: #d97757; }
.ap-pill--manual { border: 1px solid var(--ap-clay-bd); color: var(--ap-clay-tx) !important; }
.ap-pill--manual .ap-dot { background: #d97757; }
.ap-chip { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 4px; border: 1px solid var(--ap-hairline); color: var(--ap-clay-tx) !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 10.5px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; line-height: 1; white-space: nowrap; }
/* ---- job card anatomy (1.58: bordered containers carry no testid of their
   own, so cards are targeted via their st-key-card* class) ---- */
.stVerticalBlock[class*="st-key-card"] { background: var(--ap-surface) !important; border: 1px solid var(--ap-hairline) !important; border-radius: 10px !important; box-shadow: none !important; padding: 16px 18px !important; transition: border-color .15s; }
.stVerticalBlock[class*="st-key-card"]:hover { border-color: var(--ap-hairline-strong) !important; }
.ap-card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ap-score { display: inline-flex; align-items: center; justify-content: center; min-width: 26px; height: 26px; padding: 0 7px; border-radius: 4px; background: var(--ap-accent-tint); color: var(--ap-accent-tx) !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 13px; font-weight: 600; font-feature-settings: "tnum"; flex: none; }
.ap-score--hi { background: var(--ap-accent); color: var(--ap-on-accent) !important; }
.ap-company { font-family: 'Poppins', Arial, sans-serif !important; font-size: 15px; font-weight: 600; color: var(--ap-ink) !important; }
.ap-role { font-family: 'Lora', Georgia, serif !important; font-style: italic; font-size: 14.5px; color: var(--ap-ink-2) !important; }
.ap-head-right { margin-left: auto; display: inline-flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.ap-meta { font-family: 'Poppins', Arial, sans-serif !important; font-size: 12.5px; color: var(--ap-muted) !important; margin-top: 6px; }
.ap-meta * { color: var(--ap-muted) !important; }
.ap-na { font-family: 'Lora', Georgia, serif !important; font-style: italic; color: var(--ap-faint) !important; }
.ap-lead { font-family: 'Lora', Georgia, serif !important; font-style: italic; }
.ap-list-footer { border-top: 1px solid var(--ap-hairline); margin-top: 16px; padding-top: 10px; text-align: right; }
/* ---- sidebar ---- */
[data-testid="stSidebar"] { background: var(--ap-surface-2) !important; border-right: 1px solid var(--ap-hairline); }
.ap-wordmark { margin: 2px 0 10px; }
.ap-wordmark-bar { width: 28px; height: 3px; background: #d97757; margin-bottom: 10px; }
.ap-wordmark-name { font-family: 'Lora', Georgia, serif !important; font-size: 18px; font-weight: 600; color: var(--ap-ink) !important; line-height: 1.2; }
.ap-wordmark-sub { font-family: 'Lora', Georgia, serif !important; font-style: italic; font-size: 12px; color: var(--ap-muted) !important; }
.ap-sb-eyebrow { border-top: 1px solid var(--ap-hairline); padding-top: 18px; margin: 10px 0 2px; }
.ap-sb-eyebrow.first { border-top: none; padding-top: 0; margin-top: 0; }
.ap-status-line { display: block; font-family: 'Poppins', Arial, sans-serif !important; font-size: 12px; line-height: 1.5; color: var(--ap-muted) !important; margin: 2px 0 6px; }
.ap-status-line .ap-dot { display: inline-block; margin-right: 6px; vertical-align: 1px; }
.ap-status-line * { color: var(--ap-muted) !important; }
.ap-dot--on { background: #788c5d; }
.ap-dot--off { background: var(--ap-faint); }
/* ---- tabs ---- */
.stTabs [data-baseweb="tab-list"] { border-bottom: 1px solid var(--ap-hairline); gap: 22px; }
.stTabs [data-baseweb="tab"] p { font-family: 'Poppins', Arial, sans-serif !important; font-size: 12.5px !important; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--ap-muted) !important; }
.stTabs [data-baseweb="tab"]:hover p { color: var(--ap-ink) !important; }
.stTabs [aria-selected="true"] p { color: var(--ap-ink) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--ap-accent) !important; height: 2px; }
.stTabs [data-baseweb="tab-border"] { background: transparent !important; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 16px; }
/* ---- buttons: three tiers ---- */
.stButton button, .stDownloadButton button, .stLinkButton a, .stFormSubmitButton button {
  background: transparent; color: var(--ap-ink); border: 1px solid var(--ap-hairline); border-radius: 6px;
  font-family: 'Poppins', Arial, sans-serif !important; font-size: 13px; font-weight: 500; letter-spacing: .2px;
  box-shadow: none !important; transition: all .15s; }
.stButton button:hover, .stDownloadButton button:hover, .stLinkButton a:hover { background: var(--ap-surface-2); border-color: var(--ap-hairline-strong); color: var(--ap-ink); }
.stButton button p, .stDownloadButton button p, .stLinkButton a p,
.stButton button span:not([data-testid="stIconMaterial"]), .stDownloadButton button span:not([data-testid="stIconMaterial"]), .stLinkButton a span:not([data-testid="stIconMaterial"]) { color: inherit !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 13px !important; }
/* the markdown wrapper div between button and label is painted ink by the
   broad rule; force it back to inherit so tiered button colors cascade */
.stButton button div, .stDownloadButton button div, .stLinkButton a div { color: inherit !important; }
.stButton button[kind="primary"], [data-testid="stBaseButton-primary"], button[kind="primary"] { background: var(--ap-accent) !important; color: var(--ap-on-accent) !important; border: none !important; font-weight: 600; }
.stButton button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { background: var(--ap-accent-hover) !important; color: var(--ap-on-accent) !important; }
button[kind="primary"] p, button[kind="primary"] span:not([data-testid="stIconMaterial"]) { color: var(--ap-on-accent) !important; font-weight: 600; }
button[kind="primary"] [data-testid="stIconMaterial"] { color: var(--ap-on-accent) !important; }
button[kind="tertiary"], [data-testid="stBaseButton-tertiary"] { background: transparent !important; border: none !important; color: var(--ap-muted) !important; }
button[kind="tertiary"]:hover, [data-testid="stBaseButton-tertiary"]:hover { background: var(--ap-surface-2) !important; color: var(--ap-ink) !important; }
button[kind="tertiary"] p, button[kind="tertiary"] span:not([data-testid="stIconMaterial"]) { color: inherit !important; }
[class*="st-key-stop"] button:hover, [class*="st-key-stop"] button:hover p, [class*="st-key-stop"] button:hover span,
[class*="st-key-prm"] button:hover, [class*="st-key-prm"] button:hover p, [class*="st-key-prm"] button:hover span { color: var(--ap-clay-tx) !important; }
button:focus:not(:active) { box-shadow: none !important; }
button:focus-visible { outline: 2px solid var(--ap-accent); outline-offset: 1px; }
/* ---- inputs (border lives on the BaseWeb wrapper, not the inner input) ---- */
.stTextInput [data-baseweb="input"], .stTextArea [data-baseweb="textarea"], .stNumberInput [data-baseweb="input"] { background: var(--ap-surface) !important; border: 1px solid var(--ap-hairline) !important; border-radius: 6px !important; box-shadow: none !important; }
.stTextInput input, .stTextArea textarea, .stNumberInput input { background: transparent !important; color: var(--ap-ink) !important; border: none !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 14px !important; box-shadow: none !important; }
[data-baseweb="base-input"] { background: transparent !important; }
[data-baseweb="select"] > div { background: var(--ap-surface) !important; border: 1px solid var(--ap-hairline) !important; border-radius: 6px !important; box-shadow: none !important; }
.stTextInput [data-baseweb="input"]:focus-within, .stTextArea [data-baseweb="textarea"]:focus-within { border-color: var(--ap-accent) !important; box-shadow: 0 0 0 3px var(--ap-accent-ring) !important; }
[data-baseweb="select"]:focus-within > div { border-color: var(--ap-accent) !important; box-shadow: 0 0 0 3px var(--ap-accent-ring) !important; }
.stTextInput input::placeholder, .stTextArea textarea::placeholder { color: var(--ap-faint) !important; }
/* multiselect inner search input: transparent so it sits after the tags, not as a box */
[data-baseweb="select"] [data-baseweb="input"], [data-baseweb="select"] [data-baseweb="input"] > div,
[data-baseweb="select"] input { background: transparent !important; border: none !important; border-radius: 0 !important; box-shadow: none !important; min-width: 0 !important; }
[data-baseweb="tag"] { background: var(--ap-accent-tint) !important; border-radius: 4px !important; }
[data-baseweb="tag"] span, [data-baseweb="tag"] div { color: var(--ap-accent-tx) !important; font-family: 'Poppins', Arial, sans-serif !important; }
[data-baseweb="tag"] svg { fill: var(--ap-accent-tx) !important; }
/* dropdown menus portal outside .stApp; :root vars still resolve there */
div[data-baseweb="popover"] [data-baseweb="menu"], div[data-baseweb="popover"] ul { background: var(--ap-surface) !important; border: 1px solid var(--ap-hairline); border-radius: 6px; box-shadow: none !important; }
div[data-baseweb="popover"] li { color: var(--ap-ink) !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 13px !important; }
div[data-baseweb="popover"] li * { color: var(--ap-ink) !important; }
div[data-baseweb="popover"] li:hover, div[data-baseweb="popover"] li[aria-selected="true"] { background: var(--ap-surface-2) !important; }
/* ---- slider ---- */
[data-baseweb="slider"] [role="slider"] { background: var(--ap-accent) !important; box-shadow: none !important; border: none !important; }
[data-testid="stSliderThumbValue"], [data-testid="stSliderThumbValue"] * { color: var(--ap-accent-tx) !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 11px !important; padding-bottom: 2px; }
[data-testid="stSliderTickBar"] [data-testid="stMarkdownContainer"] p { color: var(--ap-faint) !important; font-family: 'Poppins', Arial, sans-serif !important; font-size: 11px !important; }
/* ---- expanders ---- */
[data-testid="stExpander"] { background: transparent; border: none; box-shadow: none !important; }
[data-testid="stExpander"] details { background: var(--ap-surface) !important; border: 1px solid var(--ap-hairline) !important; border-radius: 10px !important; }
[data-testid="stExpander"] summary { font-family: 'Poppins', Arial, sans-serif !important; }
[data-testid="stExpander"] summary p { font-family: 'Poppins', Arial, sans-serif !important; font-size: 12.5px !important; font-weight: 500; color: var(--ap-muted) !important; }
[data-testid="stExpander"] summary:hover p { color: var(--ap-ink) !important; }
[class*="st-key-card"] [data-testid="stExpander"] { border: none !important; border-top: 1px solid var(--ap-hairline) !important; border-radius: 0 !important; background: transparent !important; }
[class*="st-key-card"] [data-testid="stExpander"] details { background: transparent !important; border: none !important; border-radius: 0 !important; }
/* ---- alerts: flat tinted strips with a 3px rail (fixed semantics) ---- */
[data-testid="stAlert"] { border: none !important; border-left: 3px solid var(--ap-hairline) !important; border-radius: 4px !important; box-shadow: none !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) { background: var(--ap-clay-bg) !important; border-left-color: #d97757 !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) * { color: var(--ap-clay-tx) !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) { background: var(--ap-clay-bg) !important; border-left-color: #d97757 !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) * { color: var(--ap-clay-tx) !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) { background: var(--ap-blue-bg) !important; border-left-color: #6a9bcc !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) * { color: var(--ap-blue-tx) !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) { background: var(--ap-sage-bg) !important; border-left-color: #788c5d !important; }
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) * { color: var(--ap-sage-tx) !important; }
[data-testid="stAlert"] p { font-family: 'Poppins', Arial, sans-serif !important; font-size: 13.5px !important; }
/* ---- code / log wells (Poppins per the two-typeface rule) ---- */
[data-testid="stCode"] pre, .stApp pre { background: var(--ap-surface-2) !important; border: none !important; border-radius: 6px !important; max-height: 340px; overflow: auto; }
[data-testid="stCode"] code, .stApp code { font-family: 'Poppins', Arial, sans-serif !important; font-size: 12px !important; line-height: 1.55; color: var(--ap-ink) !important; background: transparent !important; }
/* answer textarea reads as prose */
textarea[aria-label="Answer (copy this)"] { font-family: 'Lora', Georgia, serif !important; font-size: 15px !important; line-height: 1.6 !important; }
/* ---- charts ---- */
[data-testid="stVegaLiteChart"] { background: transparent !important; }
hr { border: none !important; border-top: 1px solid var(--ap-hairline) !important; margin: 24px 0 !important; }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: var(--ap-hairline); border-radius: 6px; }
::-webkit-scrollbar-thumb:hover { background: var(--ap-hairline-strong); }
/* terminal icon assertion: must outrank every font rule above (icons render
   icon names as ligatures; any other font shows them as raw text) */
.stApp span[data-testid="stIconMaterial"], span[data-testid="stIconMaterial"], [data-testid="stSidebar"] span[data-testid="stIconMaterial"],
.stApp span[data-testid="stAlertDynamicIcon"], span[data-testid="stAlertDynamicIcon"], span[data-testid="stToastDynamicIcon"] {
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
  letter-spacing: 0 !important; font-feature-settings: 'liga' 1 !important; font-weight: 400 !important; }
"""


def apply_theme(dark: bool, accent: str) -> dict:
    if dark:
        T = {"bg": "#141413", "surface": "#1e1d1b", "surface-2": "#242320", "ink": "#faf9f5",
             "ink-2": "#d9d7ce", "muted": "#b0aea5", "faint": "#87857c", "hairline": "#2c2b26",
             "hairline-strong": "rgba(250,249,245,.18)", "rule": "#faf9f5",
             "sage-bg": "#2a2e23", "sage-tx": "#9fad8b", "sage-bd": "#414a34",
             "blue-bg": "#27323c", "blue-tx": "#95b7d8",
             "clay-bg": "#3f2a22", "clay-tx": "#e39e86", "clay-bd": "#6d4132"}
        tint = 0.78
    else:
        T = {"bg": "#faf9f5", "surface": "#ffffff", "surface-2": "#f0eee6", "ink": "#141413",
             "ink-2": "#3f3e3a", "muted": "#6f6d66", "faint": "#b0aea5", "hairline": "#e8e6dc",
             "hairline-strong": "rgba(20,20,19,.16)", "rule": "#141413",
             "sage-bg": "#e6e9de", "sage-tx": "#505c3f", "sage-bd": "#c0c8b1",
             "blue-bg": "#e4ebef", "blue-tx": "#486582",
             "clay-bg": "#f5e6dd", "clay-tx": "#8a4f3c", "clay-bd": "#ebbfae"}
        tint = 0.85
    T["accent"] = accent
    # ink on accent where it has the better contrast (true for all three brand
    # accents); ivory only for dark Custom picks
    T["on-accent"] = "#141413" if _contrast("#141413", accent) >= _contrast("#faf9f5", accent) else "#faf9f5"
    T["accent-tx"] = _mix(accent, T["ink"], 0.30)
    T["accent-tint"] = _mix(accent, T["bg"], tint)
    T["accent-hover"] = _mix(accent, "#141413", 0.08)
    r, g, b = (int(accent[i:i + 2], 16) for i in (1, 3, 5))
    T["accent-ring"] = f"rgba({r},{g},{b},.20)"
    css_vars = ";".join(f"--ap-{k}:{v}" for k, v in T.items())
    st.markdown("<style>:root{" + css_vars + "}" + _CSS + "</style>", unsafe_allow_html=True)
    return T


# ----------------------------- sidebar -----------------------------
env = read_env()
prefs = load_prefs()

st.sidebar.markdown(
    '<div class="ap-wordmark"><div class="ap-wordmark-bar"></div>'
    '<div class="ap-wordmark-name">ApplyPilot</div>'
    '<div class="ap-wordmark-sub">Application autopilot</div></div>', unsafe_allow_html=True)

st.sidebar.markdown('<div class="ap-eyebrow ap-sb-eyebrow first">Appearance</div>', unsafe_allow_html=True)
dark = st.sidebar.toggle("Dark mode", value=prefs.get("dark", False))
_names = list(ACCENTS) + ["Custom"]
_default_name = prefs.get("accent_name", "Clay")
accent_name = st.sidebar.selectbox("Accent", _names,
                                   index=_names.index(_default_name) if _default_name in _names else 0)
if accent_name == "Custom":
    accent = st.sidebar.color_picker("Custom color", prefs.get("custom_accent", "#d97757"))
else:
    accent = ACCENTS[accent_name]
save_prefs({"dark": dark, "accent_name": accent_name, "custom_accent": accent if accent_name == "Custom" else prefs.get("custom_accent", "#d97757")})
T = apply_theme(dark, accent)

st.sidebar.markdown('<div class="ap-eyebrow ap-sb-eyebrow">Run settings</div>', unsafe_allow_html=True)
model = st.sidebar.radio("Model", ["sonnet", "haiku", "opus"], index=0, horizontal=True)
supervised = st.sidebar.checkbox("Supervised (you Submit)", value=env.get("APPLYPILOT_SUPERVISED", "1") == "1")
resume_mode = st.sidebar.radio(
    "Résumé mode", ["Tailored per job", "Fixed master résumé"],
    index=1 if env.get("APPLYPILOT_FIXED_RESUME", "0") == "1" else 0,
    help="Tailored: an LLM rewrites your résumé for each job. Fixed: every application "
         "uploads the one résumé at ~/.applypilot/master_resume.pdf and only cover "
         "letters are generated per job.")
if resume_mode == "Fixed master résumé" and not (APP / "master_resume.pdf").exists():
    st.sidebar.warning("master_resume.pdf not found — copy your résumé PDF to "
                       "~/.applypilot/master_resume.pdf", icon=":material/description:")
SALARY_MODES = {"Match the posting": "posting", "Leave blank / negotiable": "blank",
                "Fixed amount": "fixed"}
_cur_sm = env.get("APPLYPILOT_SALARY_MODE", "posting")
_sm_label = next((k for k, v in SALARY_MODES.items() if v == _cur_sm), "Match the posting")
salary_mode = st.sidebar.selectbox(
    "Salary answers", list(SALARY_MODES), index=list(SALARY_MODES).index(_sm_label),
    help="How the agent answers salary questions: mirror the posting's own numbers, "
         "leave fields blank ('Negotiable' where text is required), or always give "
         "your fixed amount.")
salary_fixed = env.get("APPLYPILOT_SALARY_FIXED", "")
if SALARY_MODES[salary_mode] == "fixed":
    salary_fixed = st.sidebar.text_input("Fixed amount", value=salary_fixed,
                                         placeholder="e.g. 145000 or 140000-160000")
if st.sidebar.button("Save run settings", icon=":material/save:"):
    write_env({"APPLYPILOT_SUPERVISED": "1" if supervised else "0",
               "APPLYPILOT_FIXED_RESUME": "1" if resume_mode == "Fixed master résumé" else "0",
               "APPLYPILOT_SALARY_MODE": SALARY_MODES[salary_mode],
               "APPLYPILOT_SALARY_FIXED": salary_fixed.strip()})
    st.sidebar.success("Saved", icon=":material/check:")
_tg_on = bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"))
st.sidebar.markdown(
    f'<div class="ap-status-line"><span class="ap-dot {"ap-dot--on" if _tg_on else "ap-dot--off"}"></span>'
    f'{"Telegram connected" if _tg_on else "Telegram off"}</div>'
    f'<div class="ap-status-line">Model {html.escape(env.get("LLM_MODEL", "?"))}</div>',
    unsafe_allow_html=True)
if st.sidebar.button("Stop apply run", icon=":material/stop_circle:", type="tertiary", key="stop_all",
                     help="Force-kill any running apply + agent + its Chrome"):
    stop_run(); st.sidebar.success("Stopped all apply processes.", icon=":material/check:")

st.sidebar.markdown('<div class="ap-eyebrow ap-sb-eyebrow">Filters &amp; sort</div>', unsafe_allow_html=True)
min_score = st.sidebar.slider("Min fit score", 1, 10, 8)
min_salary_k = st.sidebar.slider("Min salary ($k)", 0, 250, 0, step=10)
include_no_salary = st.sidebar.checkbox("Include jobs with no listed salary", value=True)
search = st.sidebar.text_input("Search company / title")
sort_by = st.sidebar.selectbox("Sort by", ["Score (high to low)", "Salary (high to low)",
                                           "Company (A to Z)", "Location (A to Z)", "Most recent"])
hide_flagged = st.sidebar.checkbox("Hide staffing / marketplace / low-comp", value=True)
show_applied = st.sidebar.checkbox("Show applied / handed-off", value=False)
only_tailored = st.sidebar.checkbox("Only docs-ready (incl. cover letter)", value=False,
                                    help="Show only jobs whose documents are ready: a résumé is linked or tailored "
                                         "and a cover letter has been generated.")
_src_rows = db().execute("SELECT site, COUNT(*) n FROM jobs WHERE fit_score IS NOT NULL GROUP BY site ORDER BY n DESC").fetchall()
_src_options = [r["site"] for r in _src_rows if r["site"]]
_default_src = [s for s in _src_options if s in ("linkedin", "indeed")] or _src_options
sources = st.sidebar.multiselect("Source", _src_options, default=_default_src,
                                 help="Empty = all sources. Workday/career-site sources appear once those jobs are scored.")

# ----------------------------- masthead + stats band -----------------------------
c = counts()
_now = datetime.now()
_dateline = f"{_now:%A} &middot; {_now:%B} {_now.day}, {_now:%Y}"
st.markdown(
    f'<div class="ap-masthead"><div><div class="ap-eyebrow">Application campaign</div>'
    f'<div class="ap-title">ApplyPilot</div></div>'
    f'<div class="ap-dateline">{_dateline} &mdash; {c["total"]:,} discovered &middot; {c["applied"]} applied</div></div>'
    f'<div class="ap-rule"></div>', unsafe_allow_html=True)

_cells = []
for label, key in [("Discovered", "total"), ("Scored", "scored"), ("Strong &ge;7", "ge7"),
                   ("Tailored", "tailored"), ("Applied", "applied"),
                   ("Handed off", "handoff"), ("Failed", "failed")]:
    bad = " is-bad" if key == "failed" and c[key] > 0 else ""
    _cells.append(f'<div class="ap-stat"><div class="ap-stat-label">{label}</div>'
                  f'<div class="ap-stat-num{bad}">{c[key]:,}</div></div>')
_spend = llm_spend()
_cells.append(f'<div class="ap-stat"><div class="ap-stat-label">Est. spend</div>'
              f'<div class="ap-stat-num">${_spend["cost"]:,.2f}</div></div>')
st.markdown(f'<div class="ap-stats">{"".join(_cells)}</div>', unsafe_allow_html=True)

tab_q, tab_s, tab_a = st.tabs(["Queue", "Stats", "Answers & Projects"])


def themed_bar(df, x, y, x_type="ordinal", label_angle=0):
    # theme=None: Streamlit's default "streamlit" vega theme would override
    # the fonts/colors below (charts are canvas, CSS can't reach them)
    st.vega_lite_chart(df, {
        "mark": {"type": "bar", "color": T["accent"], "cornerRadiusEnd": 3},
        "encoding": {
            "x": {"field": x, "type": x_type, "sort": None,
                  "axis": {"title": None, "labelAngle": label_angle}},
            "y": {"field": y, "type": "quantitative", "axis": {"title": None}},
        },
        "config": {
            "background": "transparent",
            "axis": {"labelColor": T["muted"], "titleColor": T["muted"], "gridColor": T["hairline"],
                     "domainColor": T["hairline"], "tickColor": T["hairline"],
                     "labelFont": "Poppins", "titleFont": "Poppins", "labelFontSize": 11},
            "view": {"stroke": None},
        },
    }, theme=None, width="stretch")


# ============================ QUEUE TAB ============================
with tab_q:
    active = json.loads(ACTIVE_FILE.read_text()) if ACTIVE_FILE.exists() else None
    auto = False
    if active:
        st.markdown(eyebrow("Live run"), unsafe_allow_html=True)
        log_text = tail(active["log"])
        done = "Done:" in log_text
        needs_you = ("pinged you" in log_text or "NEEDHUMAN" in log_text) and not done
        row = db().execute("SELECT apply_status FROM jobs WHERE url=?", (active["url"],)).fetchone()
        status = row["apply_status"] if row else "?"
        if needs_you:
            st.error(f"Action needed — {active['company']}: solve the CAPTCHA, then review and Submit in Chrome.",
                     icon=":material/notification_important:")
        elif done:
            st.success(f"Run finished — {active['company']}", icon=":material/check_circle:")
            st.markdown(pill(status), unsafe_allow_html=True)
        else:
            st.info(f"Working on {active['company']} · model {active.get('model', '?')}",
                    icon=":material/autorenew:")
        with st.expander("Live log", expanded=needs_you):
            st.code(log_text or "(starting…)", language="text")
        bc = st.columns(5)
        if bc[0].button("Refresh", icon=":material/refresh:"):
            st.rerun()
        if bc[1].button("Stop run", icon=":material/stop_circle:", type="tertiary", key="stop_run"):
            stop_run(); st.rerun()
        if done and bc[2].button("Mark applied", icon=":material/check:"):
            mark_applied(active["url"]); ACTIVE_FILE.unlink(missing_ok=True); st.rerun()
        if bc[3].button("Clear", icon=":material/clear_all:", type="tertiary"):
            ACTIVE_FILE.unlink(missing_ok=True); st.rerun()
        auto = bc[4].checkbox("Auto-refresh (4s)", value=not done)
    else:
        st.markdown(eyebrow("Live run"), unsafe_allow_html=True)
        st.caption("No run active — use Apply on any card below.")

    st.divider()
    sql = "SELECT * FROM jobs WHERE fit_score >= ?"
    params = [min_score]
    if sources:
        sql += f" AND site IN ({','.join('?' for _ in sources)})"
        params += sources
    if not show_applied:
        # in_progress stays visible (blue pill) -- a crashed run must never
        # make a job silently vanish from the queue
        sql += " AND (apply_status IS NULL OR apply_status IN ('failed', 'in_progress'))"
    if only_tailored:
        sql += " AND tailored_resume_path IS NOT NULL AND cover_letter_path IS NOT NULL"
    rows = [dict(r) for r in db().execute(sql, params).fetchall()]
    sort_key = {
        "Score (high to low)": lambda r: -(r["fit_score"] or 0),
        "Salary (high to low)": lambda r: -salary_num(r["salary"]),
        "Company (A to Z)": lambda r: (r["company"] or "").lower(),
        "Location (A to Z)": lambda r: (r["location"] or "").lower(),
        "Most recent": lambda r: r["discovered_at"] or "",
    }[sort_by]
    rows.sort(key=sort_key, reverse=(sort_by == "Most recent"))

    hidden = load_hidden()
    shown = 0
    for r in rows:
        if r["url"] in hidden:
            continue
        if search and search.lower() not in ((r["company"] or "") + (r["title"] or "")).lower():
            continue
        sn = salary_num(r["salary"])
        if not include_no_salary and sn == 0:
            continue  # hide jobs with no parseable salary, regardless of the floor
        if min_salary_k > 0 and sn > 0 and sn < min_salary_k * 1000:
            continue  # below the salary floor (only applies to jobs that list one)
        flags = job_flags(r)
        if hide_flagged and flags:
            continue
        shown += 1
        with st.container(border=True, key=f"card{shown}"):
            comp = html.escape(r["company"] or "?")
            title = html.escape(r["title"] or "")
            hi = " ap-score--hi" if (r["fit_score"] or 0) >= 9 else ""
            meta_parts = [html.escape(r["salary"]) if r["salary"] else '<span class="ap-na">Salary n/a</span>']
            if r["location"]:
                meta_parts.append(html.escape(r["location"]))
            if r["site"]:
                meta_parts.append(html.escape(r["site"]))
            st.markdown(
                f'<div class="ap-card-head"><span class="ap-score{hi}">{r["fit_score"]}</span>'
                f'<span class="ap-company">{comp}</span><span class="ap-role">&ndash; {title}</span>'
                f'<span class="ap-head-right">{pill(r["apply_status"])}{"".join(chip(f) for f in flags)}</span></div>'
                f'<div class="ap-meta">{" &middot; ".join(meta_parts)}</div>', unsafe_allow_html=True)

            rp = read_text_sibling(r["tailored_resume_path"])
            cp = read_text_sibling(r["cover_letter_path"])
            rpdf = Path(r["tailored_resume_path"]).with_suffix(".pdf") if r["tailored_resume_path"] else None
            cpdf = Path(r["cover_letter_path"]).with_suffix(".pdf") if r["cover_letter_path"] else None

            a = st.columns([2, 2, 2, 2, 2, 4])
            a[0].link_button("Open", r["application_url"] or r["url"], icon=":material/open_in_new:")
            if rpdf and rpdf.exists():
                a[1].download_button("Résumé", rpdf.read_bytes(), file_name="resume.pdf",
                                     icon=":material/description:", key=f"r{shown}")
            if cpdf and cpdf.exists():
                a[2].download_button("Cover", cpdf.read_bytes(), file_name="cover.pdf",
                                     icon=":material/mail:", key=f"c{shown}")
            if a[3].button("Apply", type="primary", icon=":material/play_arrow:", key=f"go{shown}"):
                write_env({"APPLYPILOT_SUPERVISED": "1" if supervised else "0"})
                launch_apply(r["url"], r["company"] or "job", model); st.rerun()
            if a[4].button("Hide", type="tertiary", icon=":material/visibility_off:", key=f"h{shown}"):
                hidden.add(r["url"]); save_hidden(hidden); st.rerun()
            # handed-off = filled by the agent, submitted by you in the open
            # browser; confirm it here to move it to applied
            if r["apply_status"] in ("handoff", "failed"):
                if a[5].button("Mark applied", icon=":material/check:", key=f"ma{shown}",
                               help="Confirm you submitted this one yourself."):
                    mark_applied(r["url"]); st.rerun()

            with st.expander("Preview — résumé · cover · score"):
                if r["score_reasoning"]:
                    st.markdown(f'<span class="ap-lead">Why this score:</span> {html.escape(r["score_reasoning"])}',
                                unsafe_allow_html=True)
                if rp:
                    st.markdown("**Tailored résumé**"); st.code(rp[:2500], language="text")
                if cp:
                    st.markdown("**Cover letter**"); st.code(cp[:1500], language="text")
    st.markdown(f'<div class="ap-list-footer"><span class="ap-eyebrow">Showing {shown} roles</span></div>',
                unsafe_allow_html=True)
    if auto and active:
        time.sleep(4); st.rerun()

# ============================ STATS TAB ============================
with tab_s:
    allrows = [dict(r) for r in db().execute("SELECT * FROM jobs").fetchall()]
    perday = Counter()
    for r in allrows:
        ts = r["applied_at"] or (r["last_attempted_at"] if r["apply_status"] == "handoff" else None)
        if r["apply_status"] in ("applied", "handoff") and ts:
            perday[ts[:10]] += 1
    st.markdown(eyebrow("Per day"), unsafe_allow_html=True)
    st.subheader("Applications")
    if perday:
        df = pd.DataFrame(sorted(perday.items()), columns=["date", "applications"])
        themed_bar(df, "date", "applications", x_type="ordinal", label_angle=-40)
    else:
        st.caption("No applications yet.")

    st.markdown(eyebrow("Spend"), unsafe_allow_html=True)
    st.subheader("LLM cost (estimated)")
    sp = llm_spend()
    if sp["calls"]:
        st.markdown(f"**${sp['cost']:,.2f}** lifetime &middot; ${sp['today']:,.2f} today &middot; "
                    f"{sp['calls']:,} calls &middot; {sp['tok_in']:,} tokens in / {sp['tok_out']:,} out",
                    unsafe_allow_html=True)
        for m, v in sorted(sp["by_model"].items(), key=lambda kv: -kv[1]["cost"]):
            st.caption(f"{m} — ${v['cost']:,.2f} ({v['calls']:,} calls · "
                       f"{v['in']:,} in / {v['out']:,} out)")
    else:
        st.caption("No usage recorded yet — tracking starts with your next pipeline run.")
    st.caption("Estimates from the PRICES table in dashboard.py — edit it to match your "
               "provider's billing. Apply runs use your Claude subscription (no per-run API cost).")

    ccol, scol = st.columns(2)
    with ccol:
        st.markdown(eyebrow("Status"), unsafe_allow_html=True)
        st.subheader("Breakdown (score ≥ 7)")
        _nice = {"pending": "Pending", "in_progress": "In progress", "applied": "Applied",
                 "handoff": "Handed off", "failed": "Failed", "manual": "Manual ATS"}
        sb = Counter(_nice.get(r["apply_status"] or "pending", r["apply_status"] or "pending")
                     for r in allrows if r["fit_score"] and r["fit_score"] >= 7)
        if sb:
            themed_bar(pd.DataFrame(sb.items(), columns=["status", "count"]), "status", "count", x_type="nominal")
    with scol:
        st.markdown(eyebrow("Scores"), unsafe_allow_html=True)
        st.subheader("Distribution — board jobs")
        sd = Counter(r["fit_score"] for r in allrows if r["fit_score"] and r["site"] in ("linkedin", "indeed"))
        if sd:
            themed_bar(pd.DataFrame(sorted(sd.items()), columns=["score", "count"]), "score", "count")

# ===================== ANSWERS & PROJECTS TAB =====================
with tab_a:
    profile = load_profile()
    if not profile:
        st.markdown(eyebrow("Setup"), unsafe_allow_html=True)
        st.caption("No profile yet — run `applypilot init` in a terminal to create "
                   "~/.applypilot/profile.json, then reload this page.")
    st.markdown(eyebrow("Generator"), unsafe_allow_html=True)
    st.subheader("Draft an answer")
    st.caption("Generate a strong, truthful answer to any application question, from your projects.")
    ac1, ac2 = st.columns(2)
    g_company = ac1.text_input("Company / role", key="g_company")
    g_len = ac2.text_input("Length", value="2-3 sentences", key="g_len")
    g_q = st.text_area("Question", key="g_q", height=80)
    g_prev = st.text_area("Previous answer to improve (optional)", key="g_prev", height=80)
    if st.button("Generate answer", type="primary"):
        if g_q.strip():
            with st.spinner("Drafting…"):
                try:
                    st.session_state["g_out"] = gen_answer(profile, g_company, g_q, g_len, g_prev)
                except Exception as e:
                    st.session_state["g_out"] = f"(error: {e})"
        else:
            st.warning("Enter a question.", icon=":material/edit_note:")
    if st.session_state.get("g_out"):
        st.text_area("Answer (copy this)", value=st.session_state["g_out"], height=160, key="g_result")

    st.divider()
    st.markdown(eyebrow("Work context"), unsafe_allow_html=True)
    st.subheader("Projects")
    st.caption("Each project the agent draws on for open-ended answers. Edit, save, and future answers use it.")
    wc = profile.get("work_context", {})
    if "projects_edit" not in st.session_state:
        st.session_state["projects_edit"] = [dict(p) for p in wc.get("projects", [])]
    projs = st.session_state["projects_edit"]
    remove_idx = None
    for i, p in enumerate(projs):
        with st.expander(p.get("name", f"Project {i+1}") or f"Project {i+1}"):
            p["name"] = st.text_input("Name", p.get("name", ""), key=f"pn{i}")
            p["what"] = st.text_area("What", p.get("what", ""), key=f"pw{i}", height=70)
            p["tools"] = st.text_input("Tools", p.get("tools", ""), key=f"pt{i}")
            p["impact"] = st.text_area("Impact", p.get("impact", ""), key=f"pi{i}", height=70)
            if st.button("Remove project", icon=":material/delete:", type="tertiary", key=f"prm{i}"):
                remove_idx = i
    if remove_idx is not None:
        projs.pop(remove_idx); st.rerun()
    if st.button("Add project", icon=":material/add:"):
        projs.append({"name": "", "what": "", "tools": "", "impact": ""}); st.rerun()

    llm_usage = st.text_area("LLM / tooling facts", wc.get("llm_usage", ""), height=80)
    answer_rules = st.text_area("Answer rules", wc.get("answer_rules", ""), height=80)
    if st.button("Save projects & rules", type="primary", icon=":material/save:"):
        profile.setdefault("work_context", {})
        profile["work_context"]["projects"] = [p for p in projs if p.get("name")]
        profile["work_context"]["llm_usage"] = llm_usage
        profile["work_context"]["answer_rules"] = answer_rules
        save_profile(profile)
        st.success("Saved to profile.json — future apply runs use this.", icon=":material/check:")
