# Executive Summary: OpenRouter vs Databricks (Reasoning + Structured Output)

## Short answer

OpenRouter is **not** defaulting to plain free-form text in this path.

It defaults to a **JSON object structured mode** (`json_object`) for non-allowlisted routes like `openrouter/anthropic/...`, not strict `json_schema`.

## What we observed

With `use_native_llm=True`, `use_native_reasoning=True`, and `REASONING_EFFORT=low`:

- Databricks Claude (`databricks/databricks-claude-sonnet-4-5`) fails with:
  - `Thinking may not be enabled when tool_choice forces tool use.`
- OpenRouter Claude (`openrouter/anthropic/claude-sonnet-4.5`) succeeds.

## Why behavior differs

Both routes carry `reasoning_effort=low`, but structured mode differs:

- Databricks path keeps strict planner schema (`json_schema` semantics).
- OpenRouter non-allowlisted path is preemptively downgraded to `json_object`.

So OpenRouter avoids the exact strict structured+thinking request shape that triggers the Databricks/Anthropic incompatibility.

## Technical detail (current policy)

In `penguiflow/llm/native_policy.py`:

- OpenRouter `openai/*` and `google/*` routes keep requested mode.
- OpenRouter `stepfun/*` routes force `text` mode.
- Other OpenRouter routes (including `anthropic/*`) force `json_object` mode.

This means OpenRouter Anthropic is typically **structured JSON-object mode**, not plain text.

## Business implication

OpenRouter success here should not be interpreted as proof that Anthropic supports strict structured output + thinking under forced-tool semantics.

It is evidence that PenguiFlow's OpenRouter route policy is currently steering requests into a more compatible structured mode.
