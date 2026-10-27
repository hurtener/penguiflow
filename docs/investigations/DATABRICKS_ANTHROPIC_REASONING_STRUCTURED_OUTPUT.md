# Databricks/Anthropic Reasoning + Structured Output

## Summary

PenguiFlow can hit this Databricks error on Claude-family models:

`Thinking may not be enabled when tool_choice forces tool use.`

This is not a PenguiFlow-only bug. It matches upstream Anthropic compatibility limits already seen in LiteLLM and PydanticAI.

## Root issue

Anthropic reasoning (`thinking`) is incompatible with request shapes that effectively force tool use for structured output.

In PenguiFlow's native planner path, planner action selection usually uses structured output. On Databricks Claude-family models, that structured path appears to map to Anthropic-style forced tool semantics. When native reasoning is also enabled, Databricks rejects the request with the error above.

So the conflict is:

1. planner wants structured action output
2. provider/native path turns that into forced structured/tool behavior
3. Anthropic `thinking` is enabled
4. provider rejects the combination

## Why LiteLLM path works today

The LiteLLM-like planner path works mainly because it avoids relying on the native structured-output path for planner actions.

Instead it uses plain text / JSON-like output and lets PenguiFlow parse and repair the JSON itself. That sidesteps the forced-tool structured request shape that Anthropic rejects when thinking is enabled.

This means the current working behavior is not proof that Anthropic supports reasoning with native structured planner actions. It is evidence that prompt-and-parse avoids the incompatible request shape.

## What the reference libraries show

### LiteLLM

LiteLLM PR `#9594` explicitly describes the fix as:

- do not set forced tool choice when `thinking` is enabled for response-format conversion
- ensure token budgeting still works correctly

This is compatible with the interpretation that the provider limitation is about `thinking + forced tool/structured output`.

### PydanticAI

PydanticAI issue `#2425` reports the same error when `output_type` forces structured output and Anthropic thinking is enabled.

PydanticAI issue `#2075` shows a related Anthropic thinking/tool protocol constraint: when tool use occurs across turns, prior thinking blocks must be replayed correctly.

PydanticAI PR `#2072` fixes that second class of issue by sending prior `ThinkingPart` data back when using tool calls. That fix is important, but it does **not** remove the underlying Anthropic restriction on `thinking + forced tool choice`.

## Current PenguiFlow workaround

PenguiFlow currently contains a narrow compatibility workaround in the native policy layer:

- for incompatible Databricks model families
- when the request mode is structured (`json_schema` / `json_object`)
- native reasoning injection is disabled

This prevents the Databricks crash.

## Why the workaround is not the real solution

In the native planner path, planner action requests are structured most of the time. On Databricks Claude-family models, that means the workaround disables native reasoning on most planner decision calls.

So while the workaround is safe, it effectively removes most planner-step reasoning for the exact path where reasoning was desired.

## Real solution

The correct long-term solution is:

1. keep native reasoning enabled for Anthropic/Databricks Claude-family planner calls
2. stop using native structured output for planner action generation on incompatible families
3. let PenguiFlow use prompt-and-parse JSON extraction for planner actions
4. preserve native structured output for providers/model families where structured output and reasoning are actually compatible

In practice, this means planner action generation should prefer a plain text JSON path for these incompatible Anthropic-backed families, while still using PenguiFlow's existing JSON extraction and repair logic.

## Why this matches the reference libraries

This direction is compatible with the upstream libraries:

- LiteLLM avoids forced tool choice when thinking is enabled
- PydanticAI distinguishes between forced-tool incompatibility and separate thinking-part replay requirements
- neither upstream fix demonstrates that Anthropic supports forced structured output with thinking enabled

So the PenguiFlow-compatible long-term design is not to force Anthropic structured output harder. It is to avoid the incompatible forced structured path for planner actions while preserving reasoning.

## Recommended PenguiFlow behavior

For Databricks Claude-family and similar Anthropic-backed incompatible families:

- planner actions: prompt + parse JSON, reasoning allowed
- native structured output: disabled for planner action generation
- other native features such as model failover/rate-limit handling: preserved where possible

For compatible families:

- continue using native structured output + reasoning where supported

## Bottom line

The issue is fundamentally an Anthropic compatibility limitation around `thinking` and forced structured/tool output.

The current policy workaround avoids crashes.

The real fix is to route planner action generation for incompatible families through PenguiFlow's plain-text JSON parsing path instead of native structured output, so reasoning can remain enabled without triggering the provider restriction.
