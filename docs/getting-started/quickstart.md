# Quickstart

## What it is / when to use it

This page focuses on runnable “hello world” examples that match the current API.

Use it when you want:

- a minimal typed flow you can run in minutes,
- pointers to the next “enterprise” concepts (envelopes, streaming, cancellation),
- the fastest path to a ReactPlanner project via the CLI.

For deeper explanations, see **[Concepts](concepts.md)**.

## Non-goals / boundaries

- These examples are intentionally small; they don’t include production persistence, auth, or deployment.
- The flow example uses payload-only messages by default (envelopes are recommended for production features).

## Contract surface (what you’re exercising)

- Graph construction: `Node(...).to(...)` + `create(...)`
- Runtime lifecycle: `flow.run(...)`, `flow.emit(...)`, `flow.fetch(...)`, `flow.stop()`
- Optional typing: `ModelRegistry` + `NodePolicy(validate=...)`

## 1) Minimal flow (typed async pipeline)

This example uses **payload-only** messages (fastest to start). For streaming/cancellation/deadlines, see “Message envelopes” below.

```python
from __future__ import annotations

import asyncio
from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, NodePolicy, create


class In(BaseModel):
    text: str


class Out(BaseModel):
    upper: str


async def to_upper(msg: In, ctx) -> Out:
    return Out(upper=msg.text.upper())


async def main() -> None:
    node = Node(to_upper, name="to_upper", policy=NodePolicy(validate="both"))

    registry = ModelRegistry()
    registry.register("to_upper", In, Out)

    flow = create(node.to())
    flow.run(registry=registry)

    await flow.emit(In(text="hello"))
    result: Out = await flow.fetch()
    await flow.stop()

    print(result.upper)


if __name__ == "__main__":
    asyncio.run(main())
```

### Message envelopes (recommended for streaming/cancel/deadlines)

PenguiFlow can also run with a message envelope:

- `Message(payload=..., headers=Headers(...), trace_id=...)`
- Enables per-trace cancellation and streaming chunks that inherit routing metadata.

See:

- **[Messages & envelopes](../core/messages-and-envelopes.md)**
- **[Streaming](../core/streaming.md)**
- **[Cancellation](../core/cancellation.md)**

## 2) ReactPlanner (LLM-driven orchestration)

If you’re starting from scratch, the fastest path is using the CLI template:

```bash
uv run penguiflow new my-agent --template react
uv run penguiflow dev --project-root my-agent
```

Planner docs:

- **[ReactPlanner overview](../planner/overview.md)**
- **[Tooling (ToolNode)](../planner/tooling.md)**
- **[Pause/resume (HITL)](../planner/pause-resume-hitl.md)**

## Examples you can run

The repository includes runnable examples:

```bash
uv run python examples/quickstart/flow.py
uv run python examples/roadmap_status_updates/flow.py
```

## Operational defaults

- Always call `flow.stop()` to clean up workers.
- Keep node signatures `async def fn(message, ctx)` (two positional params).
- Prefer bounded outputs; use artifacts for large blobs in production.

## Failure modes & recovery

- **`fetch()` hangs**: nothing is reaching the Rookery sink; ensure an egress node exists and returns/emits a value.
- **Validation errors**: ensure `ModelRegistry` includes entries for nodes with `validate != "none"`.
- **Streaming doesn’t work**: use envelope style (`Message`) so you can call `ctx.emit_chunk(parent=Message(...))`.

## Observability

The runtime emits structured `FlowEvent`. In production, attach middleware and/or a `StateStore` to persist events.

## Security / multi-tenancy notes

- Use `Headers.tenant` in envelope flows and keep `trace_id` scoped per request/session.
- Don’t embed secrets in message payloads if you persist events or logs.

## Troubleshooting checklist

- **`RuntimeError: PenguiFlow is not running`**: call `flow.run(...)` before emitting.
- **CLI templates fail**: verify `uv` and the required extras are installed (`penguiflow[planner]`).
