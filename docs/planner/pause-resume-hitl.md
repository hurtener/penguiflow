# Pause/resume (HITL)

## What it is / when to use it

`ReactPlanner` supports pause/resume so tools can request human input mid-run:

- approvals (“is it OK to run this write tool?”)
- OAuth / authentication steps (handoff to a browser/login flow)
- policy gating (allowlist, scope escalation)
- “human edits” to context (user corrects data before continuing)

Pauses are **first-class outputs** of the planner: `run(...)` and `resume(...)` can both return `PlannerPause`.

## Non-goals / boundaries

- Pause/resume does not define a UI protocol. It only carries a `payload` you define.
- The library does not enforce token TTL or one-time use. If you need that, implement it in your `StateStore`.
- Pause state is **not durable by default**; durability is an opt-in via `state_store`.

## Contract surface

### Tool-side: `ToolContext.pause(...)`

Planner tools can pause by calling:

- `await ctx.pause(reason, payload)`

`reason` is one of:

- `approval_required`
- `await_input`
- `external_event`
- `constraints_conflict`

`payload` must be JSON-friendly (you are expected to render it in your UI).

### Planner-side: `PlannerPause`

When paused, the planner returns:

- `PlannerPause.reason`: the pause reason (above)
- `PlannerPause.payload`: your structured payload
- `PlannerPause.resume_token`: opaque token required to resume

### Resume: `ReactPlanner.resume(...)`

Resume uses:

```python
result = await planner.resume(
    pause.resume_token,
    user_input="approved",
    tool_context={"session_id": "..."},
)
```

Key details:

- `user_input` is optional; use it for approval decisions, pasted OAuth codes, etc.
- `tool_context` on resume **overrides** any tool_context captured in the pause record.
- `memory_key` can be passed again if you use explicit memory isolation.

### Distributed durability: `StateStore` capability

If you pass `state_store=...` to `ReactPlanner(...)`, pause records are stored:

1. in-memory (always), and
2. in the state store **if** it provides `save_planner_state(token, payload)` / `load_planner_state(token)`.

See `penguiflow.state.protocol.SupportsPlannerState`.

!!! note
    A failing state store does not prevent pausing: the pause is already recorded in memory.
    Save/load errors are logged and then ignored (pause still returns).

## Operational defaults

- Always pass a session identifier:
  - set `tool_context={"session_id": "..."}`
  - or pass a `MemoryKey(session_id=...)`

This enables per-session dispatch so one planner instance can safely serve concurrent sessions.

- Treat `resume_token` as a secret (it is an authorization capability).
- Keep pause payload small; large/binary data should go into artifacts and referenced by id.
- If you run distributed workers, configure a state store with `save_planner_state` and `load_planner_state`.

## Failure modes & recovery

### Invalid/expired token (`KeyError`)

**Symptoms**

- `planner.resume(...)` raises `KeyError`

**Likely causes**

- in-memory pause record was lost (process restart) and state store is not configured
- state store does not implement `load_planner_state`
- token was never saved (save failed) and the process restarted

**Fix**

- provide a durable `StateStore` implementing planner state support
- re-run the session from the last durable checkpoint (application-specific)

### Tool context lost on resume

Pause records attempt to serialize `tool_context` to JSON when storing to a state store.
If your `tool_context` contains non-serializable objects, the stored record may drop `tool_context`.

**Fix**

- pass `tool_context` explicitly again on `resume(...)`
- keep only JSON-friendly routing keys in `tool_context` (e.g. `session_id`, `tenant_id`) and store real clients elsewhere

### Pause disabled

If `pause_enabled=False` on the planner, calling `ctx.pause(...)` fails.

**Fix**

- enable pause or remove HITL flows for that deployment

## Observability

- Use `event_callback` to record pause/resume/finish events and resume latency.
- Track counts by pause reason and tool name (which tool requested HITL).
- For distributed systems, also track state store save/load failure rates for pause state.

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- A pause payload is application-defined: treat it as potentially sensitive.
- Never include secrets directly in the payload; store them in `tool_context` or a secret store.
- Namespace resume tokens per tenant/user at the application layer (or ensure state store keys are effectively unguessable and scoped).

## Runnable example: approval-gated tool (pattern)

!!! warning
    `ctx.pause(...)` interrupts execution by raising an internal signal. The tool does not “resume mid-function”.
    After you call `planner.resume(...)`, the planner continues from the saved trajectory and the LLM chooses what to do next.

This pattern gates a write tool by reading an approval flag from `tool_context` (which you can change between pause and resume).

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import PlannerPause, ReactPlanner, ToolContext


class WriteArgs(BaseModel):
    text: str


class WriteOut(BaseModel):
    ok: bool


@tool(desc="Write a line to a system (approval required)", side_effects="write")
async def write_line(args: WriteArgs, ctx: ToolContext) -> WriteOut:
    approvals = ctx.tool_context.get("approvals") or {}
    if not approvals.get("write_line"):
        await ctx.pause(
            "approval_required",
            {"title": "Approve write", "preview": args.text, "approval_key": "write_line"},
        )

    # Safe to execute: approval is provided by the orchestrator via tool_context.
    del args, ctx
    return WriteOut(ok=True)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("write_line", WriteArgs, WriteOut)
    catalog = build_catalog([Node(write_line, name="write_line")], registry)

    planner = ReactPlanner(llm="gpt-4o-mini", catalog=catalog)
    tool_ctx = {"session_id": "demo", "approvals": {}}
    result = await planner.run("Write 'hello' to the system", tool_context=tool_ctx)

    while isinstance(result, PlannerPause):
        # In real systems: render result.payload in your UI and wait for user action.
        tool_ctx = {"session_id": "demo", "approvals": {"write_line": True}}
        result = await planner.resume(
            result.resume_token,
            user_input="approved",
            tool_context=tool_ctx,
        )

    print(result.reason, getattr(result.payload, "raw_answer", result.payload))


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **Token invalid after deploy/restart**: you need a `StateStore` that persists planner pause state.
- **Resume continues with missing clients**: pass `tool_context` again on resume.
- **Pauses never happen**: ensure `pause_enabled=True` and your tool actually calls `ctx.pause(...)`.
