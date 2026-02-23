# Errors, retries, and timeouts

## What it is / when to use it

PenguiFlow emphasizes predictable runtime behavior:

- timeouts and retries are configured via `NodePolicy`,
- errors are wrapped with trace and node metadata (`FlowError`),
- failures are emitted as structured `FlowEvent` signals (and optionally persisted).

Use these knobs when you need “production semantics”:

- bound latency with timeouts,
- retry transient failures,
- surface terminal failures to a caller or UI.

## Non-goals / boundaries

- Retries cannot make non-idempotent side effects safe. Design nodes to tolerate retries (or gate commits).
- The runtime does not automatically classify “transient vs permanent” for you beyond timeouts and basic exception semantics.

## Contract surface

## NodePolicy knobs

`NodePolicy` controls validation and reliability behaviors:

- `timeout_s`: hard timeout for a node invocation
- `max_retries`: retry count after failures
- `backoff_base`, `backoff_mult`, `max_backoff`: exponential backoff parameters
- `validate`: `"both" | "in" | "out" | "none"`

Example:

```python
from penguiflow import NodePolicy

policy = NodePolicy(
    validate="both",
    timeout_s=10.0,
    max_retries=3,
    backoff_base=0.5,
    backoff_mult=2.0,
    max_backoff=10.0,
)
```

Retry semantics:

- attempts start at 0
- on failure, the runtime retries while `attempt < max_retries`
- total attempts = `max_retries + 1`

## Operational defaults

- Prefer small timeouts on network-bound nodes.
- Keep retries bounded (`max_retries` 1–3) and backoff reasonable.
- Emit errors to the sink only when you want caller-visible failures:
  - `create(..., emit_errors_to_rookery=True)`

## Recommended defaults

- Use short timeouts on network-bound nodes.
- Make tool calls idempotent where possible.
- Prefer retrying on transient errors only (timeouts, 429/5xx).

## What happens on failure

When a node raises:

1. PenguiFlow emits a `node_error` (or `node_timeout`) event with exception metadata.
2. If `max_retries > 0`, it emits `node_retry`, sleeps for an exponential backoff delay, and re-invokes the node.
3. Once retries are exhausted, PenguiFlow creates a `FlowError` with:
   - `trace_id`, `node_name`, `node_id`
   - an error code (`FlowErrorCode.NODE_EXCEPTION` / `FlowErrorCode.NODE_TIMEOUT`)
   - attempt/latency metadata

By default, `FlowError` is logged/observable via events. If you want errors to be treated as “normal outputs” you can enable:

- `create(..., emit_errors_to_rookery=True)`

This routes `FlowError` to the Rookery sink so `fetch()` can return it.

## Retry-safe node design

Retries are only safe if your node is idempotent (or can tolerate duplicates).

Common strategies:

- Use request ids / trace ids as idempotency keys when calling external services.
- For side-effecting operations, separate “plan” and “commit” steps and gate the commit step (HITL or explicit checks).
- Keep timeouts low and bound I/O so cancellation and deadlines can interrupt work.

## Failure modes & recovery

### Retries cause duplicate side effects

**Fix**

- add idempotency keys (use `trace_id` as the request id where appropriate)
- split “plan” and “commit” nodes and gate commit behind HITL/policy

### Timeouts fire but work continues

Timeouts cancel the node invocation task, but external systems may continue work if you triggered a non-cancellable request.

**Fix**

- use provider-specific cancel APIs where available
- keep operations small and check cancellation/deadlines before expensive work

## Observability

Watch these `FlowEvent` types:

- `node_error`, `node_timeout` (raw failures)
- `node_retry` (retry bursts)
- `node_failed` (terminal failure; includes `flow_error` payload)

Track:

- retry rate by node and exception type,
- timeout rate by node,
- latency distributions (`latency_ms`) for `node_success` and failures.

## Security / multi-tenancy notes

- Exception reprs can leak sensitive details (URLs, headers). If you persist events, redact at the boundary.
- Only enable `emit_errors_to_rookery=True` if callers can safely see `FlowError` payloads.

## Runnable example: retry then succeed

```python
from __future__ import annotations

import asyncio

from penguiflow import Node, NodePolicy, create


class Flaky:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, msg, _ctx) -> int:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("transient failure")
        return 42


async def main() -> None:
    flaky = Flaky()
    node = Node(flaky, name="flaky", policy=NodePolicy(validate="none", max_retries=3, backoff_base=0.01))

    flow = create(node.to())
    flow.run()

    await flow.emit({"x": 1})
    result = await flow.fetch()
    print(result)

    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **Retries never happen**: confirm `max_retries > 0` and your node actually raises exceptions.
- **Everything times out**: increase `timeout_s` or reduce work per node; check for blocking I/O.
- **Failures don’t reach callers**: enable `emit_errors_to_rookery=True` (and ensure it’s safe for your app).

## See also

- `FlowError` and `FlowErrorCode` in the public API
- Observability hooks via `FlowEvent` callbacks
