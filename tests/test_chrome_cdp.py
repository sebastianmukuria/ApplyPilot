import json

import httpx

from applypilot.apply import chrome


class _Response:
    status_code = 200

    def __init__(self, payload=None):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None

    @property
    def text(self):
        return json.dumps(self._payload)


def test_reset_tabs_closes_page_targets(monkeypatch):
    monkeypatch.setattr(chrome, "_detached_ports", set())
    monkeypatch.setattr(chrome, "_read_detached", lambda: {})
    closed_targets = []

    def fake_get(url, timeout):
        if url == "http://localhost:9333/json/list":
            return _Response([
                {"id": "page-1", "type": "page"},
                {"id": "worker-1", "type": "service_worker"},
            ])
        if url == "http://localhost:9333/json/close/page-1":
            closed_targets.append("page-1")
            return _Response({})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(chrome.httpx, "get", fake_get)

    assert chrome.reset_tabs(9333) is True
    assert closed_targets == ["page-1"]


def test_reset_tabs_returns_false_on_error(monkeypatch):
    monkeypatch.setattr(chrome, "_detached_ports", set())
    monkeypatch.setattr(chrome, "_read_detached", lambda: {})

    def fail_get(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(chrome.httpx, "get", fail_get)

    assert chrome.reset_tabs(65500) is False


def test_reset_tabs_refuses_detached_ports(monkeypatch):
    calls = []
    monkeypatch.setattr(chrome, "_detached_ports", set())
    monkeypatch.setattr(chrome, "_read_detached", lambda: {9222: 12345})
    monkeypatch.setattr(chrome.httpx, "get", lambda *args, **kwargs: calls.append(args))

    assert chrome.reset_tabs(9222) is False
    assert calls == []


def test_launch_chrome_polls_cdp_until_ready(monkeypatch, tmp_path):
    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    get_calls = []
    sleeps = []

    def fake_get(url, timeout):
        get_calls.append(url)
        if len(get_calls) < 3:
            raise httpx.ConnectError("not ready")
        return _Response()

    monkeypatch.setattr(chrome, "_detached_ports", set())
    monkeypatch.setattr(chrome, "_read_detached", lambda: {})
    monkeypatch.setattr(chrome, "_chrome_procs", {})
    monkeypatch.setattr(chrome, "_worker_ports", {})
    monkeypatch.setattr(chrome, "setup_worker_profile", lambda worker_id: tmp_path)
    monkeypatch.setattr(chrome, "_kill_on_port", lambda port: None)
    monkeypatch.setattr(chrome, "_suppress_restore_nag", lambda profile_dir: None)
    monkeypatch.setattr(chrome.config, "get_chrome_path", lambda: "/fake/chrome")
    monkeypatch.setattr(chrome.subprocess, "Popen", lambda cmd, **kwargs: FakeProc())
    monkeypatch.setattr(chrome.httpx, "get", fake_get)
    monkeypatch.setattr(chrome.time, "sleep", lambda seconds: sleeps.append(seconds))

    proc, port = chrome.launch_chrome(7, port=9333)

    assert proc.pid == 4242
    assert port == 9333
    assert len(get_calls) == 3
    assert get_calls[0] == "http://localhost:9333/json/version"
    assert sleeps == [0.25, 0.25]
