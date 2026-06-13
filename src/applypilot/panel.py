"""Shared control-panel logic for the Streamlit GUI and FastAPI app."""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

APP = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))
DB = APP / "applypilot.db"
LOGDIR = APP / "logs"
ENV = APP / ".env"
PROFILE = APP / "profile.json"
HIDDEN_FILE = APP / "gui_hidden.json"
ACTIVE_FILE = APP / "gui_active_run.json"
RUNS_FILE = APP / "gui_runs.json"
PREFS_FILE = APP / "gui_prefs.json"

STAFFING = ["robert half", "fitt talent", "why hiring", "thecorporate", "crossing hurdles", "recruit", "staffing"]
MARKETPLACE = ["turing", "toptal", "upwork", "mercor", "fiverr", "gun.io"]

USAGE_FILE = APP / "llm_usage.jsonl"
# $ per 1M tokens (input, output). Estimates - edit to match your provider's
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


def paths() -> dict[str, Path]:
    """Return ApplyPilot panel paths for the current environment."""
    app = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))
    return {
        "APP": app,
        "DB": app / "applypilot.db",
        "LOGDIR": app / "logs",
        "ENV": app / ".env",
        "PROFILE": app / "profile.json",
        "HIDDEN_FILE": app / "gui_hidden.json",
        "ACTIVE_FILE": app / "gui_active_run.json",
        "RUNS_FILE": app / "gui_runs.json",
        "PREFS_FILE": app / "gui_prefs.json",
        "USAGE_FILE": app / "llm_usage.jsonl",
    }


def _path(name: str) -> Path:
    return paths()[name]


# ----------------------------- data helpers -----------------------------
def db():
    c = sqlite3.connect(str(_path("DB")))
    c.row_factory = sqlite3.Row
    return c


def read_env() -> dict:
    d = {}
    env = _path("ENV")
    if env.exists():
        for ln in env.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k] = v
    return d


def write_env(updates: dict):
    d = read_env()
    for k, v in updates.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    env = _path("ENV")
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text("\n".join(["# ApplyPilot configuration"] + [f"{k}={v}" for k, v in d.items()]) + "\n")
    try:
        os.chmod(env, 0o600)
    except OSError:
        pass


def load_prefs() -> dict:
    prefs = _path("PREFS_FILE")
    return json.loads(prefs.read_text()) if prefs.exists() else {}


def save_prefs(p):
    prefs = _path("PREFS_FILE")
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text(json.dumps(p))


def load_hidden() -> set:
    hidden = _path("HIDDEN_FILE")
    return set(json.loads(hidden.read_text())) if hidden.exists() else set()


def save_hidden(s):
    hidden = _path("HIDDEN_FILE")
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text(json.dumps(sorted(s)))


def load_profile() -> dict:
    profile = _path("PROFILE")
    return json.loads(profile.read_text()) if profile.exists() else {}


def save_profile(p):
    profile = _path("PROFILE")
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps(p, indent=2))


def counts() -> dict:
    c = db(); q = lambda s: c.execute(s).fetchone()[0]
    try:
        return {k: q(v) for k, v in {
            "total": "SELECT COUNT(*) FROM jobs",
            "scored": "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL",
            "ge7": "SELECT COUNT(*) FROM jobs WHERE fit_score>=7",
            "tailored": "SELECT COUNT(*) FROM jobs WHERE tailored_resume_path IS NOT NULL",
            "applied": "SELECT COUNT(*) FROM jobs WHERE apply_status='applied'",
            "handoff": "SELECT COUNT(*) FROM jobs WHERE apply_status='handoff'",
            "failed": "SELECT COUNT(*) FROM jobs WHERE apply_status='failed'",
        }.items()}
    finally:
        c.close()


def llm_spend() -> dict:
    """Aggregate llm_usage.jsonl into estimated cost (lifetime + today, by model)."""
    out = {"cost": 0.0, "today": 0.0, "tok_in": 0, "tok_out": 0, "calls": 0, "by_model": {}}
    usage_file = _path("USAGE_FILE")
    if not usage_file.exists():
        return out
    today = datetime.now(timezone.utc).date().isoformat()  # usage is logged in UTC
    for ln in usage_file.read_text(errors="ignore").splitlines():
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


def job_flags(r) -> list[str]:
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
    c = db()
    try:
        c.execute("UPDATE jobs SET apply_status='applied', applied_at=? WHERE url=?",
                  (datetime.now().isoformat(), url))
        c.commit()
    finally:
        c.close()


def reset_job(url):
    c = db()
    try:
        c.execute("UPDATE jobs SET apply_status=NULL, apply_error=NULL, apply_attempts=0, applied_at=NULL WHERE url=?",
                  (url,))
        c.commit()
    finally:
        c.close()


