# Concurrency

## What it is / when to use it

PenguiFlow runtime concurrency comes from two places:

- the graph itself (multiple nodes can run concurrently),
- bounded edge queues (backpressure) that prevent unbounded buffering.

Use these patterns when you need fan-out/fan-in with safe defaults:

- rate-limit work,
- avoid memory blow-ups,
- keep per-trace correlation stable.

## Non-goals / boundaries

- This page is about **core runtime** concurrency, not planner-level parallel tool execution.
- Concurrency does not imply ordering across branches; ordering is only guaranteed within a single queue/edge.

## Contract surface

### Backpressure: `queue_maxsize`

Each graph edge is an `asyncio.Queue(maxsize=queue_maxsize)`:

- default `queue_maxsize=64`
- `queue_maxsize <= 0` disables backpressure (unbounded queues)

Configure via:

```python
flow = create(..., queue_maxsize=128)
```

### Helper: `map_concurrent`

`map_concurrent(items, worker, max_concurrency=...)` runs an async worker over items with a semaphore.

### Helper: `join_k`

`join_k(name, k)` is a runtime `Node` that aggregates `k` messages **per trace_id** and emits a batch downstream.

Important:

- `join_k` requires messages with a `trace_id` (use `Message` envelopes).

## Operational defaults

- Keep queues bounded (`queue_maxsize` 64–256 is a typical range).
- Keep per-trace fan-out bounded (don’t emit thousands of messages for one trace without a join/limit).
- Prefer envelope-style for fan-out/fan-in (`Message`) so joins can correlate by `trace_id`.

## Failure modes & recovery

### Queue “deadlocks” / stalls

**Symptoms**

- `emit(...)` awaits forever
- queue depth grows and never drains

**Likely causes**

- downstream node is not running or is blocked
- fan-out rate exceeds consumer capacity

**Fix**

- add timeouts and cancellation, and size queues deliberately
- reduce fan-out or add a join/aggregation earlier

### Memory growth

**Likely causes**

- `queue_maxsize <= 0` (unbounded queues)
- joins that never complete (`join_k` waiting for `k` messages that never arrive)

**Fix**

- keep queues bounded
- ensure fan-out emits exactly `k` items per trace (or use a different join strategy)

## Observability

The runtime emits `FlowEvent` which includes:

- `queue_maxsize`, queue depths (in/out/total),
- `trace_pending` and `trace_inflight` counts (when trace-scoped),
- retry/timeout/cancel events.

Use these signals to:

- detect backpressure (queue depth trending up),
- alert on error/retry bursts,
- find hotspots (high latency nodes).

## Security / multi-tenancy notes

- `join_k` buckets by `trace_id`. Never reuse a trace id across tenants.
- Treat trace ids and per-trace fetch/cancel as an authorization surface in your app.

## Runnable examples

### `map_concurrent` (no graph)

```python
from __future__ import annotations

import asyncio

from penguiflow import map_concurrent


async def worker(x: int) -> int:
    await asyncio.sleep(0.01)
    return x * 2


async def main() -> None:
    results = await map_concurrent([1, 2, 3], worker, max_concurrency=8)
    print(results)


if __name__ == "__main__":
    asyncio.run(main())
```

### `join_k` (fan-out/fan-in per trace)

```python
from __future__ import annotations

import asyncio

from penguiflow import Headers, Message, Node, NodePolicy, create, join_k


async def fanout(msg: Message, ctx) -> None:
    for item in msg.payload:
        await ctx.emit(msg.model_copy(update={"payload": item}), to=worker_node)


async def work(msg: Message, _ctx) -> Message:
    return msg.model_copy(update={"payload": msg.payload * 2})


async def deliver(msg: Message, _ctx) -> list[int]:
    return msg.payload


fanout_node = Node(fanout, name="fanout", policy=NodePolicy(validate="none"))
worker_node = Node(work, name="work", policy=NodePolicy(validate="none"))
join_node = join_k("join", k=3)
final_node = Node(deliver, name="final", policy=NodePolicy(validate="none"))


async def main() -> None:
    flow = create(
        fanout_node.to(worker_node),
        worker_node.to(join_node),
        join_node.to(final_node),
        final_node.to(),
    )
    flow.run()

    message = Message(payload=[1, 2, 3], headers=Headers(tenant="demo"))
    await flow.emit(message, trace_id=message.trace_id)
    result = await flow.fetch(trace_id=message.trace_id)
    print(result)

    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **High queue depth**: add more consumers, reduce fan-out, or increase `queue_maxsize` carefully.
- **Join never emits**: confirm every trace emits exactly `k` branch messages and each branch preserves `trace_id`.
- **Unexpected cross-talk**: ensure `trace_id` and `Headers.tenant` are scoped per user/tenant.
