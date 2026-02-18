# Native LLM Policy Table

This document defines how provider/model specific behavior is handled in the
native LLM path.

It is the reference for adding new policy rules without regressions.

## Scope

- Applies to `NativeLLMAdapter` only.
- Does not change LiteLLM behavior.
- Focuses on structured output compatibility and reasoning reliability.

Primary implementation files:

- `penguiflow/llm/native_policy.py`
- `penguiflow/llm/protocol.py`
- `penguiflow/llm/providers/nim.py`

Related planner reliability fixes:

- `penguiflow/planner/react_step.py` (system-message insertion order)
- `penguiflow/planner/react_runtime.py` (terminal answer type safety)

## Core Concepts

### 1) Structured mode

The adapter resolves one of three modes per request attempt:

- `json_schema`
- `json_object`
- `text` (no structured response format)

Downgrade chain:

- `json_schema -> json_object -> text`

### 2) Reasoning policy

The policy decides per request attempt:

- whether `reasoning_effort` is injected into provider request extras
- whether reasoning callbacks are emitted to the caller

For NIM structured calls, reasoning is ON by default and can be disabled
adaptively after an error on retry.

### 3) Attempt-level adaptation

Policy can change between attempts after provider errors:

- mode downgrade (schema compatibility)
- NIM structured reasoning fallback (disable reasoning on retry)

## Current Provider Rules

### OpenRouter (conservative)

Resolved by route prefix inside `openrouter/<provider>/<model>`:

- `openai`, `google`: keep requested mode
- `stepfun`: force `text`
- others: force `json_object`

### NIM

- If requested mode is `json_schema`, first attempt uses `json_object`.
- On structured non-retryable error, retry with reasoning disabled for that run.
- Provider-level message ordering guarantees all `system` messages are first.

## NIM model-specific reasoning controls

Mapped in `NIMProvider`:

- `qwen/qwen3.5-397b-a17b` -> `chat_template_kwargs.thinking_mode`
- `stepfun-ai/step-3.5-flash` -> `chat_template_kwargs.reasoning_mode`
- `z-ai/glm5` -> `chat_template_kwargs.reasoning_mode`
- `moonshotai/kimi-k2.5` -> `chat_template_kwargs.reasoning_mode`
- `deepseek-ai/deepseek-v3.1-terminus` -> `chat_template_kwargs.reasoning_mode`
- `minimaxai/minimax-m2.1` -> `chat_template_kwargs.enable_thinking`

Override precedence:

1. Explicit `extra_body.chat_template_kwargs` values
2. Alias `chat_template_kwargs` merge (fill missing)
3. Derived from `reasoning_effort`

Unsupported budget controls are ignored with warnings.

## How to add a new policy

1. Add/adjust routing logic in `resolve_policy()` (or helper functions) in:
   - `penguiflow/llm/native_policy.py`
2. If provider-specific request shaping is needed, update provider implementation:
   - for OpenAI-compatible providers: `penguiflow/llm/providers/*.py`
3. Add adapter-level fallback handling only when needed:
   - `penguiflow/llm/protocol.py`
4. Add tests before merge.

## Required tests for policy changes

At minimum, update or add tests in:

- `tests/test_llm_protocol.py`
- `tests/test_llm_provider_openrouter.py` (if OpenRouter affected)
- `tests/test_llm_provider_nim.py` (if NIM affected)

If planner behavior is impacted, also cover:

- `tests/test_react_planner.py`
- `tests/test_planner_streaming_extractors.py`

## CI expectations

All policy changes must keep the standard CI green:

- `uv run ruff check penguiflow penguiflow_a2a tests`
- `uv run mypy`
- `uv run pytest --cov=penguiflow --cov-report=term-missing`

## Live E2E policy validation (manual only)

Live provider matrix checks are intentionally not part of CI.

For NIM/OpenRouter live validation:

- run manually with env keys in shell only
- do not commit keys
- rotate temporary keys after use

Suggested matrix dimensions:

- models: provider-specific representative set
- reasoning effort: `high` and `off`
- mode: unstructured + structured request paths
- outcomes: success rate, parse quality, chunk behavior, latency

## Design constraints

- Preserve non-structured chat behavior unless a policy explicitly targets it.
- Prefer provider-specific policy over global behavior changes.
- Prefer conservative defaults + adaptive fallback over optimistic hardcoding.
- Keep policy table centralized and easy to diff.
