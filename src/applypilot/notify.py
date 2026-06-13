"""Best-effort notification fan-out for ApplyPilot events."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import threading
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

EVENTS = {"needs_human", "run_failed", "run_finished", "batch_done"}
THROTTLE_SECONDS = 40
_last_sent: dict[tuple[int, str], float] = {}
_lock = threading.Lock()


def notify(event: str, reason: str, *, worker_id: int = 0) -> None:
    """Send an event to every configured channel.

    Notification delivery is intentionally best-effort: every channel is
    isolated so a broken webhook never blocks a different channel or the apply
    worker itself.
    """
    if event not in EVENTS:
        logger.debug("Unknown notification event: %s", event)
        return

    if event == "needs_human" and _is_throttled(worker_id, event):
        return

    for channel in (
        _send_telegram,
        _send_ntfy,
        _send_webhook,
        _send_discord,
        _send_slack,
        _send_macos,
    ):
        try:
            channel(event, reason)
        except Exception:
            logger.debug("Notification channel failed: %s", channel.__name__, exc_info=True)


def last_needs_human_sent_at(worker_id: int) -> float | None:
    """Return the last accepted needs_human send time for a worker."""
    with _lock:
        return _last_sent.get((worker_id, "needs_human"))


def needs_human_sent_recently(worker_id: int, *, within: float = 60) -> bool:
    """Return True if a needs_human notification was sent recently."""
    last = last_needs_human_sent_at(worker_id)
    return last is not None and time.time() - last < within


def _is_throttled(worker_id: int, event: str) -> bool:
    now = time.time()
    key = (worker_id, event)
    with _lock:
        if now - _last_sent.get(key, 0) < THROTTLE_SECONDS:
            return True
        _last_sent[key] = now
    return False


def _webhook_text(reason: str) -> str:
    return f"ApplyPilot: {reason}"


def _send_telegram(event: str, reason: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        return
    text = f"🔔 ApplyPilot needs you: {reason}"[:300]
    httpx.get(
        f"https://api.telegram.org/bot{token}/sendMessage",
        params={"chat_id": chat, "text": text},
        timeout=10,
    )


def _send_ntfy(event: str, reason: str) -> None:
    topic = os.environ.get("APPLYPILOT_NTFY_TOPIC", "")
    if not topic:
        return
    server = os.environ.get("APPLYPILOT_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    priority = "high" if event == "needs_human" else "default"
    httpx.post(
        f"{server}/{topic}",
        content=_webhook_text(reason),
        headers={"Title": "ApplyPilot", "Priority": priority, "Tags": "airplane"},
        timeout=10,
    )


def _send_webhook(event: str, reason: str) -> None:
    url = os.environ.get("APPLYPILOT_WEBHOOK_URL", "")
    if not url:
        return
    httpx.post(
        url,
        json={"event": event, "reason": reason, "ts": datetime.now(timezone.utc).isoformat()},
        timeout=10,
    )


def _send_discord(event: str, reason: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    httpx.post(url, json={"content": _webhook_text(reason)}, timeout=10)


def _send_slack(event: str, reason: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url:
        return
    httpx.post(url, json={"text": _webhook_text(reason)}, timeout=10)


def _send_macos(event: str, reason: str) -> None:
    if platform.system() != "Darwin" or os.environ.get("APPLYPILOT_MACOS_BANNER") == "0":
        return

    script = f'display notification "{_applescript_string(reason)}" with title "ApplyPilot"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:
        logger.debug("macOS banner notification failed", exc_info=True)

    try:
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Glass.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        logger.debug("macOS notification sound failed", exc_info=True)


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
