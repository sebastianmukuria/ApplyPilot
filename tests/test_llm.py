import json
import subprocess

import httpx
import pytest

from applypilot import llm


class FakeHTTPClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response

    def close(self):
        pass


def _compat_response(text: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


def _native_response(text: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 6},
        },
        request=httpx.Request("POST", "https://example.test/generateContent"),
    )


def test_claude_cli_client_happy_path_writes_subscription_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({
                "result": "draft text",
                "usage": {"input_tokens": 11, "output_tokens": 7},
                "total_cost_usd": 0.00123456,
            }),
            stderr="",
        )

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    client = llm.ClaudeCLIClient("sonnet")
    text = client.chat([
        {"role": "system", "content": "Be direct."},
        {"role": "user", "content": "Write this."},
    ])

    assert text == "draft text"
    cmd, kwargs = calls[0]
    assert cmd == [
        "/usr/bin/claude",
        "-p",
        "--model",
        "sonnet",
        "--output-format",
        "json",
        "--no-session-persistence",
    ]
    assert "System instructions:\nBe direct." in kwargs["input"]
    assert "User:\nWrite this." in kwargs["input"]
    assert kwargs["timeout"] == 180

    rec = json.loads((tmp_path / "llm_usage.jsonl").read_text().strip())
    assert rec["model"] == "claude-sonnet (pipeline)"
    assert rec["in"] == 11
    assert rec["out"] == 7
    assert rec["cost"] == 0.001235
    assert rec["kind"] == "subscription"


def test_claude_cli_nonzero_error_does_not_leak_prompt(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")
    secret_prompt = "SECRET APPLICATION TEXT"

    def fake_run(cmd, **kwargs):
        assert secret_prompt in kwargs["input"]
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="permission denied")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc:
        llm.ClaudeCLIClient("haiku").chat([{"role": "user", "content": secret_prompt}])

    msg = str(exc.value)
    assert "permission denied" in msg
    assert secret_prompt not in msg


def test_claude_cli_missing_binary_is_helpful(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError) as exc:
        llm.ClaudeCLIClient("haiku").chat([{"role": "user", "content": "hello"}])

    assert "claude" in str(exc.value).lower()
    assert "path" in str(exc.value).lower()


def test_native_gemini_payload_includes_default_thinking_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    monkeypatch.delenv("APPLYPILOT_GEMINI_THINKING_BUDGET", raising=False)
    client = llm.LLMClient(llm._GEMINI_COMPAT_BASE, "gemini-3.5-flash", "key")
    fake = FakeHTTPClient(_native_response())
    client._client = fake
    client._use_native_gemini = True

    assert client.chat([{"role": "user", "content": "hi"}]) == "ok"

    config = fake.calls[0]["json"]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}


def test_native_gemini_payload_honors_thinking_budget_override(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("APPLYPILOT_GEMINI_THINKING_BUDGET", "64")
    client = llm.LLMClient(llm._GEMINI_COMPAT_BASE, "gemini-3.5-flash", "key")
    fake = FakeHTTPClient(_native_response())
    client._client = fake
    client._use_native_gemini = True

    client.chat([{"role": "user", "content": "hi"}])

    config = fake.calls[0]["json"]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 64}


def test_gemini_compat_payload_adds_google_extension_for_non_lite(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("APPLYPILOT_GEMINI_THINKING_BUDGET", "32")
    client = llm.LLMClient(llm._GEMINI_COMPAT_BASE, "gemini-3.5-flash", "key")
    fake = FakeHTTPClient(_compat_response())
    client._client = fake

    client.chat([{"role": "user", "content": "hi"}])

    payload = fake.calls[0]["json"]
    assert payload["google"] == {"thinking_config": {"thinking_budget": 32}}


def test_gemini_compat_lite_model_skips_google_extension(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path))
    client = llm.LLMClient(llm._GEMINI_COMPAT_BASE, "gemini-3.1-flash-lite", "key")
    fake = FakeHTTPClient(_compat_response())
    client._client = fake

    assert client.chat([{"role": "user", "content": "hi"}]) == "ok"

    assert "google" not in fake.calls[0]["json"]


def test_stage_provider_resolution_precedence():
    env = {
        "LLM_URL": "http://localhost:11434/v1",
        "OPENAI_API_KEY": "openai-key",
        "APPLYPILOT_PIPELINE_PROVIDER": "openai",
        "APPLYPILOT_SCORE_PROVIDER": "gemini",
    }

    assert llm.resolve_provider_name("score", env=env) == "gemini"
    assert llm.resolve_provider_name("cover", env=env) == "openai"
    assert llm.resolve_provider_name("tailor", env={"LLM_URL": "http://local"}) == "local"


def test_unknown_stage_provider_raises_cleanly():
    with pytest.raises(RuntimeError) as exc:
        llm.resolve_provider_name("cover", env={"APPLYPILOT_COVER_PROVIDER": "bad"})

    assert "Unknown LLM provider" in str(exc.value)
    assert "APPLYPILOT_COVER_PROVIDER" in str(exc.value)
