# Databricks Claude 4.7/4.8 and Sonnet 5 Compatibility

## GitHub Issue

**Title:** Complete Databricks Claude compatibility across LiteLLM and native transports

**Description:**

PenguiFlow supports Databricks Claude through two distinct request paths:

- LiteLLM planner transport: `ReactPlanner(use_native_llm=False)`
- Native Databricks transport: `ReactPlanner(use_native_llm=True)`

New Claude endpoints reject request parameters that were valid for older
models. Each transport needs explicit, profile-driven request shaping. A
partial profile for Sonnet 5 fixed its LiteLLM temperature failure but left
native reasoning on an invalid legacy-thinking fallback.

This investigation excludes native tool calling. It covers only basic
completion, structured planner output, streaming, and reasoning request
parameters.

## Confirmed Reproductions

All probes used PenguiFlow directly with Databricks credentials. No ACR,
MLflow, AG-UI, or deployment code participated.

| Transport | Revision | Model | Request parameters | Result |
|---|---|---|---|---|
| LiteLLM | Parent `7493e499` | Sonnet 5 | `temperature=0.0` | HTTP 400: model does not support temperature |
| Native | Current branch | Sonnet 5 | `thinking={"type":"enabled","budget_tokens":4096}` | HTTP 400: legacy thinking is unsupported; use adaptive thinking and output effort |
| Native | Remediated branch | Sonnet 5 | `temperature=0.0`, low reasoning effort | Passes; response `56` |
| LiteLLM | Current branch with LiteLLM 1.94.0 | Opus 4.7, Opus 4.8, Sonnet 5 | Temperature and low reasoning effort | Passes |
| LiteLLM planner | Current branch with LiteLLM 1.94.0 | Opus 4.7, Opus 4.8, Sonnet 5 | Basic, schema, streaming, reasoning, local-node workflow | Passes |

## Exact Reproduction Commands

Both commands require `examples/.env` to contain valid `DATABRICKS_HOST` and
`DATABRICKS_TOKEN` values. They load credentials without printing them.

### Historical LiteLLM Temperature Rejection

This runs the parent of the fix in a detached temporary worktree, so it does
not modify the active checkout:

```bash
WORKTREE="$(mktemp -d /tmp/penguiflow-litellm-temperature.XXXXXX)"
git worktree add --detach "$WORKTREE" 7493e49909160fa14a33470a8f115364a20c7bec
uv run --project "$WORKTREE" --with python-dotenv python -c '
import asyncio
import os
from dotenv import load_dotenv

load_dotenv("/home/german/projects/penguiflow/examples/.env")
from penguiflow.planner.llm import _LiteLLMJSONClient

async def main():
    client = _LiteLLMJSONClient(
        {
            "model": "databricks/databricks-claude-sonnet-5",
            "api_base": os.environ["DATABRICKS_HOST"] + "/serving-endpoints",
            "api_key": os.environ["DATABRICKS_TOKEN"],
        },
        temperature=0.0,
        json_schema_mode=False,
        timeout_s=90,
    )
    await client.complete(messages=[{"role": "user", "content": "Reply with exactly: pong"}])

asyncio.run(main())
'
```

Expected result:

```text
BAD_REQUEST: Model global.anthropic.claude-sonnet-5 does not support the temperature parameter.
```

The historical `_LiteLLMJSONClient` includes `temperature: 0.0` in its request.

### Current Native Sonnet 5 Reasoning Regression Check

Run this from the current repository root:

```bash
uv run python -c '
import asyncio
from dotenv import load_dotenv

load_dotenv("examples/.env")
from penguiflow.llm import create_native_adapter

async def main():
    client = create_native_adapter(
        "databricks/databricks-claude-sonnet-5",
        temperature=0.0,
        reasoning_effort="low",
        timeout_s=90,
    )
    await client.complete(
        messages=[{"role": "user", "content": "What is 7 times 8? Reply only with the number."}]
    )

asyncio.run(main())
'
```

Expected result after remediation:

```text
56
```

The native request omits unsupported sampling parameters and emits:

```json
{"thinking":{"type":"adaptive"},"output_config":{"effort":"low"}}
```

## Diagnosis

### LiteLLM historical temperature failure

Before the fix, `_LiteLLMJSONClient` unconditionally forwarded the planner
default:

```python
params.setdefault("temperature", self._temperature)
```

Databricks Sonnet 5 rejects `temperature`, `top_p`, and `top_k`. The current
LiteLLM path resolves the model profile and omits temperature when
`supports_temperature=False`.

### LiteLLM Opus adaptive reasoning

Databricks Opus 4.7 and 4.8 reject LiteLLM's legacy thinking mapping. The
current LiteLLM path translates PenguiFlow's `reasoning_effort` to:

```json
{
  "thinking": {"type": "adaptive"},
  "output_config": {"effort": "low"}
}
```

This requires LiteLLM 1.94.0 or newer.

### Native Sonnet 5 reasoning failure (fixed)

