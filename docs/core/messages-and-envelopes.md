# Messages & envelopes

## What it is / when to use it

PenguiFlow supports two “message styles”:

1. **Payload-only**: nodes receive and return plain Pydantic models (fastest to start).
2. **Envelope-based** (recommended for production): nodes pass a `Message(payload=..., headers=..., trace_id=...)` so the runtime can:
   - route and correlate work by `trace_id`,
   - enforce per-trace cancellation and deadlines,
   - emit streaming chunks that inherit routing metadata.

Use envelopes when you need enterprise behavior: multi-tenant isolation, deterministic correlation, streaming, cancellation, and deadlines.

## Non-goals / boundaries

- `Message` does not encrypt or redact data; it is a container plus metadata.
- Headers and metadata are not a policy engine. Use tool visibility/policies and your app’s authz layer for security decisions.
- Envelopes don’t magically make blocking I/O cancellable; cancellation requires cooperative await points.

## Contract surface

### Core models

The envelope primitives live in `penguiflow.types`:

- `Headers(tenant: str, topic: str | None = None, priority: int = 0)`
- `Message(payload: Any, headers: Headers, trace_id: str, deadline_s: float | None, meta: dict)`
- `StreamChunk(stream_id: str, seq: int, text: str, done: bool, meta: dict)`
- `FinalAnswer(text: str, citations: list[str])`

### Runtime helpers and rules

Runtime entry points:

- `await flow.emit(msg)` sends a message to ingress nodes (OpenSea → ingress)
- `await flow.fetch()` reads from the egress sink (Rookery)
- `await flow.cancel(trace_id)` cancels trace-scoped work (best-effort, per-trace)

Important rules:

- A message is “trace-scoped” if it has a readable `.trace_id` attribute (the built-in `Message` does).
- Deadlines are enforced when a node receives a `Message(deadline_s=...)`:
  - expired messages are skipped and (for `Message`) a `FinalAnswer(text="Deadline exceeded")` is emitted to Rookery.

### Trace-scoped roundtrips (`emit(trace_id=...)` + `fetch(trace_id=...)`)

For request/response semantics, you can use trace-scoped emit/fetch:

- `await flow.emit(msg, trace_id=trace_id)`
- `await flow.fetch(trace_id=trace_id)`

Behavior:

- `emit(trace_id=...)` attaches/overrides `.trace_id` on the message.
- it acquires a per-trace “roundtrip lock” so concurrent roundtrips for the same trace are serialized.
- the runtime switches to a trace-scoped Rookery dispatcher:
  - `fetch(from_=...)` filtering is **not supported** once trace-scoped fetching is enabled
  - `fetch(trace_id=...)` does not support `from_` filtering

Use trace-scoped roundtrips when multiple traces are active and you need deterministic correlation.

## Operational defaults

- Always set `Headers.tenant` (multi-tenant boundary).
- Treat `trace_id` as the correlation key; generate one per user session or per request.
- Keep `meta` JSON-friendly if you plan to persist or forward events.
- Prefer returning `Message(payload=FinalAnswer(...))` as the canonical “done” signal for envelope flows.

## Failure modes & recovery

### Mixed payload-only and envelope flows

If you mix payload-only and `Message` envelopes in one graph, you can lose metadata propagation.

Fix:

- pick one style per flow, or
- ensure envelope nodes take and return `Message` consistently.

### Trace-scoped fetch surprises

If you start using `emit(trace_id=...)` / `fetch(trace_id=...)`, `fetch(from_=...)` is no longer allowed.

Fix:

- keep a single “egress contract” (route final results to Rookery), and
- use `trace_id` for correlation rather than `from_` filtering.

### Deadline exceeded “silently”

Expired `Message(deadline_s=...)` inputs are skipped and a deadline final answer can be emitted.

Fix:

- set deadlines deliberately and monitor deadline skip events (see Observability),
- propagate deadlines to downstream messages when appropriate.

## Observability

The runtime emits structured `FlowEvent` objects for node lifecycle and trace behavior:

- `node_start`, `node_success`, `node_error`, `node_timeout`, `node_retry`, `node_failed`
- `deadline_skip`
- `trace_cancel_start`, `trace_cancel_drop`

`FlowEvent` includes queue depths and trace-level pending/inflight counts, which are essential for debugging backpressure.

You can attach middleware to capture events:

- `PenguiFlow(..., middlewares=[...])`
- or `flow.add_middleware(...)`

See `penguiflow.middlewares.log_flow_events` for a ready-made structured logger.

## Security / multi-tenancy notes

- `Headers.tenant` is the default tenant boundary; never route cross-tenant messages through the same trace id.
- Do not store secrets in `meta` unless your event storage and logs are equally protected.
- Treat artifacts and external tool outputs as sensitive; keep large/binary data out of `payload` unless you intentionally accept prompt bloat.

## Runnable example: envelope flow with streaming and a final answer

This example:

- emits a `Message` with a tenant header,
- streams `StreamChunk`s to a chunk sink (same `trace_id`),
- emits a final `FinalAnswer` to Rookery.

```python
from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, NodePolicy, create
from penguiflow.types import FinalAnswer


async def chunk_sink(msg: Message, _ctx) -> None:
    chunk = msg.payload
    print(chunk.text, end="")
    if chunk.done:
        print("")


async def compose(msg: Message, ctx) -> None:
    await ctx.emit_chunk(parent=msg, text="hello ", to=chunk_node)
    await ctx.emit_chunk(parent=msg, text="world", done=True, to=chunk_node)
    await ctx.emit(msg.model_copy(update={"payload": FinalAnswer(text="hello world")}), to=final_node)


async def deliver_final(msg: Message, _ctx) -> FinalAnswer:
    return msg.payload


chunk_node = Node(chunk_sink, name="chunk_sink", policy=NodePolicy(validate="none"))
final_node = Node(deliver_final, name="final", policy=NodePolicy(validate="none"))
compose_node = Node(compose, name="compose", policy=NodePolicy(validate="none"))


async def main() -> None:
    flow = create(
        compose_node.to(chunk_node, final_node),
        chunk_node.to(),
        final_node.to(),
    )
    flow.run()

    message = Message(payload={"request": "ignored"}, headers=Headers(tenant="demo"))
    await flow.emit(message, trace_id=message.trace_id)
    result = await flow.fetch(trace_id=message.trace_id)
    print("Final:", result.text)
    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **No streaming output**: ensure you are calling `ctx.emit_chunk(parent=Message(...), ...)` and routing to a sink node.
- **`fetch(trace_id=...)` errors**: don’t pass `from_` filtering; ensure you used `emit(..., trace_id=...)` or have a dispatcher running.
- **Cross-trace mixups**: always use unique `trace_id` per request/session and set `Headers.tenant`.
