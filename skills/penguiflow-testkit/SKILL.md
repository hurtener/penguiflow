---
name: penguiflow-testkit
description: Write concise PenguiFlow unit tests with `penguiflow.testkit` — drive a single envelope trace through a flow with `run_one(flow, message, registry=None, timeout_s=1.0)`, assert execution order with `assert_node_sequence(trace_id, [...])`, simulate transient or terminal failures with `simulate_error(node_name, code, fail_times=N)` for retry/timeout tests, inspect captured events with `get_recorded_events(trace_id)`, and verify Message envelope preservation with `assert_preserves_message_envelope(node, ...)`. Use when a user says "write a flow test", "FlowTestKit", "test a node", "test retries", "simulate a failure", "assert node order", or asks how to unit-test PenguiFlow code with pytest-asyncio.
---

# PenguiFlow Testkit

## When to use
- Unit-testing a PenguiFlow flow with envelope messages.
- Asserting node execution order through routers and joins.
- Exercising retry/timeout semantics without mocking the runtime.
- Asserting envelope preservation (a node returned a `Message` with intact headers/trace_id).
- Inspecting `FlowEvent` history programmatically.

## When NOT to use
- Integration testing distributed systems (use real workers and a real broker).
- Mocking external services (use stubs and dependency injection inside node bodies).
- End-to-end tests through your app's HTTP layer (run the actual service).
- Planner-level behavior (use the planner's own test helpers; testkit is runtime-only).

## Hard boundaries
This skill covers the **runtime** test harness. It doesn't test planners, UI event streams, or A2A endpoints. For those, see the corresponding skill — they each ship their own test patterns.

## Workflow

### 1) Set up pytest-asyncio
```python
# pyproject.toml or pytest.ini
[tool.pytest.ini_options]
asyncio_mode = "auto"
```
PenguiFlow tests are `async def`. With `asyncio_mode = "auto"`, pytest treats them as async automatically. Otherwise decorate with `@pytest.mark.asyncio`.

### 2) Build a minimal envelope flow
Two nodes plus a sink, `policy=NodePolicy(validate="none")` for quick start. The point is testing node behavior, not flow construction.

### 3) Drive a single trace with `run_one`
```python
message = Message(payload="hello", headers=Headers(tenant="test"))
result = await run_one(flow, message)   # starts flow, emits, fetches, stops, records events
assert result == "HELLO"
```
`run_one(flow, message, registry=None, timeout_s=1.0)` is **envelope-only** — always pass a `Message`. `timeout_s=1.0` protects against hangs; bump for slow flows. Full lifecycle in `references/api-and-fixtures.md`.

### 4) Assert execution order
`assert_node_sequence(trace_id, ["parse", "route", "handle_a"])` compares the deduped `node_start` order. For parallel branches, assert smaller invariants per branch (inter-branch order is non-deterministic).

### 5) Test retries with `simulate_error`
```python
flaky = Node(
    simulate_error("flaky", "SIM_FAIL", fail_times=2),
    name="flaky",
    policy=NodePolicy(max_retries=2),
)
flow = create(flaky.to())

message = Message(payload={"ok": True}, headers=Headers(tenant="test"))
result = await run_one(flow, message)
assert result == {"ok": True}     # succeeded on the 3rd attempt

events = get_recorded_events(message.trace_id)
assert sum(1 for e in events if e.event_type == "node_retry") == 2
```

`simulate_error(node_name, code, fail_times=1, return_message=None)`:
- Returns an async callable suitable for wrapping in `Node(...)`.
- First N invocations raise an exception with the given `code`.
- N+1th invocation returns the incoming message (or `return_message` if supplied).

Pair with `NodePolicy(max_retries=N)` to assert retry behavior.

### 6) Test terminal failures
Use `simulate_error(..., fail_times=N)` with `NodePolicy(max_retries < N)` and `create(..., emit_errors_to_rookery=True)`. `run_one` then returns a `FlowError` you can assert against (`result.code == FlowErrorCode.NODE_EXCEPTION`). See `references/patterns.md`.

### 7) Assert envelope preservation
`await assert_preserves_message_envelope(my_node)` runs the node against a synthetic `Message` and asserts the returned message preserves `headers` and `trace_id`. Catches `return Message(headers=Headers(tenant="other"))` bugs that drop tenant scope.

### 8) Inspect events programmatically
`get_recorded_events(trace_id)` returns an immutable `tuple[FlowEvent, ...]`. Use for retry counts, timeout firing, specific lifecycle assertions, latency ranges.

## Troubleshooting (fast checks)
- **`TypeError: run_one expects a Message`** — wrap payload in `Message(payload=..., headers=Headers(...))`.
- **No recorded events for trace_id** — flow didn't actually run; check `run_one` returned a real result; verify trace_id match.
- **Sequence mismatch under routing** — assert smaller invariants instead of the full interleaved order.
- **Retry test fails** — `NodePolicy.max_retries < fail_times`; retries exhaust before success. Total attempts = `max_retries + 1`.
- **Timeout test flaky** — `simulate_error` doesn't simulate slow paths; for timeouts, use `await asyncio.sleep(...)` longer than `timeout_s`.
- **`run_one` hangs** — node returns `None` or doesn't reach Rookery; bump `timeout_s` to surface the actual hang.
- **Cross-test bleed** — `get_recorded_events` is global; ensure each test uses a fresh `trace_id`.

## Worked example
- `examples/testkit_demo/flow.py` — minimal retry + assertion pattern, runnable directly.
- Tests under `tests/test_testkit.py` are the canonical reference for `run_one`, `assert_node_sequence`, `simulate_error`, `get_recorded_events`.

## References (load only as needed)
- `references/api-and-fixtures.md` — every testkit helper, signatures, recording lifecycle, fixture patterns.
- `references/patterns.md` — testing routers, joins, fan-out, terminal failures, envelope preservation, planner-side stubbing.
- `references/coverage-and-ci.md` — pytest-asyncio setup, coverage targets (84.5%, `fail_under` in `pyproject.toml`), CI matrix, fixtures for `Headers`/`Message`/`ModelRegistry`.