The current Sonnet 5 profile declares only `supports_temperature=False`. With
native reasoning enabled, `DatabricksProvider` has no profile-specific
reasoning style and falls back to generic Claude budget thinking:

```json
{
  "thinking": {"type": "enabled", "budget_tokens": 4096}
}
```

Databricks rejects that payload. The Sonnet 5 profile now declares
`reasoning_request_style="adaptive_effort"`, selecting the endpoint-compatible
adaptive-thinking path without new model-name branching.

## Current Status

- LiteLLM temperature handling: fixed.
- LiteLLM Opus 4.7/4.8 adaptive reasoning: fixed.
- Native Sonnet 5 temperature handling: fixed by the existing partial profile.
- Native Sonnet 5 adaptive reasoning: fixed and live-verified.
- Databricks Claude sampling passthrough: fixed. `temperature`, `top_p`, and
  `top_k` are profile-declared rejected parameters and cannot be reintroduced
  through `LLMRequest.extra`.
- Sonnet 5 non-tool profile metadata: completed for native structured output,
  streaming, image input, schema transformation, and native transport choice.
- Native tool calling: intentionally out of scope.

## Live Planner Smoke Matrix

Verified against live Databricks on 2026-07-31. Each planner scenario used a
typed structured workflow for `17 * 23` and returned `391`.

| Model | Planner transport | Structured workflow | Streaming | Low reasoning |
|---|---|---|---|---|
| Opus 4.7 | LiteLLM | Pass | Pass | Pass |
| Opus 4.7 | Native | Pass | Pass | Pass |
| Opus 4.8 | LiteLLM | Pass | Pass | Pass |
| Opus 4.8 | Native | Pass | Pass | Pass |
| Sonnet 5 | LiteLLM | Pass | Pass | Pass |
| Sonnet 5 | Native | Pass | Pass | Pass |

The following direct native Sonnet 5 checks also passed: basic completion,
schema-guided output, streaming, and low reasoning. A focused streaming probe
found normal text chunks but no separate reasoning chunks for Opus 4.7, Opus
4.8, or Sonnet 5 on either transport. Low reasoning completed successfully;
reasoning-token streaming is therefore not currently surfaced by these
Databricks routes when adaptive thinking uses its default
`display="omitted"` behavior.

### Summarized Reasoning Probe

Adaptive thinking accepts `display="summarized"` on all three models:

```json
{
  "thinking": {"type": "adaptive", "display": "summarized"},
  "output_config": {"effort": "high"},
  "max_tokens": 16000
}
```

Live streaming requests using this payload returned visible reasoning summary
chunks and normal text through both native and LiteLLM transports.

| Model | Native summary chunks | LiteLLM summary chunks | Final answer |
|---|---:|---:|---|
| Opus 4.7 | 28 | 24 | `9` |
| Opus 4.8 | 27 | 30 | `9` |
| Sonnet 5 | 27 | 21 | `9` |

The displayed content is an Anthropic-provided reasoning summary, not raw
chain-of-thought. With `display="omitted"`, signed reasoning blocks remain
available for continuation but their visible text is intentionally empty.

## Remediation Plan

### Increment 1: Complete Native Sonnet 5 Reasoning Profile (complete)

1. Add complete Sonnet 5 capability metadata, including schema-guided output,
   JSON-only restriction, tools, streaming, image input, Databricks limits,
   native structured-output metadata, and native transport preference.
2. Set `supports_reasoning=True` and
   `reasoning_request_style="adaptive_effort"`.
3. Add a native provider regression test asserting Sonnet 5 omits temperature
   and emits adaptive thinking plus `output_config.effort`, never budget
   thinking.
4. Run a live native Sonnet 5 request with `temperature=0.0` and
   `reasoning_effort="low"`.

### Increment 2: Cross-Transport Capability Matrix

1. Add parameter-level tests for Opus 4.7, Opus 4.8, and Sonnet 5 on both
   transports.
2. Cover temperature omission, adaptive reasoning, JSON-schema output, and
   streaming independently.
3. Record known provider restrictions for unsupported feature combinations
   without expanding native tool-calling scope.

### Increment 3: Improve Native Error Diagnosis

1. Preserve Databricks' nested 400 message when mapping native provider errors.
2. Add a regression test using the Sonnet 5 legacy-thinking response shape.
3. Verify callers receive the actionable Databricks explanation rather than an
   empty provider message.

## Acceptance Criteria

- Sonnet 5 native requests omit `temperature`, `top_p`, and `top_k`.
- Sonnet 5 native reasoning emits adaptive thinking and output effort. Verified.
- Native Sonnet 5 reasoning with low effort succeeds live. Verified: `56`.
- LiteLLM behavior remains green for Opus 4.7, Opus 4.8, and Sonnet 5.
- Both transports have profile/request regression coverage.
- Native Databricks 400 errors retain their actionable nested message.

## Sources

- Databricks supported models: <https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models>
- Databricks model scoring: <https://docs.databricks.com/aws/en/machine-learning/model-serving/score-foundation-models>
- Local live reproduction results recorded above.
