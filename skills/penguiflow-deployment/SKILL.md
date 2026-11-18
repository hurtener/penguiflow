---
name: penguiflow-deployment
description: Take a PenguiFlow agent from local dev to a production worker fleet — pick a deployment shape (embedded service, queue-backed worker pool, distributed), enforce envelope discipline (`Message` + `Headers.tenant` + `trace_id`), set runtime limits (`queue_maxsize`, `NodePolicy.timeout_s`/`max_retries`, `Message.deadline_s`), implement worker lifecycle (`flow.run(...)` → `await flow.stop()`), wire durability via `StateStore` capabilities, configure distributed hooks (`MessageBus`, `RemoteTransport`+`RemoteNode`, A2A bindings), and harden tool execution. Use when a user says "deploy to production", "worker integration", "multi-worker", "queue-backed", "distributed execution", "RemoteNode", "MessageBus", or asks how to size queues/timeouts/retries.
---

# PenguiFlow Deployment

## When to use
- Moving from local dev / playground to a long-lived production service.
- Building a queue-backed job worker.
- Sizing concurrency, queues, timeouts, retries.
- Scaling to multiple workers / machines.
- Wiring distributed primitives (`StateStore`, `MessageBus`, `RemoteTransport`).

## When NOT to use
- Local iteration → use [[penguiflow-quickstart]] (`penguiflow dev`).
- Persistence semantics in depth → use [[penguiflow-statestore]].
- A2A wire contract → use [[penguiflow-a2a-integration]] (this skill points at it for distributed agent calls).
- Observability dashboards/alerts → use [[penguiflow-observability]].
- Test harness → use [[penguiflow-testkit]].

## Hard boundaries
This skill is the **production runbook**: shapes, sizing, lifecycle, distributed contracts. Persistence implementation lives in statestore. Observability dashboards live in observability. A2A bindings live in a2a-integration. Don't duplicate.

## Workflow

### 1) Pick a deployment shape
| Shape | When | Trade-offs |
|---|---|---|
| Embedded in a service (FastAPI, gRPC) | Synchronous user-facing requests | Simplest; tied to service scaling |
| Queue-backed worker pool | Async jobs, batch, fan-out | Scales horizontally; needs a queue |
| Distributed (`MessageBus` + multi-worker) | High throughput across machines | Most complex; needs durable infra |

Start with embedded or queue-backed. Distributed only when you've hit horizontal scaling limits on a single process.

### 2) Adopt envelopes (`Message`)
Production = envelopes:
```python
msg = Message(payload=..., headers=Headers(tenant=...), deadline_s=...)
await flow.emit(msg, trace_id=msg.trace_id)
result = await flow.fetch(trace_id=msg.trace_id)
```
Required for per-trace cancel, deadlines, multi-tenant isolation, deterministic correlation in multi-trace systems. Payload-only is a dev convenience. See [[penguiflow-core-flows]] `references/messages-and-envelopes.md`.

### 3) Set runtime limits deliberately
| Knob | Where | Default | Production start |
|---|---|---|---|
| `queue_maxsize` | `create(...)` | 64 | 64-256 depending on workload |
| `NodePolicy.timeout_s` | every I/O node | unset | always set; tune to p99 + headroom |
| `NodePolicy.max_retries` | per node | 0 | 1-3 for idempotent network ops; 0 for non-idempotent |
| `NodePolicy.backoff_base`/`max_backoff` | per node | 0.5 / 30 | tune to dependency's rate-limits |
| `Message.deadline_s` | envelope | None | set on user-initiated work |

Never disable backpressure (`queue_maxsize <= 0`) unless you have your own flow control. Never run a network node without `timeout_s`.

### 4) Manage flow lifecycle in your worker
```python
flow = create(...)
flow.run(registry=registry)        # start workers
try:
    async for job in queue:
        message = Message(payload=job.payload, headers=Headers(tenant=job.tenant), deadline_s=job.deadline)
        result = await flow.fetch(trace_id=message.trace_id)   # or with run/emit pattern
        await queue.mark_complete(job.id, result=result)
finally:
    await flow.stop()              # always, on shutdown
```
Patterns: **stateless-per-job** (new flow per job, best isolation) vs **shared-flow** (one flow, many traces, lower overhead). See `references/worker-patterns.md`.

