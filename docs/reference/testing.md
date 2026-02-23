# Testing (FlowTestKit)

## What it is / when to use it

PenguiFlow ships a small test harness (`penguiflow.testkit`) to make unit tests concise:

- run a single envelope trace through a flow (`run_one`)
- assert execution order (`assert_node_sequence`)
- simulate failures for retry/timeout tests (`simulate_error`)
- inspect recorded runtime events (`get_recorded_events`)
- assert envelope preservation for Message-in/Message-out nodes (`assert_preserves_message_envelope`)

Use FlowTestKit when you want to test node behavior and reliability semantics without re-implementing runtime plumbing.

## Non-goals / boundaries

- FlowTestKit is not an integration test runner for distributed systems.
- It does not mock external services; use stubs/fakes and dependency injection in nodes.
- `run_one` is envelope-only: it expects a `penguiflow.types.Message`.

## Contract surface

### `run_one(flow, message, registry=None, timeout_s=1.0)`

- starts the flow,
- emits the message,
- fetches the first Rookery result,
- stops the flow,
- records runtime events for the trace id.

### `assert_node_sequence(trace_id, expected_names)`

Asserts that the recorded `node_start` order (deduped) matches your expected node name sequence.

### `simulate_error(node_name, code, fail_times=1, ...)`

Returns an async callable suitable for wrapping in `Node(...)` that:

- raises an exception `fail_times` times, then
- returns the incoming message (or a configured result).

This is useful for retry-centric tests (`NodePolicy.max_retries`).

### `get_recorded_events(trace_id)`

Returns an immutable snapshot of recorded `FlowEvent` history for a trace.

### `assert_preserves_message_envelope(node, message=None, ctx=None)`

Executes a node callable (or `Node`) and asserts it returns a `Message` that preserves:

- `headers`
- `trace_id`

## Operational defaults (recommended)

- Prefer envelope-based tests (`Message`) for reliability features (cancellation/deadlines/streaming correlation).
- Keep test flows minimal: a couple of nodes plus a sink.
- Assert both positive and negative paths:
  - success,
  - retry then success,
  - retry exhausted (terminal failure).

## Failure modes & recovery

- **`TypeError: run_one expects a Message`**: wrap your payload in `Message(payload=..., headers=Headers(...))`.
- **No recorded events**: you didn’t run through `run_one` (or trace id mismatch).
- **Sequence mismatch**: a router/join changed topology; update your expectation or assert on a smaller invariant.

## Observability

Tests can assert directly on runtime events:

- ensure retries happened (`node_retry`),
- ensure timeouts emitted (`node_timeout`),
- ensure terminal failures surfaced (`node_failed`).

## Security / multi-tenancy notes

- Use synthetic test payloads; do not include real tokens or customer data in fixtures.
- Avoid persisting test traces in real `StateStore` backends; use in-memory stores in tests.

## Runnable example

This example simulates a node failing twice, then succeeding with retries enabled.

```python
from __future__ import annotations

import pytest

from penguiflow import Headers, Message, Node, NodePolicy, create
from penguiflow.testkit import assert_node_sequence, get_recorded_events, run_one, simulate_error


@pytest.mark.asyncio
async def test_retries_then_success() -> None:
    flaky = Node(simulate_error("flaky", "SIM_FAIL", fail_times=2), name="flaky", policy=NodePolicy(max_retries=2))
    flow = create(flaky.to())

    message = Message(payload={"ok": True}, headers=Headers(tenant="test"))
    result = await run_one(flow, message)
    assert result == {"ok": True}

    assert_node_sequence(message.trace_id, ["flaky"])
    events = get_recorded_events(message.trace_id)
    assert any(e.event_type == "node_retry" for e in events)
```

## Troubleshooting checklist

- If a retry test fails, confirm your `NodePolicy.max_retries` covers the number of simulated failures.
- If you need to assert envelope behavior for Message nodes, use `assert_preserves_message_envelope`.

