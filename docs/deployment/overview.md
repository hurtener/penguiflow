# Deployment

## What it is / when to use it

This section explains how to run PenguiFlow as a **long-lived production system**:

- embedded inside a Python service (FastAPI, worker, CLI runner),
- as a **job worker** behind a queue (stateless or long-lived flows),
- optionally distributed (StateStore + MessageBus + RemoteTransport).

Use this page to decide which deployment shape you want, and which operational “contracts” you must implement.

## Non-goals / boundaries

- These docs do not replace your org’s platform standards (Kubernetes, service mesh, secrets, CI/CD).
- PenguiFlow does not ship a full “platform” (no built-in job queue, metrics exporter, or authz layer).
- “Distributed execution” is opt-in; you can run a single-process deployment safely with the core runtime.

## Contract surface (choices you must make)

### 1) Runtime style: payload-only vs envelopes

- **Payload-only** is fastest to start and works well for single-tenant pipelines.
- **Envelopes** (`Message(payload=..., headers=Headers(...), trace_id=...)`) are the production-friendly default:
  - per-trace cancellation and deadlines,
  - deterministic correlation (`fetch(trace_id=...)`),
  - streaming chunks with inherited metadata,
  - multi-tenant isolation via `Headers.tenant`.

See **[Messages & envelopes](../core/messages-and-envelopes.md)**.

### 2) Reliability knobs (per node)

Every node has a `NodePolicy` that defines:

- timeouts (`timeout_s`),
- retries (`max_retries`) and exponential backoff.

See **[Errors, retries, timeouts](../core/errors-retries-timeouts.md)**.

### 3) Backpressure + concurrency

Edges are bounded queues (`queue_maxsize`) so the system applies backpressure instead of unbounded buffering.

See **[Concurrency](../core/concurrency.md)**.

### 4) Durability and audit (optional)

In production you often need one (or both):

- **structured events** (`FlowEvent`) for debugging and metrics, and/or
- a **StateStore** to persist `StoredEvent` (audit history, distributed pause/resume, replay-friendly event capture).

See **[State store](../tools/statestore.md)** and **[Telemetry patterns](../observability/telemetry-patterns.md)**.

### 5) Tool integrations (if using ToolNode / planner)

If you use `ReactPlanner` and ToolNode integrations, deployment must include:

- secret injection (env vars / secret manager),
- tool allowlists / visibility policies,
- safe concurrency limits per tool source.

See **[Tools configuration](../tools/configuration.md)** and **[Production deployment](production-deployment.md)**.

## Operational defaults (recommended)

- Start with **one process** and a bounded queue (`queue_maxsize` > 0).
- Prefer **envelopes** for any multi-tenant / request-correlated system.
- Treat tool execution as untrusted:
  - allowlist tools and gate sensitive actions with HITL,
  - enforce timeouts on every external boundary.
- Attach structured logging early:
  - `configure_logging(structured=True)`,
  - `middlewares=[log_flow_events(...)]` for consistent runtime events.

## Failure modes & recovery

- **Requests get “mixed up”**: you are using unscoped `fetch()` with multiple concurrent traces. Fix: use unique `trace_id` per request and `fetch(trace_id=...)`.
- **Workers “hang”**: no egress message reaches Rookery. Fix: ensure at least one egress node exists and emits/returns a final value.
- **Memory growth**: unbounded edges (`queue_maxsize <= 0`) or large payloads in events. Fix: keep queues bounded; use artifacts/resources for large blobs.
- **Retry storms**: retries amplify load on downstream dependencies. Fix: reduce retries, add timeouts, and implement idempotency on side-effecting nodes.

## Observability

PenguiFlow emits `FlowEvent` around node execution and control-plane behavior (timeouts, retries, cancellation, deadline skips).

Operationally:

- log FlowEvents (middleware) and ensure logs include `trace_id`,
- extract metrics from FlowEvents (counters + histograms + queue-depth gauges),
- persist events via `StateStore` if you need audit/replay.

See **[Logging](../observability/logging.md)** and **[Metrics & alerts](../observability/metrics-and-alerts.md)**.

## Security / multi-tenancy notes

- Always set `Headers.tenant` and keep tenant boundaries consistent across a trace.
- Never store secrets in message payloads or `meta` if you persist events/logs.
- Treat `trace_id` as an authorization surface (don’t allow cross-tenant fetch/cancel).

## Runnable examples

```bash
uv run python examples/quickstart/flow.py
uv run python examples/roadmap_status_updates/flow.py
```

## Troubleshooting checklist

- Need job workers: start with **[Worker integration](worker-integration.md)**.
- Need production hardening (limits, rollout, multi-tenant defaults): use **[Production deployment](production-deployment.md)**.
- Need to understand runtime behavior: see **[Core runtime](../core/flows-and-nodes.md)**.

## Next steps

- **[Worker integration](worker-integration.md)**
- **[Production deployment](production-deployment.md)**
