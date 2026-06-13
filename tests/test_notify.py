import pytest

import applypilot.notify as notify


NOTIFY_ENV_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "APPLYPILOT_NTFY_TOPIC",
    "APPLYPILOT_NTFY_SERVER",
    "APPLYPILOT_WEBHOOK_URL",
    "DISCORD_WEBHOOK_URL",
    "SLACK_WEBHOOK_URL",
    "APPLYPILOT_MACOS_BANNER",
]


@pytest.fixture(autouse=True)
def clean_notify(monkeypatch):
    notify._last_sent.clear()
    for key in NOTIFY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")
    yield
    notify._last_sent.clear()


def test_notify_fires_each_configured_channel(monkeypatch):
    gets = []
    posts = []
    runs = []
    popens = []

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-id")
    monkeypatch.setenv("APPLYPILOT_NTFY_TOPIC", "applypilot")
    monkeypatch.setenv("APPLYPILOT_NTFY_SERVER", "https://ntfy.example")
    monkeypatch.setenv("APPLYPILOT_WEBHOOK_URL", "https://hooks.example/generic")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hooks.example/discord")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/slack")
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(notify.httpx, "get", lambda *args, **kwargs: gets.append((args, kwargs)))
    monkeypatch.setattr(notify.httpx, "post", lambda *args, **kwargs: posts.append((args, kwargs)))
    monkeypatch.setattr(notify.subprocess, "run", lambda *args, **kwargs: runs.append((args, kwargs)))
    monkeypatch.setattr(notify.subprocess, "Popen", lambda *args, **kwargs: popens.append((args, kwargs)))

    notify.notify("needs_human", "review the form", worker_id=7)

    assert gets == [(
        ("https://api.telegram.org/botbot-token/sendMessage",),
        {"params": {"chat_id": "chat-id", "text": "🔔 ApplyPilot needs you: review the form"}, "timeout": 10},
    )]
    post_urls = [args[0] for args, _ in posts]
    assert post_urls == [
        "https://ntfy.example/applypilot",
        "https://hooks.example/generic",
        "https://hooks.example/discord",
        "https://hooks.example/slack",
    ]
    assert posts[0][1]["content"] == "ApplyPilot: review the form"
    assert posts[0][1]["headers"] == {"Title": "ApplyPilot", "Priority": "high", "Tags": "airplane"}
    assert posts[1][1]["json"]["event"] == "needs_human"
    assert posts[1][1]["json"]["reason"] == "review the form"
    assert posts[2][1]["json"] == {"content": "ApplyPilot: review the form"}
    assert posts[3][1]["json"] == {"text": "ApplyPilot: review the form"}
    assert all(kwargs["timeout"] == 10 for _, kwargs in posts)
    assert runs[0][0][0][:2] == ["osascript", "-e"]
    assert runs[0][1]["timeout"] == 10
    assert popens[0][0][0] == ["afplay", "/System/Library/Sounds/Glass.aiff"]


def test_notify_channel_failure_does_not_stop_later_channels(monkeypatch):
    posts = []

    monkeypatch.setenv("APPLYPILOT_NTFY_TOPIC", "applypilot")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/slack")

    def fake_post(url, **kwargs):
        if url.startswith("https://ntfy.sh/"):
            raise RuntimeError("ntfy down")
        posts.append((url, kwargs))

    monkeypatch.setattr(notify.httpx, "post", fake_post)

    notify.notify("run_failed", "Beta failed: timeout")

    assert posts == [("https://hooks.example/slack", {"json": {"text": "ApplyPilot: Beta failed: timeout"}, "timeout": 10})]


def test_needs_human_throttles_but_run_failed_does_not(monkeypatch):
    posts = []
    now = [1_000.0]

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/slack")
    monkeypatch.setattr(notify.time, "time", lambda: now[0])
    monkeypatch.setattr(notify.httpx, "post", lambda *args, **kwargs: posts.append((args, kwargs)))

    notify.notify("needs_human", "first", worker_id=3)
    notify.notify("needs_human", "second", worker_id=3)
    notify.notify("run_failed", "failed once", worker_id=3)
    notify.notify("run_failed", "failed twice", worker_id=3)
    now[0] += 41
    notify.notify("needs_human", "third", worker_id=3)

    texts = [kwargs["json"]["text"] for _, kwargs in posts]
    assert texts == [
        "ApplyPilot: first",
        "ApplyPilot: failed once",
        "ApplyPilot: failed twice",
        "ApplyPilot: third",
    ]


def test_macos_banner_uses_argv_with_escaped_reason(monkeypatch):
    runs = []
    popens = []
    reason = 'bad "quote" and \\ slash'

    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(notify.subprocess, "run", lambda *args, **kwargs: runs.append((args, kwargs)))
    monkeypatch.setattr(notify.subprocess, "Popen", lambda *args, **kwargs: popens.append((args, kwargs)))

    notify.notify("run_finished", reason)

    assert runs[0][0][0] == [
        "osascript",
        "-e",
        'display notification "bad \\"quote\\" and \\\\ slash" with title "ApplyPilot"',
    ]
    assert "shell" not in runs[0][1]
    assert popens[0][0][0] == ["afplay", "/System/Library/Sounds/Glass.aiff"]


def test_macos_banner_skipped_off_darwin(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")
    monkeypatch.setattr(notify.subprocess, "run", lambda *args, **kwargs: calls.append(("run", args, kwargs)))
    monkeypatch.setattr(notify.subprocess, "Popen", lambda *args, **kwargs: calls.append(("popen", args, kwargs)))

    notify.notify("run_finished", "done")

    assert calls == []
