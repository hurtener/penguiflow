# Actions & schema (ReactPlanner)

## What this is / when to use it

This page documents the **LLM-facing action contract** used by `ReactPlanner`.

You need this when you are:

- authoring or auditing prompts that produce actions,
- integrating a custom `JSONLLMClient`,
- debugging invalid tool calls / parallel plans / final responses.

## Non-goals / boundaries

- This is not a full prompt reference. The canonical prompt assembly lives in `penguiflow/planner/prompts.py`.
- This page does not describe every internal model field, only the parts that affect behavior and reliability.

## Contract surface

### Unified action schema

The LLM output is a single JSON object with:

- `next_node: string` (tool name or opcode)
- `args: object` (tool args payload, defaults to `{}`)

In Python this is `PlannerAction`:

- `penguiflow.planner.models.PlannerAction`

### Special `next_node` values

These values are opcodes, not tool names:

- `final_response`: terminal answer
- `parallel`: parallel tool execution with optional join
- `task.subagent`: background subagent task
- `task.tool`: background single-tool job

Anything else is treated as a **tool call**.

## Operational defaults

- **Always validate** tool args against the tool’s Pydantic args model.
- Prefer **explicit join injection** for parallel joins (see below).
- Keep tool outputs bounded; use artifacts for large/binary payloads.

## Parallel execution (`next_node="parallel"`)

### Shape

```json
{
  "next_node": "parallel",
  "args": {
    "steps": [
      {"node": "tool_a", "args": {"q": "..." }},
      {"node": "tool_b", "args": {"id": "..." }}
    ],
    "join": {
      "node": "join_tool",
      "args": {},
      "inject": {"results": "$results", "expect": "$expect"}
    }
  }
}
```

### Join injection sources

`args.join.inject` maps **join tool arg fields** to these sources:

- `$results`: list of successful tool outputs
- `$branches`: branch details (node + args + observation/error)
- `$failures`: list of failures with errors (when present)
- `$success_count`: number of successful branches
- `$failure_count`: number of failed branches
- `$expect`: expected number of branches

### Join execution rules (important)

- Branch tools run concurrently.
- If **any branch fails**, the join is **skipped** with reason `branch_failures`.
- If the join tool is executed and it fails validation or raises, the join is recorded as an error in the parallel observation.

!!! warning
    Implicit join injection (auto-filling `results`, `expect`, etc. when `inject` is missing) exists for backward compatibility but is deprecated.
    Prefer explicit `inject` mapping.

## Final answer (`next_node="final_response"`)

Terminal actions are expected to place user-facing text in `args.answer`.

Common optional fields include:

- `artifacts` (heavy tool outputs or references)
- `sources` (citations)
- `suggested_actions`
- `confidence` (0..1)
- `language` (ISO 639-1)

## Failure modes & recovery

### Invalid JSON / schema mismatch

ReactPlanner includes repair and “arg-fill” paths for malformed actions:

- `repair_attempts`: how many times the planner tries to repair invalid output
- `arg_fill_enabled`: when the tool name is correct but args are missing/invalid, ask the LLM only for missing fields
- `max_consecutive_arg_failures`: if exceeded, the planner forces a finish with `requires_followup=True` (to avoid infinite loops)

### Unknown tool name

If `next_node` is not in the tool catalog, the planner will produce a structured validation error and re-prompt or finish depending on budget/settings.

## Observability

Planner actions and validation/repair events are emitted via `PlannerEvent` when `event_callback` is configured.

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Never allow tool names that are not in the catalog.
- Do not embed secrets in `llm_context` or action args. Use `tool_context` for secrets and opaque objects.
- Treat any tool with `side_effects="write"` or `"external"` as high risk: prefer allowlists and HITL gating.

## Troubleshooting checklist

- **Parallel join skipped unexpectedly**: check if any branch had errors (join is skipped on branch failures).
- **Join tool args validation fails**: ensure `join.inject` maps fields to valid injection sources and that the join tool’s args model matches injected shapes.
- **Planner stuck repairing args**: reduce tool surface, add examples, enable arg-fill, and confirm model can produce strict JSON.

## Runnable example: scripted action outputs (no network)

This shows the minimal action contract end-to-end by scripting a `JSONLLMClient` to emit two actions:

1. a tool call (`next_node="echo"`)
2. a finish (`next_node="final_response"`)

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient


class EchoArgs(BaseModel):
    text: str


class EchoOut(BaseModel):
    response: str


@tool(desc="Echo input", side_effects="pure")
async def echo(args: EchoArgs, ctx: ToolContext) -> EchoOut:
    del ctx
    return EchoOut(response=args.text)


class ScriptedClient(JSONLLMClient):
    def __init__(self) -> None:
        self._step = 0

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        del messages, response_format, stream, on_stream_chunk
        self._step += 1
        if self._step == 1:
            return json.dumps({"next_node": "echo", "args": {"text": "hello"}}, ensure_ascii=False)
        return json.dumps({"next_node": "final_response", "args": {"answer": "done"}}, ensure_ascii=False)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoOut)
    catalog = build_catalog([Node(echo, name="echo")], registry)

    planner = ReactPlanner(llm_client=ScriptedClient(), catalog=catalog)
    result = await planner.run("demo", tool_context={"session_id": "demo"})
    assert isinstance(result, PlannerFinish)
    print(result.reason, getattr(result.payload, "raw_answer", result.payload))


if __name__ == "__main__":
    asyncio.run(main())
```