def launch_apply(url, company, model):
    logdir = _path("LOGDIR")
    logdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    logf = logdir / f"gui_apply_{ts}.log"
    reset_job(url)
    # 0600: agent stderr can include URLs carrying the Telegram bot token
    fh = open(os.open(str(logf), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w")
    # -m applypilot resolves inside this interpreter's env even when the venv
    # isn't on PATH (e.g. launched via .venv/bin/applypilot gui)
    p = subprocess.Popen([sys.executable, "-m", "applypilot", "apply", "--url", url, "--model", model],
                         stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    _path("ACTIVE_FILE").write_text(json.dumps(
        {"url": url, "company": company, "log": str(logf), "pid": p.pid, "started": ts, "model": model}))


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def _parse_run_started(started: str | None) -> datetime | None:
    if not started:
        return None
    for fmt in ("%Y%m%d_%H%M%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(started[:19], fmt)
        except ValueError:
            pass
    return None


def _save_runs(runs: dict) -> None:
    runs_file = _path("RUNS_FILE")
    runs_file.parent.mkdir(parents=True, exist_ok=True)
    runs_file.write_text(json.dumps(runs, indent=2, sort_keys=True))


def load_runs(prune: bool = True, now: datetime | None = None) -> dict:
    """Load GUI run registry, optionally pruning dead entries after grace."""
    runs_file = _path("RUNS_FILE")
    if not runs_file.exists():
        return {}
    try:
        raw = json.loads(runs_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    runs = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if not prune:
        return runs

    now_dt = now or datetime.now()
    kept = {}
    changed = False
    for run_id, run in runs.items():
        if _pid_alive(run.get("pid")):
            kept[run_id] = run
            continue
        started = _parse_run_started(run.get("started"))
        if started is not None and now_dt.tzinfo is not None and started.tzinfo is None:
            started = started.replace(tzinfo=now_dt.tzinfo)
        if started is None or (now_dt - started).total_seconds() < 600:
            kept[run_id] = run
        else:
            changed = True
    if changed:
        _save_runs(kept)
    return kept


def max_gui_runs() -> int:
    try:
        return max(1, int(os.environ.get("APPLYPILOT_MAX_RUNS", "3")))
    except ValueError:
        return 3


def alloc_slot(max_slots: int) -> int | None:
    live_slots = {
        int(run.get("worker_slot", 0))
        for run in load_runs().values()
        if _pid_alive(run.get("pid"))
    }
    for slot in range(max_slots):
        if slot not in live_slots:
            return slot
    return None


def launch_apply_slot(url, company, model, slot: int) -> str:
    logdir = _path("LOGDIR")
    logdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{ts}_w{slot}"
    logf = logdir / f"gui_apply_{ts}_w{slot}.log"
    reset_job(url)
    # 0600: agent stderr can include URLs carrying the Telegram bot token
    fh = open(os.open(str(logf), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w")
    p = subprocess.Popen(
        [
            sys.executable, "-m", "applypilot", "apply",
            "--url", url, "--model", model, "--worker-slot", str(slot),
        ],
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    run = {
        "url": url,
        "company": company,
        "model": model,
        "pid": p.pid,
        "worker_slot": slot,
        "log": str(logf),
        "started": ts,
    }
    runs = load_runs(prune=False)
    runs[run_id] = run
    _save_runs(runs)
    if slot == 0:
        _path("ACTIVE_FILE").write_text(json.dumps(
            {"url": url, "company": company, "log": str(logf), "pid": p.pid, "started": ts, "model": model}))
    return run_id


def _terminate_process_group(pid) -> bool:
    try:
        pgid = os.getpgid(int(pid))
        os.killpg(pgid, signal.SIGTERM)
        time.sleep(1)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        return True
    except (OSError, ValueError, TypeError):
        return False


def _kill_worker_port(worker_slot: int) -> None:
    from applypilot.apply.chrome import BASE_CDP_PORT, _kill_on_port, is_port_detached

    port = BASE_CDP_PORT + int(worker_slot)
    if not is_port_detached(port):
        _kill_on_port(port)


def stop_run_id(run_id: str) -> bool:
    runs = load_runs(prune=False)
    run = runs.get(run_id)
    if not run:
        return False

    if run.get("pid"):
        _terminate_process_group(run.get("pid"))
    _kill_worker_port(int(run.get("worker_slot", 0)))

    runs.pop(run_id, None)
    _save_runs(runs)

    active_file = _path("ACTIVE_FILE")
    if active_file.exists():
        try:
            active = json.loads(active_file.read_text())
        except (OSError, json.JSONDecodeError):
            active = {}
        if active.get("url") == run.get("url"):
            active_file.unlink(missing_ok=True)
    return True


def stop_run() -> str:
    """Kill the apply run: its process group (launched detached) + the agent
    subprocess + the Chrome it's driving. Works even if the marker was cleared."""
    killed = []
    active_file = _path("ACTIVE_FILE")
    # tracked process group (apply was started with start_new_session=True)
    if active_file.exists():
        try:
            pid = json.loads(active_file.read_text()).get("pid")
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
    active_file.unlink(missing_ok=True)
    # the explicit nuke also forgets handed-off browsers (they're fair game here)
    (_path("APP") / "detached_ports.json").unlink(missing_ok=True)
    # nothing is running anymore, so any in_progress lock is stale -- release
    # them or the locked jobs stay invisible in the queue
    c = db()
    try:
        c.execute("UPDATE jobs SET apply_status=NULL WHERE apply_status='in_progress'")
        c.commit()
    finally:
        c.close()
    return ", ".join(killed) or "any orphaned apply processes"


def redact_log_line(line: str) -> str:
    return re.sub(r"chat_id=\d+", "chat_id=***", re.sub(r"bot\d+:[\w-]+", "bot***", line))


def tail(path, n=40) -> str:
    try:
        lines = Path(path).read_text(errors="ignore").splitlines()
    except OSError:
        return ""
    return "\n".join(redact_log_line(ln) for ln in lines[-n:])


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
