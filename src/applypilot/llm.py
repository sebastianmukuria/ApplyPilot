"""Provider layer for ApplyPilot pipeline LLM calls.

Provider selection is stage-aware:
  APPLYPILOT_{COVER,ANSWER,SCORE,TAILOR}_PROVIDER
  APPLYPILOT_PIPELINE_PROVIDER
  auto-detect from GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL

API providers use ``LLM_MODEL`` as a model override. Claude CLI uses
``APPLYPILOT_CLAUDE_MODEL`` and records subscription-covered ledger rows.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider detection / selection
# ---------------------------------------------------------------------------

_GEMINI_COMPAT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_GEMINI_NATIVE_BASE = "https://generativelanguage.googleapis.com/v1beta"

ALLOWED_PROVIDERS = ("gemini", "openai", "local", "claude-cli")
_STAGE_PROVIDER_ENV = {
    "cover": "APPLYPILOT_COVER_PROVIDER",
    "answer": "APPLYPILOT_ANSWER_PROVIDER",
    "score": "APPLYPILOT_SCORE_PROVIDER",
    "tailor": "APPLYPILOT_TAILOR_PROVIDER",
    "track": "APPLYPILOT_TRACK_PROVIDER",
}


class ChatClient(Protocol):
    """Common interface used by pipeline call sites."""

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        ...

    def ask(self, prompt: str, **kwargs) -> str:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    base_url: str
    model: str
    api_key: str = ""


def _normalize_stage(stage: str | None) -> str | None:
    if stage is None:
        return None
    stage_key = stage.strip().lower()
    if not stage_key:
        return None
    if stage_key not in _STAGE_PROVIDER_ENV:
        valid = ", ".join(sorted(_STAGE_PROVIDER_ENV))
        raise RuntimeError(f"Unknown LLM stage '{stage}'. Expected one of: {valid}.")
    return stage_key


def _validate_provider(value: str, source: str) -> str:
    provider = value.strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        allowed = ", ".join(ALLOWED_PROVIDERS)
        raise RuntimeError(f"Unknown LLM provider '{value}' in {source}. Expected one of: {allowed}.")
    return provider


def _provider_override(stage: str | None, env: Mapping[str, str]) -> tuple[str, str] | None:
    stage_key = _normalize_stage(stage)
    if stage_key:
        var = _STAGE_PROVIDER_ENV[stage_key]
        if env.get(var, "").strip():
            return env[var], var

    var = "APPLYPILOT_PIPELINE_PROVIDER"
    value = env.get(var, "").strip()
    if value and value.lower() != "auto":
        return env[var], var

    return None


def _claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def _auto_provider_name(
    env: Mapping[str, str],
    claude_available: bool | None = None,
) -> str | None:
    """Auto-detection order. A Claude Code subscription alone runs the ENTIRE
    pipeline (discovery scoring included) -- API keys are optional
    accelerators. An explicit LLM_URL is a deliberate local-model setup and
    outranks it.
    """
    if env.get("LLM_URL", "").strip():
        return "local"
    if claude_available if claude_available is not None else _claude_cli_available():
        return "claude-cli"
    if env.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    if env.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return None


def resolve_provider_name(
    stage: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    default: str | None = None,
    claude_available: bool | None = None,
) -> str:
    """Resolve the provider name without constructing a client.

    ``default`` is for UI/settings surfaces that must render even before the
    user has configured credentials. Runtime client creation leaves it unset so
    the historical "No LLM provider configured" error is preserved.
    """
    env_map = os.environ if env is None else env
    override = _provider_override(stage, env_map)
    if override:
        return _validate_provider(*override)

    auto = _auto_provider_name(env_map, claude_available=claude_available)
    if auto:
        return auto

    if default is not None:
        return _validate_provider(default, "default")

    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, or LLM_URL in your environment."
    )


def _provider_spec(provider: str, env: Mapping[str, str]) -> ProviderSpec:
    model_override = env.get("LLM_MODEL", "").strip()

    if provider == "gemini":
        api_key = env.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required when the LLM provider is gemini.")
        return ProviderSpec(
            provider="gemini",
            base_url=_GEMINI_COMPAT_BASE,
            model=model_override or "gemini-2.0-flash",
            api_key=api_key,
        )

    if provider == "openai":
        api_key = env.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when the LLM provider is openai.")
        return ProviderSpec(
            provider="openai",
            base_url="https://api.openai.com/v1",
            model=model_override or "gpt-4o-mini",
            api_key=api_key,
        )

    if provider == "local":
        local_url = env.get("LLM_URL", "").strip()
        if not local_url:
            raise RuntimeError("LLM_URL is required when the LLM provider is local.")
        return ProviderSpec(
            provider="local",
            base_url=local_url.rstrip("/"),
            model=model_override or "local-model",
            api_key=env.get("LLM_API_KEY", ""),
        )

    if provider == "claude-cli":
        return ProviderSpec(
            provider="claude-cli",
            base_url="claude-cli",
            model=env.get("APPLYPILOT_CLAUDE_MODEL", "").strip() or "haiku",
        )

    raise RuntimeError(f"Unsupported LLM provider: {provider}")


def _resolve_provider(stage: str | None = None) -> ProviderSpec:
    """Resolve provider configuration from the current environment."""
    provider = resolve_provider_name(stage)
    return _provider_spec(provider, os.environ)


def _detect_provider() -> tuple[str, str, str]:
    """Return (base_url, model, api_key) for legacy callers/tests.

    Reads env at call time (not module import time) so that load_env() called
    in _bootstrap() is always visible here.
    """
    spec = _resolve_provider()
    return spec.base_url, spec.model, spec.api_key


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_TIMEOUT = 120  # seconds
_CLAUDE_TIMEOUT = 180  # seconds

# Base wait on first 429/503 (doubles each retry, caps at 60s).
# Gemini free tier is 15 RPM = 4s minimum between requests; 10s gives headroom.
_RATE_LIMIT_BASE_WAIT = 10


def _usage_file() -> Path:
    from applypilot.config import APP_DIR

    return Path(os.environ.get("APPLYPILOT_DIR", str(APP_DIR))) / "llm_usage.jsonl"


def _append_usage_record(record: dict) -> None:
    path = _usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gemini_thinking_budget() -> int:
    raw = os.environ.get("APPLYPILOT_GEMINI_THINKING_BUDGET", "0").strip() or "0"
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("APPLYPILOT_GEMINI_THINKING_BUDGET must be an integer.") from exc


class LLMClient:
    """Thin LLM client supporting OpenAI-compatible and native Gemini endpoints.

    For Gemini keys, starts on the OpenAI-compat layer. On a 403 (which
    happens with preview/experimental models not exposed via compat), it
    automatically switches to the native generateContent API and stays there
    for the lifetime of the process.
    """

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)
        # True once we've confirmed the native Gemini API works for this model
        self._use_native_gemini: bool = False
        self._is_gemini: bool = base_url.startswith(_GEMINI_COMPAT_BASE)

    def _log_usage(self, prompt_tokens, completion_tokens) -> None:
        """Append one usage record to ~/.applypilot/llm_usage.jsonl.

        Raw token counts only -- cost estimation happens in the GUI, where the
        price table lives. Append of a single short line is atomic enough for
        the parallel pipeline workers. Never let accounting break a request.
        """
        if prompt_tokens is None and completion_tokens is None:
            return
        try:
            rec = {
                "ts": _utc_ts(),
                "model": self.model,
                "in": int(prompt_tokens or 0),
                "out": int(completion_tokens or 0),
            }
            _append_usage_record(rec)
        except Exception:
            log.debug("Could not record LLM usage", exc_info=True)

    # -- Native Gemini API --------------------------------------------------

    def _chat_native_gemini(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the native Gemini generateContent API.

        Used automatically when the OpenAI-compat endpoint returns 403,
        which happens for preview/experimental models not exposed via compat.

        Converts OpenAI-style messages to Gemini's contents/systemInstruction
        format transparently.
        """
        contents: list[dict] = []
        system_parts: list[dict] = []

        for msg in messages:
            role = msg["role"]
            text = msg.get("content", "")
            if role == "system":
                system_parts.append({"text": text})
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                # Gemini uses "model" instead of "assistant"
                contents.append({"role": "model", "parts": [{"text": text}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingBudget": _gemini_thinking_budget()},
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        url = f"{_GEMINI_NATIVE_BASE}/models/{self.model}:generateContent"
        # key goes in a header, never the URL — request URLs end up in logs
        resp = self._client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("usageMetadata", {})
        self._log_usage(meta.get("promptTokenCount"), meta.get("candidatesTokenCount"))
        return data["candidates"][0]["content"]["parts"][0]["text"]

    # -- OpenAI-compat API --------------------------------------------------

    def _chat_compat(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Call the OpenAI-compatible endpoint."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        model_lower = self.model.lower()
        if "gemini" in model_lower and "lite" not in model_lower:
            # Gemini thinking models can spend max_tokens on hidden reasoning
            # and return empty resume/letter text; cap thinking explicitly.
            payload["google"] = {
                "thinking_config": {
                    "thinking_budget": _gemini_thinking_budget(),
                },
            }

        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        # 403 on Gemini compat = model not available on compat layer.
        # Raise a specific sentinel so chat() can switch to native API.
        if resp.status_code == 403 and self._is_gemini:
            raise _GeminiCompatForbidden(resp)

        return self._handle_compat_response(resp)

    def _handle_compat_response(self, resp: httpx.Response) -> str:
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage", {})
        self._log_usage(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return data["choices"][0]["message"]["content"]

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        for attempt in range(_MAX_RETRIES):
            try:
                # Route to native Gemini if we've already confirmed it's needed
                if self._use_native_gemini:
                    return self._chat_native_gemini(messages, temperature, max_tokens)

                return self._chat_compat(messages, temperature, max_tokens)

            except _GeminiCompatForbidden as exc:
                # Model not available on OpenAI-compat layer — switch to native.
                log.warning(
                    "Gemini compat endpoint returned 403 for model '%s'. "
                    "Switching to native generateContent API. "
                    "(Preview/experimental models are often compat-only on native.)",
                    self.model,
                )
                self._use_native_gemini = True
                # Retry immediately with native — don't count as a rate-limit wait
                try:
                    return self._chat_native_gemini(messages, temperature, max_tokens)
                except httpx.HTTPStatusError as native_exc:
                    raise RuntimeError(
                        f"Both Gemini endpoints failed. Compat: 403 Forbidden. "
                        f"Native: {native_exc.response.status_code} — "
                        f"{native_exc.response.text[:200]}"
                    ) from native_exc

            except httpx.HTTPStatusError as exc:
                resp = exc.response
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                    # Respect Retry-After header if provided (Gemini sends this).
                    retry_after = (
                        resp.headers.get("Retry-After")
                        or resp.headers.get("X-RateLimit-Reset-Requests")
                    )
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except (ValueError, TypeError):
                            wait = _RATE_LIMIT_BASE_WAIT * (2 ** attempt)
                    else:
                        wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)

                    log.warning(
                        "LLM rate limited (HTTP %s). Waiting %ds before retry %d/%d. "
                        "Tip: Gemini free tier = 15 RPM. Consider a paid account "
                        "or switching to a local model.",
                        resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = min(_RATE_LIMIT_BASE_WAIT * (2 ** attempt), 60)
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()


# Pipeline threads (batched scoring workers, the answers endpoint) must not
# spawn unbounded claude processes; two concurrent CLI calls is plenty.
_CLAUDE_CLI_SLOTS = threading.Semaphore(2)


class ClaudeCLIClient:
    """Pipeline client backed by the local Claude CLI subscription."""

    def __init__(self, model: str = "haiku") -> None:
        self.model = model

    def _flatten_messages(self, messages: list[dict]) -> str:
        system_parts: list[str] = []
        conversation: list[str] = []

        for msg in messages:
            role = str(msg.get("role", "user")).strip().lower() or "user"
            content = str(msg.get("content", ""))
            if role == "system":
                system_parts.append(content)
            else:
                conversation.append(f"{role.title()}:\n{content}")

        parts: list[str] = []
        if system_parts:
            parts.append("System instructions:\n" + "\n\n".join(system_parts))
        if conversation:
            parts.append("Conversation:\n" + "\n\n".join(conversation))
        return "\n\n".join(parts).strip()

    def _log_usage(self, usage: dict, total_cost_usd: float | int | str | None) -> None:
        try:
            rec = {
                "ts": _utc_ts(),
                "model": f"claude-{self.model} (pipeline)",
                "in": int(usage.get("input_tokens", 0) or 0),
                "out": int(usage.get("output_tokens", 0) or 0),
                "cost": round(float(total_cost_usd or 0), 6),
                "kind": "subscription",
            }
            _append_usage_record(rec)
        except Exception:
            log.debug("Could not record claude usage", exc_info=True)

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Run ``claude -p`` and return the JSON result text.

        ``temperature`` and ``max_tokens`` are accepted for API parity with
        LLMClient; the Claude CLI prompt mode does not expose those knobs here.
        """
        del temperature, max_tokens
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError(
                "Claude CLI provider selected but 'claude' was not found on PATH. "
                "Install Claude Code CLI or choose another pipeline provider."
            )

        with _CLAUDE_CLI_SLOTS:
            proc = subprocess.run(
                [
                    exe,
                    "-p",
                    "--model",
                    self.model,
                    "--output-format",
                    "json",
                    "--no-session-persistence",
                ],
                input=self._flatten_messages(messages),
                text=True,
                capture_output=True,
                timeout=_CLAUDE_TIMEOUT,
                check=False,
            )
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip()[-1000:] or "no stderr"
            raise RuntimeError(
                f"Claude CLI failed with exit code {proc.returncode}: {stderr_tail}"
            )

        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude CLI returned invalid JSON.") from exc

        result = data.get("result")
        if not isinstance(result, str):
            raise RuntimeError("Claude CLI JSON response did not include result text.")

        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        self._log_usage(usage, data.get("total_cost_usd"))
        return result

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        return None


class _GeminiCompatForbidden(Exception):
    """Sentinel: Gemini OpenAI-compat returned 403. Switch to native API."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Gemini compat 403: {response.text[:200]}")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: ChatClient | None = None
_client_cache: dict[tuple[str | None, ProviderSpec], ChatClient] = {}


def _build_client(spec: ProviderSpec) -> ChatClient:
    if spec.provider == "claude-cli":
        return ClaudeCLIClient(spec.model)
    return LLMClient(spec.base_url, spec.model, spec.api_key)


def get_client(stage: str | None = None) -> ChatClient:
    """Return a cached LLM client for the requested pipeline stage."""
    global _instance
    stage_key = _normalize_stage(stage)
    if stage_key is None and _instance is not None:
        return _instance

    spec = _resolve_provider(stage_key)
    cache_key = (stage_key, spec)
    client = _client_cache.get(cache_key)
    if client is None:
        log.info("LLM provider: %s  model: %s", spec.provider, spec.model)
        client = _build_client(spec)
        _client_cache[cache_key] = client
    if stage_key is None:
        _instance = client
    return client
