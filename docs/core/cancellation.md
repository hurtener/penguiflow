# Cancellation

## What it is / when to use it

Cancellation in the core runtime is **per trace** (`trace_id`).

Use it when:

- a user abandons a request,
- a deadline/budget is exceeded and you want to stop work,
- you need “stop the world for this trace” semantics across a fan-out graph.

## Non-goals / boundaries

- Cancellation is best-effort. If a node does blocking I/O without await points, it cannot be interrupted cleanly.
- The runtime does not emit a built-in “cancelled final answer” to Rookery. Cancellation stops work; it does not automatically produce a user-visible result.
- `cancel(trace_id)` only applies to trace-scoped messages (use `Message` envelopes).

## Contract surface

### `PenguiFlow.cancel(trace_id)`

```python
cancelled = await flow.cancel(trace_id)
```

- returns `True` if the trace was active and cancellation was triggered
- returns `False` if the trace was not found

When cancelling, the runtime:

- sets a per-trace cancellation event,
- drops queued messages for that trace from edge queues and fetch queues,
- cancels in-flight node invocation tasks associated with that trace.

### Trace-scoping

A message is “trace-scoped” if it has a readable `.trace_id` attribute.

Most production deployments use the envelope:

```python
from penguiflow import Headers, Message

message = Message(payload=..., headers=Headers(tenant="acme"))
await flow.emit(message)
```

See **[Messages & envelopes](messages-and-envelopes.md)**.

### `TraceCancelled`

Internally, cancellation is represented by `TraceCancelled` and `asyncio.CancelledError` paths.
You usually don’t catch these in your node code; instead, write nodes that are cancellation-friendly.

## Operational defaults

- Always attach a `trace_id` (use `Message`) for request-scoped work you may want to cancel.
- Keep node code cooperative:
  - avoid blocking I/O in nodes
  - call async SDKs with timeouts
  - don’t swallow `asyncio.CancelledError`
- If you start external tasks, make them trace-aware and cancel them in your orchestrator (the runtime doesn’t automatically cancel arbitrary external tasks on `cancel(trace_id)`).

## Failure modes & recovery

### Cancel returns `False`

**Likely causes**

- you cancelled a trace that never existed or already completed

**Fix**

- ensure you cancel the same `trace_id` you emitted

### Cancelled work appears to “continue”

**Likely causes**

- node is doing blocking I/O and can’t be interrupted
- work is happening outside the runtime (external background tasks)

**Fix**

- make nodes cooperative (await points + timeouts)
- use your own cancellation wiring for external tasks (or check the runtime’s cancellation event)

## Observability

Cancellation shows up in `FlowEvent`:

- `trace_cancel_start`
- `trace_cancel_drop`
- `node_trace_cancelled`

Alerting ideas:

- rising cancellation rate (could indicate timeouts/UX issues)
- traces with high `trace_pending` that are frequently cancelled (backpressure problems)

## Security / multi-tenancy notes

- Treat `trace_id` as an authorization surface: a user must not be able to cancel another user’s trace.
- Use `Headers.tenant` to enforce tenant scoping at your ingress layer.

## Runnable example: best-effort cancel

This example starts a long-running node, then cancels the trace and demonstrates that no final result is produced.

```python
from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, NodePolicy, create


async def slow(msg: Message, _ctx) -> None:
    del msg
    await asyncio.sleep(10.0)


slow_node = Node(slow, name="slow", policy=NodePolicy(validate="none"))


async def main() -> None:
    flow = create(slow_node.to())
    flow.run()

    message = Message(payload={"work": "x"}, headers=Headers(tenant="demo"))
    await flow.emit(message, trace_id=message.trace_id)

    cancelled = await flow.cancel(message.trace_id)
    print("cancelled:", cancelled)

    try:
        await asyncio.wait_for(flow.fetch(trace_id=message.trace_id), timeout=0.2)
        print("unexpected result")
    except asyncio.TimeoutError:
        print("no result (expected)")

    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **Cancel does nothing**: ensure your messages have a `trace_id` (use `Message`).
- **Cancel is slow**: nodes may be blocked; add timeouts and avoid blocking I/O.
- **External work continues**: cancel your external tasks explicitly; don’t assume runtime cancellation covers them.
