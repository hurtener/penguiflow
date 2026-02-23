# Steering (runtime control plane)

## What it is / when to use it

“Steering” is the mechanism for sending **out-of-band control events** to a running `ReactPlanner` (and its tools), such as:

- cancel the run immediately,
- redirect the goal while it is working,
- inject a correction/note into the LLM-visible context,
- pause/resume a task in session-backed runtimes,
- deliver a user message while background tasks are running.

Use steering when you have an interactive UI (chat/websocket) and you want:

- responsive cancellation,
- mid-flight clarification/redirect without losing state,
- a unified way to control foreground + background task execution.

## Non-goals / boundaries

- Steering is not a persistence layer. If you need durable steering logs, store the events in your own store.
- Steering is not an auth system. You must gate who is allowed to send steering events.
- Steering does not guarantee delivery; inbox queues are bounded and `push(...)` can return `False`.

## Contract surface

### Core types

- `SteeringEvent` / `SteeringEventType`: `penguiflow.state.models`
- `SteeringInbox`: `penguiflow.steering`
- validation/sanitization helpers:
  - `validate_steering_event(event)`
  - `sanitize_steering_event(event)`

### Attaching steering to planner calls

Provide a steering inbox per run/resume:

- `await planner.run(..., steering=inbox)`
- `await planner.resume(..., steering=inbox)`

When a steering inbox is attached:

- tool execution can be interrupted on CANCEL (tools are awaited concurrently with the inbox cancel event),
- the runtime drains steering events between steps and injects them into the next LLM call.

### Event types (high-level semantics)

Common event types and their intended use:

- `INJECT_CONTEXT`: add a note/correction into the LLM-visible steering injection stream
- `REDIRECT`: change the active goal/query for the next planning iteration
- `USER_MESSAGE`: deliver a user message that arrived “mid-flight” (often includes `active_tasks`)
- `CANCEL`: cancel the run; raises `SteeringCancelled`

Session-backed runtimes may also use:

- `APPROVE` / `REJECT`: approve/reject a pending patch or resume token
- `PAUSE` / `RESUME`: flow-control to pause/resume task processing
- `PRIORITIZE`: reprioritize a background task

!!! warning
    Steering payloads are LLM-visible once injected. Do not include secrets.

## Operational defaults (recommended)

- Use a per-session inbox; keep it alive for the duration of a user session.
- Validate and sanitize events at the transport boundary:
  - reject invalid payload shapes (`validate_steering_event`)
  - clamp size/depth (`sanitize_steering_event`)
- Treat `CANCEL` as a hard stop and surface it explicitly to callers (don’t swallow `SteeringCancelled`).

## Failure modes & recovery

### Steering appears to do nothing

**Likely causes**

- you forgot to pass `steering=inbox` into `run(...)` / `resume(...)`
- you are pushing events to a different inbox instance than the one the planner is using

### `push(...)` returns `False`

**Likely causes**

- inbox queue is full, or you exceeded `max_pending_user_messages`

**Fix**

- increase `SteeringInbox(maxsize=...)` for your workload
- apply backpressure at your websocket/UI layer (drop or coalesce repeated USER_MESSAGE events)

### Cancellation doesn’t interrupt a long-running tool

**Likely causes**

- tool code ignores cancellation (CPU-bound work or blocking I/O without await points)

**Fix**

- ensure tools are async and await regularly (or offload CPU-bound work to threads/processes)

## Observability

`ReactPlanner` emits `PlannerEvent(event_type="steering_received")` for each drained event (with `event_id`, `event_type`, `source`).

The runtime also records a bounded `steering_history` list in `trajectory.metadata` (for debugging), and can defer finishing if steering arrives while the LLM is responding.

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Authenticate and authorize steering senders. Treat steering as an admin/control channel.
- Sanitize payloads before persisting or re-injecting; do not allow arbitrary deep objects.
- Do not let tenants inject tool_context by steering. Steering is LLM-visible context only.

## Runnable example: cancel a long tool call

This example demonstrates:

- attaching a `SteeringInbox`,
- sending a `CANCEL` event while a tool is running,
- catching `SteeringCancelled`.

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node
from penguiflow.catalog import build_catalog, tool
from penguiflow.planner import ReactPlanner, ToolContext
from penguiflow.planner.models import JSONLLMClient
from penguiflow.steering import SteeringCancelled, SteeringInbox
from penguiflow.state.models import SteeringEvent, SteeringEventType


class SleepArgs(BaseModel):
    seconds: float = 10.0


class SleepOut(BaseModel):
    ok: bool


@tool(desc="Sleep for a while", side_effects="read")
async def sleepy(args: SleepArgs, ctx: ToolContext) -> SleepOut:
    del ctx
    await asyncio.sleep(args.seconds)
    return SleepOut(ok=True)


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
            return json.dumps({"next_node": "sleepy", "args": {"seconds": 10}}, ensure_ascii=False)
        return json.dumps({"next_node": "final_response", "args": {"answer": "done"}}, ensure_ascii=False)


async def main() -> None:
    registry = ModelRegistry()
    registry.register("sleepy", SleepArgs, SleepOut)
    catalog = build_catalog([Node(sleepy, name="sleepy")], registry)

    planner = ReactPlanner(llm_client=ScriptedClient(), catalog=catalog)
    inbox = SteeringInbox()

    async def cancel_soon() -> None:
        await asyncio.sleep(0.2)
        await inbox.push(
            SteeringEvent(
                session_id="demo",
                task_id="foreground",
                event_type=SteeringEventType.CANCEL,
                payload={"reason": "user_cancelled"},
            )
        )

    try:
        await asyncio.gather(planner.run("demo", steering=inbox, tool_context={"session_id": "demo"}), cancel_soon())
    except SteeringCancelled as exc:
        print(f"cancelled: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- Are you passing the same inbox instance into `run(...)`/`resume(...)` that you push events into?
- Are you validating and sanitizing events at ingress?
- Are your tools cancellation-friendly (async, not blocking)?
- Are you surfacing `SteeringCancelled` explicitly to your callers/UI?

