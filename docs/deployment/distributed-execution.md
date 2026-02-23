# Distributed execution & remote calls

## What it is / when to use it

PenguiFlow can run as a single-process runtime, but it also supports “distributed hooks” when you need:

- multiple worker processes (or machines),
- durable audit history of runtime events,
- remote tool/agent execution with streaming and cancellation,
- agent-to-agent calls (A2A-style) via the `penguiflow_a2a` package.

Use this page when you are moving from “local dev” to “multi-worker production”.

## Non-goals / boundaries

- PenguiFlow does not ship a complete distributed scheduler. You provide queueing, worker management, and service discovery.
- There is no single “official” backend; the library exposes protocols (StateStore, MessageBus, RemoteTransport).
- Distributed systems require idempotency and strict limits; the library helps but cannot guarantee correctness if you violate those contracts.

## Contract surface

### 1) `StateStore` (durability + audit)

At minimum, a `StateStore` must implement:

- `save_event(event: StoredEvent) -> None` (idempotent)
- `load_history(trace_id: str) -> Sequence[StoredEvent]`
- `save_remote_binding(binding: RemoteBinding) -> None`

Optional capabilities (duck-typed) enable:

- planner pause/resume state
- memory state persistence
- task/session updates and steering inbox
- artifact integration

See **[State store](../tools/statestore.md)**.

### 2) `MessageBus` (distributed edges)

A `MessageBus` is a minimal interface:

- `publish(envelope: BusEnvelope) -> None`

The runtime can publish edge traffic to a message bus so downstream workers can process edges out-of-process.

See also: **[Worker integration](worker-integration.md)**.

### 3) `RemoteTransport` + `RemoteNode` (remote execution)

Remote calls are modeled as:

- `RemoteTransport.send(request) -> RemoteCallResult` (unary)
- `RemoteTransport.stream(request) -> AsyncIterator[RemoteStreamEvent]` (streaming)
- `RemoteTransport.cancel(agent_url, task_id)` (cancellation)

`RemoteNode(...)` builds a `Node` that proxies work to a remote agent/service through a transport.

Key operational point:

- remote cancellation requires a durable `task_id` and a transport implementation that supports cancellation.

### 4) A2A bindings (`penguiflow_a2a`)

PenguiFlow includes A2A-inspired server/client bindings in the `penguiflow_a2a` package:

- server: `A2AService` + `create_a2a_http_app(...)`
- client transport: `A2AHttpTransport` (implements `RemoteTransport`)

Install extras:

- server: `pip install "penguiflow[a2a-server]"`
- client: `pip install "penguiflow[a2a-client]"`
- gRPC bindings: `pip install "penguiflow[a2a-grpc]"`

## Operational defaults (recommended)

- Always use envelope messages (`Message`) and set:
  - `Headers.tenant` (tenant boundary),
  - `trace_id` (correlation).
- Time-bound every remote boundary:
  - node timeouts for the remote node,
  - request-level deadlines (`Message.deadline_s`) when appropriate.
- Persist bindings:
  - enable `record_binding=True` for remote nodes,
  - ensure your `StateStore.save_remote_binding` is implemented and durable.
- Assume at-least-once delivery in the surrounding platform:
  - make remote calls idempotent where possible,
  - avoid side effects on retry without idempotency keys.

## Failure modes & recovery

- **Remote tasks leak** (can’t cancel): you didn’t persist `task_id` bindings or your transport doesn’t support cancel.
  - Fix: implement `save_remote_binding` and use a transport with cancellation; enforce job deadlines so leaks are bounded.
- **Cross-tenant leakage**: remote calls share auth tokens or re-use trace ids across tenants.
  - Fix: isolate by `Headers.tenant` + per-tenant credentials; never reuse trace ids across tenants.
- **Retry storms**: transient remote failures trigger retries across multiple workers.
  - Fix: cap retries, add backoff, and add circuit-breaker behavior at the boundary.

## Observability

Distributed execution needs two layers:

1. **Runtime events** (`FlowEvent`) per worker:
   - queue depth, node lifecycle, retries/timeouts, cancellations, deadline skips.
2. **Remote events** from remote nodes/transports:
   - remote call latency,
   - remote status and task ids,
   - cancellation success/failure.

Recommended dashboards/alerts are covered in **[Metrics & alerts](../observability/metrics-and-alerts.md)**.

## Security / multi-tenancy notes

- Treat remote agent URLs as sensitive and validate/allowlist them.
- Do not log raw remote payloads by default; log ids + summaries + structured metadata.
- If you accept callback URLs (webhooks), validate them (SSRF protection).

## Runnable examples

```bash
uv run python examples/a2a_grpc_server/flow.py
uv run python examples/trace_cancel/flow.py
```

For remote-node patterns (transport-dependent), search for `RemoteNode(` usage in examples and tests.

## Troubleshooting checklist

- If a system “works locally” but fails distributed:
  - confirm `StateStore` durability and idempotency,
  - confirm queue/worker semantics (visibility timeout, retries),
  - confirm per-trace correlation (`fetch(trace_id=...)`) and tenant headers.

