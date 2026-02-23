# Flows & nodes

## What it is / when to use it

PenguiFlow’s core runtime executes a directed graph of async nodes with:

- bounded queues (backpressure),
- per-node reliability policy (timeouts + retries),
- optional typed validation (`ModelRegistry`),
- structured runtime events (`FlowEvent`) for observability.

Use the core runtime when you want deterministic pipelines or agent graphs where the *graph topology* is the orchestrator.

## Non-goals / boundaries

- The runtime is not a planner. It will not choose tools or invent steps (see `ReactPlanner` for that).
- It does not persist state by default. Durability is via an optional `StateStore`.
- It does not enforce multi-tenant authz; tenant scoping is a contract you implement (typically via message headers and separate ingress).

## Contract surface

### Nodes

`Node` wraps an async function with metadata:

- signature: `async def fn(message, ctx) -> Any`
- `NodePolicy` controls validation, timeouts, and retries
- `allow_cycle=True` permits self-cycles for loop-style graphs

`NodePolicy` (runtime):

- `validate`: `"both" | "in" | "out" | "none"`
- `timeout_s`: per-invocation timeout
- `max_retries`: retry count (total attempts = `max_retries + 1`)
- `backoff_base`, `backoff_mult`, `max_backoff`: exponential backoff parameters

### Graph construction

Graphs are defined by adjacency tuples:

```python
from penguiflow import Node, create

flow = create(
    a.to(b),
    b.to(),
)
```

`create(...)` returns a `PenguiFlow` runtime with key knobs:

- `queue_maxsize`: bounded queue size per edge (default 64, `<= 0` disables backpressure)
- `allow_cycles`: allow cycles in the graph (default False)
- `middlewares`: async hooks receiving `FlowEvent`
- `emit_errors_to_rookery`: route terminal `FlowError` objects to the sink so callers can fetch them
- `state_store`: persist runtime events and remote bindings
- `message_bus`: optional bus integration for edge traffic

### Endpoints: OpenSea and Rookery

The runtime synthesizes two endpoints:

- **OpenSea**: ingress (feeds nodes with no incoming edges)
- **Rookery**: egress sink (receives nodes with no outgoing edges)

This is why `await flow.emit(...)` and `await flow.fetch()` work “out of the box”.

### Messages (payload-only vs envelopes)

- Payload-only nodes pass Pydantic models directly.
- Envelope-based flows pass `Message(payload=..., headers=..., trace_id=...)` to enable cancellation, deadlines, and streaming.

See **[Messages & envelopes](messages-and-envelopes.md)**.

## Operational defaults (recommended)

- Keep `queue_maxsize` bounded (64 is a safe starting point).
- Use `ModelRegistry` + `NodePolicy(validate="both")` for typed boundaries.
- Use `Message` envelopes for production features (streaming/cancel/deadlines).
- Always `await flow.stop()` to avoid orphaned node workers.

## Failure modes & recovery

### Cycle errors

If your graph contains a cycle and `allow_cycles=False`, `create(...)` raises `CycleError`.

Fix:

- remove the cycle, or
- set `allow_cycles=True` (and ensure nodes are cycle-safe).

### Nothing ever reaches `fetch()`

Typical causes:

- no egress node exists (all nodes have successors), so nothing routes to Rookery
- your egress node returns `None` (no message emitted)
- you never `flow.run(...)` before emitting

Fix:

- ensure an egress node exists and returns a value (or emits to a sink),
- call `flow.run(...)` before `emit(...)`.

### Validation failures

If `NodePolicy.validate != "none"` and your registry is missing a model entry, `flow.run(registry=...)` fails fast.

Fix:

- register models for every validated node name, or set `validate="none"` for that node.

## Observability

The runtime emits structured `FlowEvent` objects and logs them (logger: `penguiflow.core`).

You can attach middleware to capture events:

- pass `middlewares=[...]` to `create(...)`, or
- call `flow.add_middleware(...)`.

If `state_store` is configured, runtime events are persisted via `StateStore.save_event(...)`.

## Security / multi-tenancy notes

- Don’t mix tenants inside a trace. Use `Headers.tenant` and enforce scoping at ingress/egress boundaries.
- Avoid storing secrets in message `meta` if you persist events or logs.
- Treat `trace_id` and `fetch(trace_id=...)` as an authorization surface in apps (don’t allow a user to fetch/cancel another user’s trace).

## Runnable example: minimal typed flow

```python
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, NodePolicy, create


class In(BaseModel):
    text: str


class Out(BaseModel):
    upper: str


async def to_upper(msg: In, _ctx) -> Out:
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

## Troubleshooting checklist

- **`RuntimeError: PenguiFlow already running`**: you called `run()` twice; create a new flow instance or stop first.
- **`RuntimeError: PenguiFlow is not running`**: call `flow.run(...)` before emitting.
- **`CycleError`**: remove cycles or enable `allow_cycles=True` (and make cycles explicit).
- **`fetch()` hangs**: nothing is reaching Rookery; confirm an egress node emits/returns a value.
