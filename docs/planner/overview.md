# ReactPlanner overview

## What it is / when to use it

`ReactPlanner` is PenguiFlow’s JSON-only, ReAct-style planner loop:

1. Ask the LLM for a **typed JSON action** (`PlannerAction`).
2. Execute the action (tool call / parallel plan / pause / final answer).
3. Repeat until the planner finishes (or pauses).

Use it when you want an LLM-driven workflow that is:

- **schema-constrained** (Pydantic validation + repair/arg-fill),
- **interruptible** (pause/resume for approval/OAuth),
- **operationally observable** (structured `PlannerEvent` stream),
- **composable** (tools come from the same Node + catalog system).

!!! tip
    Planner features require installing `penguiflow[planner]`.

## Non-goals / boundaries

- `ReactPlanner` is **not a workflow engine** on its own; it orchestrates *tool calls* and returns a structured result.
- It does **not** provide durable session storage by default. Durability is via an optional `StateStore`.
- It is **not thread-safe** and must not be used concurrently from multiple tasks on the same instance.

!!! warning
    `ReactPlanner` has mutable per-run state. It provides an internal *per-session dispatch* hotfix:
    if you supply a `session_id` (in `tool_context` or `MemoryKey`) calls are serialized per session.
    If you do not provide a session id, calls are serialized globally on a fallback lock.

## Contract surface

### Inputs

At construction time you provide:

- an LLM: `llm="..."` (LiteLLM) **or** `llm_client=JSONLLMClient`
- a tool catalog: `catalog=[NodeSpec, ...]` **or** `nodes=[Node, ...]` + `registry=ModelRegistry`

Per call you commonly provide:

- `query: str`
- `llm_context: Mapping[str, Any] | None` (JSON-only, LLM-visible)
- `tool_context: Mapping[str, Any] | None` (tool-only, may include secrets/clients)
- `tool_visibility: ToolVisibilityPolicy | None` (dynamic per-call tool filtering)
- `memory_key: MemoryKey | None` (explicit memory isolation key)

### Output types

`ReactPlanner.run(...)` and `ReactPlanner.resume(...)` return:

- `PlannerFinish` with `reason: "answer_complete" | "no_path" | "budget_exhausted"` and `payload` (commonly a `FinalPayload`-shaped object)
- `PlannerPause` with `reason`, `payload`, and `resume_token`

### Pause/resume contracts

- Tools can pause via `ctx.pause(reason=..., payload=...)` (see **[Pause/resume (HITL)](pause-resume-hitl.md)**).
- Resume uses `planner.resume(token, user_input=..., tool_context=..., memory_key=...)`.

### Context split (must-know)

Planner calls accept two context surfaces:

- `llm_context`: JSON-serializable context visible to the LLM (facts, UI state, memory payloads)
- `tool_context`: tool-only context not visible to the LLM (clients, callbacks, secrets, opaque objects)

Treat the split as a **security boundary**.

## Operational defaults (recommended starting points)

- `json_schema_mode=True`, `temperature=0.0` (reduce invalid JSON rate)
- Keep tools **small** (args/output) and push large/binary outputs into artifacts
- Enable guardrails deliberately:
  - `repair_attempts` (default 3) and `arg_fill_enabled=True`
  - set `max_consecutive_arg_failures` to force a safe finish instead of looping
- Enforce budgets in production:
  - `max_iters` (default 8), `hop_budget`, and `deadline_s`
- Set concurrency intentionally:
  - planner safety: `absolute_max_parallel` (default 50)
  - per-call: `planning_hints.max_parallel`
  - per-tool source: ToolNode `ExternalToolConfig.max_concurrency`

## Failure modes & recovery

- **Invalid JSON / schema mismatches**: increase model quality, keep `json_schema_mode=True`, add tool examples, keep schemas small.
- **Unknown tool / “tool not found”**: ensure the tool exists in the catalog and is visible under `tool_policy` / `tool_visibility`.
- **Loops on invalid args**: rely on `arg_fill_enabled` and cap with `max_consecutive_arg_failures`.
- **Pause tokens lost across restarts**: configure a `StateStore` that supports planner pause state persistence (see pause page).
- **Accidental serialization of all work**: provide `tool_context["session_id"]` (or `MemoryKey.session_id`) so per-session dispatch can serialize per session instead of globally.

## Observability

`ReactPlanner` emits a structured event stream via `event_callback`:

- `PlannerEvent(event_type=..., extra=...)` for step start/complete, tool calls, LLM calls, pause/resume, streaming chunks, and finish.

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Never put secrets (tokens, tenant internal ids, client objects) into `llm_context`.
- Use `ToolPolicy` / `ToolVisibilityPolicy` to enforce per-tenant tool availability.
- For memory, prefer explicit `MemoryKey` and keep `MemoryIsolation.require_explicit_key=True` in multi-tenant services.
- Treat `resume_token` as a secret; it is effectively an authorization capability.

## Minimal runnable example

Small “typed tool + planner” setup:

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerPause, ReactPlanner, ToolContext


class Query(BaseModel):
    text: str


class Answer(BaseModel):
    response: str


@tool(desc="Answer a question", tags=["demo"])
async def answer_query(args: Query, ctx: ToolContext) -> Answer:
    del ctx
    return Answer(response=f"Echo: {args.text}")


async def main() -> None:
    registry = ModelRegistry()
    registry.register("answer_query", Query, Answer)

    node = Node(answer_query, name="answer_query")
    catalog = build_catalog([node], registry)

    planner = ReactPlanner(llm="gpt-4o-mini", catalog=catalog)
    result = await planner.run("Say hi", tool_context={"session_id": "demo"})

    if isinstance(result, PlannerPause):
        raise RuntimeError(f"unexpected pause: {result.reason}")

    if result.reason != "answer_complete":
        raise RuntimeError(f"unexpected finish reason: {result.reason}")

    payload = result.payload
    print(getattr(payload, "raw_answer", payload))


if __name__ == "__main__":
    asyncio.run(main())
```

## Key integrations

- Production configuration: **[Configuration](configuration.md)**
- Tool execution: **[Tooling](tooling.md)** (ToolNode for MCP/UTCP/HTTP)
- Safety/policy: **[Guardrails](guardrails.md)**
- Human-in-the-loop: **[Pause/resume (HITL)](pause-resume-hitl.md)**
- Short-term memory: **[Memory](memory.md)**
- Runtime control: **[Steering](steering.md)**
- Concurrent work: **[Background tasks](background-tasks.md)**
- Provider integration: **[Native LLM layer](native-llm.md)** and **[LLM clients](llm-clients.md)**

## Troubleshooting checklist

- **Planner pauses unexpectedly**: check which tool called `ctx.pause(...)` and validate policy/HITL expectations.
- **No tool calls happen**: verify the catalog is non-empty and tool visibility is not filtering everything out.
- **Planner is serializing everything**: ensure `tool_context["session_id"]` is set.
- **Frequent invalid args**: simplify schemas and add `@tool(examples=...)`.

See **[Planner troubleshooting](troubleshooting.md)** and the long-form integration guide in the repo root.
