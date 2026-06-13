# Spec A: pipeline LLM providers — Claude CLI + Gemini thinking fix

Work inside this repo; **do not commit**. No network in your sandbox — never
call real APIs; tests monkeypatch transport. Suite must stay green:
`.venv/bin/python -m pytest tests/ -q` (103 pass today).

Read first: `src/applypilot/llm.py` (provider detection, LLMClient with
OpenAI-compat + native-Gemini fallback, `_log_usage` ledger),
`src/applypilot/scoring/{scorer,tailor,cover_letter}.py` and
`src/applypilot/panel.py` `gen_answer` (the call sites — all use
`get_client()`), `src/applypilot/server.py` settings endpoints,
`src/applypilot/apply/launcher.py` (~line 915: the subscription ledger
record pattern to mirror). Context: quality of covers/answers matters most;
scoring is high-volume and cheap-model-tolerant.

## 1. Refactor llm.py into a small provider layer (keep it ONE file, clean)

- Keep `LLMClient` (OpenAI-compat + native Gemini) as-is in behavior, but:
- **Gemini thinking fix**: thinking models (gemini-3.5-flash etc.) burn
  max_tokens on reasoning and return empty text. On the NATIVE Gemini path,
  always include `"thinkingConfig": {"thinkingBudget": <N>}` in
  generationConfig, N from env `APPLYPILOT_GEMINI_THINKING_BUDGET`
  (default 0). On the COMPAT path, when the model name contains "gemini"
  and does NOT contain "lite", pass
  `extra_body={"google": {"thinking_config": {"thinking_budget": N}}}` —
  implement as an extra JSON key `"google": {...}` merged into the payload
  (the compat layer accepts vendor extensions; harmless if ignored). Add a
  comment explaining the empty-resume failure mode this prevents.
- **New `ClaudeCLIClient`**: same `chat(messages, temperature, max_tokens)`
  interface. Runs `claude -p --model <model> --output-format json
  --no-session-persistence` with the prompt on stdin (system + user messages
  flattened: system content under a "System instructions:" header, then the
  conversation). Parse the JSON result: text from `result`, usage from
  `usage`/`total_cost_usd`. Append a ledger record to llm_usage.jsonl in the
  SAME shape the launcher writes (kind="subscription", explicit cost, model
  `f"claude-{model} (pipeline)"`). Model name: env `APPLYPILOT_CLAUDE_MODEL`
  default "haiku". Timeout 180s; on nonzero exit raise RuntimeError with
  stderr tail (never the prompt). `claude` resolved from PATH; raise a clear
  error if missing.
- **Stage-aware selection**: `get_client(stage: str | None = None)`.
  Provider resolution order: env `APPLYPILOT_{STAGE}_PROVIDER` (STAGE in
  COVER, ANSWER, SCORE, TAILOR) → `APPLYPILOT_PIPELINE_PROVIDER` → existing
  auto-detection (gemini/openai/local). Provider values: `gemini`, `openai`,
  `local`, `claude-cli`. Update call sites: cover_letter.py →
  `get_client(stage="cover")`, panel.gen_answer → `get_client(stage="answer")`,
  scorer → `stage="score"`, tailor → `stage="tailor"`. No behavior change
  when no env is set.
- Cleanliness pass on llm.py while in there: dedupe the retry loop if it
  helps readability, module docstring updated, type hints consistent. Do not
  grow the file beyond what the features need.

## 2. Settings surface (server.py)

- GET /api/settings adds: `cover_provider` (resolved value for the cover
  stage: "gemini"|"openai"|"local"|"claude-cli"), `claude_cli_available`
  (shutil.which("claude") is not None).
- PUT accepts `cover_provider` ("" removes both APPLYPILOT_COVER_PROVIDER
  and APPLYPILOT_ANSWER_PROVIDER; a value sets BOTH — covers and answers
  travel together, that's the product intent). Validate against the allowed
  provider values (422 otherwise).

## 3. Tests

- ClaudeCLIClient: monkeypatch subprocess.run — happy path (text + ledger
  record written with kind=subscription and explicit cost), nonzero exit
  raises without leaking the prompt, missing binary raises helpful error.
- Thinking fix: native payload contains thinkingConfig with budget 0 by
  default and honors the env override; compat payload for "gemini-3.5-flash"
  carries the google extension while "gemini-3.1-flash-lite" still works.
- Stage selection: env matrix (stage override beats pipeline override beats
  auto), unknown provider value raises cleanly.
- Settings: GET fields, PUT round-trip + validation.

## Constraints

Don't touch web/, docs/, apply/ (the launcher's ledger writer stays where it
is). Never log prompts or keys. Style matches neighbors. Full suite green;
short summary; no commits.