### 5) Wire durability (when you need it)
Attach a `StateStore` to `create(...)`. Every `FlowEvent` calls `save_event` (best-effort). Multi-worker pause/resume, memory persistence, A2A binding continuity — see [[penguiflow-statestore]] for required and optional capabilities.

### 6) Wire distributed primitives (when you need them)
- **`StateStore`** — durable audit + binding storage. Mandatory for distributed pause/resume.
- **`MessageBus`** — `publish(envelope: BusEnvelope)` lets you route edge traffic to out-of-process workers. Optional; needed when scaling beyond one process per graph.
- **`RemoteTransport` + `RemoteNode`** — proxy a node to a remote service. Implements `send`/`stream`/`cancel`. Pair with `record_binding=True` and `StateStore.save_remote_binding` to enable cancellation across restarts.
- **A2A bindings** — for agent-to-agent calls, use the dedicated skill. Install extras: `penguiflow-lite[a2a-server]`, `penguiflow-lite[a2a-client]`, `penguiflow-lite[a2a-grpc]`.

See `references/distributed-primitives.md`.

### 7) Harden tool execution (planner + ToolNode)
Tools are an untrusted boundary:
- Set `max_concurrency` per `ExternalToolConfig`.
- `tool_filter` allowlists.
- Gate write/external tools behind HITL ([[penguiflow-hitl-pause-resume]]) or guardrails ([[penguiflow-guardrails]]).
- Secrets in env/secret managers, never in `llm_context`.

See [[penguiflow-mcp-integration]].

### 8) Observe + alert
Structured logging (`configure_logging(structured=True)`), `log_flow_events` middleware everywhere, metrics derived from `FlowEvent` with bounded cardinality. Alert on queue saturation, error/timeout rates, retry storms, `node_failed` accumulation. See [[penguiflow-observability]].

### 9) Graceful shutdown
On `SIGTERM`: stop accepting new jobs → drain in-flight with a deadline → `flow.cancel(trace_id)` for over-deadline traces → `await flow.stop()` → exit. Most platforms give 30-60s before SIGKILL; tune the drain deadline to fit within that window.

## Troubleshooting (fast checks)
- **Memory grows unbounded** — `queue_maxsize <= 0` somewhere; bound it.
- **Random latency spikes** — no `timeout_s` on a network node; set it.
- **Retry storms cascade across workers** — at-least-once delivery + non-idempotent nodes; add idempotency keys (use `trace_id`) or `max_retries=0`.
- **Worker stalls on shutdown** — `flow.stop()` not called or in-flight tasks not cancelled; wire the graceful shutdown handler.
- **Cross-tenant data in audit** — `Headers.tenant` missing; enforce at ingress.
- **Remote tasks leak (can't cancel)** — `record_binding=False` or `StateStore.save_remote_binding` missing; enable durable bindings.
- **Distributed pause/resume fails** — `StateStore` missing `save_planner_state`/`load_planner_state`; implement them.
- **One bad job DoSes the worker** — no `max_concurrent_tasks` / no resource limits; cap concurrency at every layer.
- **Logs flood from `node_start`** — sample or filter; high-throughput nodes generate one event per call.

## Worked examples
- Templates demonstrate worker scaffolding: `penguiflow new <template> --with-a2a --with-background-tasks --dry-run` shows the lifecycle wiring.
- `examples/reliability_middleware/flow.py` and `examples/mlflow_metrics/flow.py` show production-shape observability wiring.

## References (load only as needed)
- `references/worker-patterns.md` — stateless-per-job vs shared-flow, queue contracts (at-least-once, retry, dead-letter), per-job correlation, graceful shutdown.
- `references/sizing-and-limits.md` — `queue_maxsize`, `timeout_s`, `max_retries`, `deadline_s` sizing recipes by workload type; sample SLOs.
- `references/distributed-primitives.md` — `MessageBus`, `RemoteTransport`/`RemoteNode`, A2A handoff, recovery semantics, at-least-once + idempotency rules.
