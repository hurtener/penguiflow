# Production deployment

## What it is / when to use it

This page is a **production hardening runbook** for PenguiFlow systems, focused on:

- sizing and limits (concurrency, queue depth, timeouts),
- safe defaults for multi-tenant agents (headers, trace scoping),
- reliability and recovery (retries, cancellation, deadline policy),
- observability (structured logs + derived metrics),
- tool execution safety (allowlists + HITL gates).

Use it when you are deploying a long-lived service or worker fleet and you want to be “on-call ready”.

## Non-goals / boundaries

- This does not prescribe a specific platform (Kubernetes vs ECS vs bare metal).
- PenguiFlow does not include an official metrics exporter; you derive metrics from `FlowEvent`.
- This does not replace your org’s security model; it documents the surfaces you must harden.

## Contract surface (things you must configure deliberately)

### Runtime limits

- **Edge backpressure**: `queue_maxsize` in `create(...)`.
- **Per-node policy**: `NodePolicy(timeout_s=..., max_retries=..., backoff_...)`.
- **Message deadlines**: `Message(deadline_s=...)` for end-to-end bounds (envelope flows).
- **Cancellation**: `await flow.cancel(trace_id)` is best-effort, trace-scoped.

Core references:

- **[Flows & nodes](../core/flows-and-nodes.md)**
- **[Errors, retries, timeouts](../core/errors-retries-timeouts.md)**
- **[Cancellation](../core/cancellation.md)**

### Multi-tenant boundaries (recommended)

Envelope flows should set:

- `Headers.tenant` (tenant boundary),
- `trace_id` (request/session correlation key).

Use trace-scoped correlation in multi-trace systems:

- `await flow.emit(message, trace_id=trace_id)`
- `await flow.fetch(trace_id=trace_id)`

See **[Messages & envelopes](../core/messages-and-envelopes.md)**.

### Durability / audit (optional, but common)

If you need audit history, distributed pause/resume, or “what happened?” debugging without relying on logs:

- configure a `StateStore` and store `StoredEvent` via runtime event persistence.

See **[State store](../tools/statestore.md)**.

### Tool execution (planner + ToolNode)

If you run `ReactPlanner` / ToolNode integrations, treat tools as an **untrusted boundary**:

- enforce per-tool timeouts and retries,
- set concurrency limits per tool source,
- allowlist tools and gate sensitive operations with HITL,
- keep tool auth in env/secret managers (no hardcoded tokens).

See **[Tooling](../planner/tooling.md)** and **[Tools configuration](../tools/configuration.md)**.

## Operational defaults (starting point)

These defaults are conservative and easy to reason about:

- **Bounded queues**: keep `queue_maxsize` > 0 (64 is a safe start).
- **Time-bound everything**:
  - set `NodePolicy.timeout_s` for every I/O node,
  - set message deadlines (`deadline_s`) for request flows.
- **Retries are budgeted**:
  - start with `max_retries=0..2` depending on idempotency,
  - keep backoff bounded (`max_backoff`) and avoid retry storms.
- **Envelopes for production**:
  - use `Message` + `Headers.tenant`,
  - use unique `trace_id` per request/session and `fetch(trace_id=...)`.
- **Structured logging + FlowEvents**:
  - call `configure_logging(structured=True)` early,
  - attach `log_flow_events(...)` middleware and derive metrics.

## Failure modes & recovery

### Symptom: system is “stuck” (no results)

Likely causes:

- no egress node routes to Rookery,
- `fetch()` is unscoped in a multi-trace system (you’re reading the wrong trace),
- cancellation/deadline dropped work before it could produce a final message.

Fix:

- ensure at least one egress node exists and emits/returns a final value,
- use `fetch(trace_id=...)` for concurrent traces,
- monitor `deadline_skip` and cancellation events in logs/metrics.

### Symptom: high latency / timeouts

Likely causes:

- downstream dependency latency spike,
- queue saturation (backpressure), or
- retries amplifying load.

Fix:

- tighten `timeout_s` and reduce retries,
- reduce concurrency at the busiest tool/node boundary,
- scale worker replicas only after you’ve bounded retries/timeouts.

### Symptom: duplicate side effects (double writes)

Cause: retries re-executed a side-effecting node.

Fix:

- make side effects idempotent (idempotency key based on `trace_id` + step),
- or separate “compute” and “commit” nodes and only commit once.

### Symptom: cross-tenant leakage

Cause: shared traces/headers or unsafe logging/artifacts.

Fix:

- enforce tenant boundary at ingress and in tool config,
- redact logs and avoid persisting raw tool payloads.

## Observability (what to log + what to measure)

### Minimum production observability

- **Structured logs** (JSON) for `penguiflow` loggers.
- Runtime `FlowEvent` capture via middleware.
- Derived metrics for:
  - `node_success` / `node_error` / `node_timeout` counters,
  - node latency histogram (ms),
  - queue depth gauges (incoming/outgoing/total),
  - cancellation and deadline skip counters.

See:

- **[Logging](../observability/logging.md)**
- **[Telemetry patterns](../observability/telemetry-patterns.md)**
- **[Metrics & alerts](../observability/metrics-and-alerts.md)**

## Security / multi-tenancy notes

- Do not tag metrics with `trace_id` (cardinality explosion). Use logs/events for trace-level debugging.
- Treat tool outputs as untrusted; never log raw payloads by default.
- Keep secrets out of `Message.meta` and out of persisted events unless you intentionally secure that store.

## Runnable example: “golden” service skeleton

This skeleton shows the lifecycle hooks you want in a long-lived process:

```python
from __future__ import annotations

import asyncio
import logging

from penguiflow import (
    Headers,
    Message,
    Node,
    NodePolicy,
    configure_logging,
    create,
    log_flow_events,
)


async def handler(msg: Message, _ctx) -> Message:
    # Replace with your real work.
    return msg


async def main() -> None:
    configure_logging(structured=True)
    logger = logging.getLogger("penguiflow.flow")

    node = Node(handler, name="handler", policy=NodePolicy(timeout_s=10, max_retries=1))
    flow = create(node.to(), middlewares=[log_flow_events(logger)])
    flow.run()

    message = Message(payload={"ping": True}, headers=Headers(tenant="demo"))
    await flow.emit(message, trace_id=message.trace_id)
    _ = await flow.fetch(trace_id=message.trace_id)

    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **No results**: confirm an egress node exists and routes to Rookery; use `fetch(trace_id=...)` with envelopes.
- **Timeouts spike**: check downstream latency and queue depths; reduce retries before scaling out.
- **Retry storms**: lower `max_retries`, cap backoff, and enforce idempotency on side effects.
- **Leaky logs**: switch to structured logs and redact; avoid logging payloads by default.
