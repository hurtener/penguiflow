# Sizing and Limits

Concrete recipes for runtime knobs by workload type. All numbers are starting points — measure and tune.

## Knob inventory

| Knob | Scope | Purpose |
|---|---|---|
| `queue_maxsize` | `create(...)` | Bounded edge queues; backpressure. |
| `NodePolicy.timeout_s` | per-node | Per-invocation timeout. |
| `NodePolicy.max_retries` | per-node | Retry budget. |
| `NodePolicy.backoff_base/_mult/max_backoff` | per-node | Exponential backoff. |
| `Message.deadline_s` | per-trace | End-to-end deadline. |
| `ExternalToolConfig.max_concurrency` | per-ToolNode | Bound parallel external calls. |
| `ExternalToolConfig.timeout_s` | per-ToolNode | Bound per-tool latency. |
| `BackgroundTasksConfig.max_concurrent_tasks` | session (overrides `SessionLimits` when set) | Bound concurrent tasks in a session. |
| Queue visibility timeout | external queue | Time before message redelivery. |
| Worker concurrency (asyncio task count) | platform | Number of concurrent jobs per worker. |
| Replica count | platform | Horizontal scale. |

## Recipe: chat agent (user-facing, low traffic)

| Knob | Value |
|---|---|
| `queue_maxsize` | 64 |
| `NodePolicy.timeout_s` (LLM calls) | 30s |
| `NodePolicy.timeout_s` (tool calls) | 15s |
| `NodePolicy.max_retries` (LLM) | 1 |
| `NodePolicy.max_retries` (idempotent tools) | 2 |
| `Message.deadline_s` | 60s |
| `ExternalToolConfig.max_concurrency` | 3-5 |
| Worker concurrency | 10-20 |
| Replicas | Start 2 (HA), scale on queue depth |

SLO target: p95 end-to-end < 30s.

## Recipe: batch ETL worker

| Knob | Value |
|---|---|
| `queue_maxsize` | 128-256 |
| `NodePolicy.timeout_s` | 120s |
| `NodePolicy.max_retries` | 3 (idempotent extracts) |
| `Message.deadline_s` | 600s (10 min) per job |
| Worker concurrency | 4-8 |
| Replicas | scale to queue depth |

Longer timeouts; lower concurrency per worker (extracts are usually I/O- and memory-heavy).

## Recipe: real-time stream / SSE

| Knob | Value |
|---|---|
| `queue_maxsize` (chunk edge) | 256-512 |
| `NodePolicy.timeout_s` (compose) | 60s |
| `Message.deadline_s` | optional, generous |
| Worker concurrency | 50-200 (many idle WebSocket holders) |

High concurrency is OK because most "work" is sitting on `await`s for client transport. Tune `queue_maxsize` of the chunk edge specifically — chunks burst.

## Recipe: agent network specialist (A2A serving)

| Knob | Value |
|---|---|
| `queue_maxsize` | 64 |
| `NodePolicy.timeout_s` | match SLA |
| `Message.deadline_s` | match SLA |
| `A2AConfig.payload_mode` | `AUTO` |
| Worker concurrency | 20-50 |

For specialists serving many concurrent A2A callers, lean on `Headers.tenant` and `trace_id` for isolation; `session_id` for memory.

## Recipe: HITL workflow (low traffic, long sessions)

| Knob | Value |
|---|---|
| `queue_maxsize` | 32 |
| `NodePolicy.timeout_s` | 60s (per node) |
| `Message.deadline_s` | None (or hours) — pause spans are unbounded |
| `BackgroundTasksConfig.task_timeout_s` | 3600s |
| Pause state TTL (StateStore) | 24h - 7d depending on UX |

Long sessions need durable state. Don't set deadlines that race the user's attention span.

## Sizing `queue_maxsize`

Heuristics:
- Default 64 fits most.
- Bump to 128-256 for fan-out where producers can outpace consumers briefly.
- Bump to 256-512 for streaming chunk edges where the consumer is network-bound.
- Never go unbounded (`<= 0`) without your own flow control.

Signals to bump:
- `queue_depth_total` trends toward `queue_maxsize` regularly.
- `emit()` `awaits` are visible in latency p95.

Signals to lower:
- Memory pressure under steady load.
- Producer was slowed deliberately and you no longer need the buffer.

## Sizing `NodePolicy.timeout_s`

Formula: `timeout_s = p99(latency) × 1.5` with a hard ceiling per workload type.

| Workload | Hard ceiling |
|---|---|
| Sync user-facing | 30s |
| Async batch | 300s |
| Long-running ETL | 1800s |

Don't trust upstream "this is fast" without measurement. Always measure.

## Sizing `max_retries`

| Operation | `max_retries` |
|---|---|
| Idempotent network call (HTTP GET, S3 GET) | 2-3 |
| Idempotent with idempotency key (HTTP POST + key) | 2-3 |
| Non-idempotent (DB write without key) | 0 |
| CPU-bound | 0 (failures are deterministic) |
| Validation | 0 (won't get better on retry) |
| Transient errors only (5xx, 429) | 2-3 |

Retries amplify failures during incidents. With 3 workers × 3 retries, a 1% transient error rate becomes a 9× load amplification at the dependency.

## Sizing backoff

```
backoff_base * backoff_mult ** attempt, capped at max_backoff
```

Default `base=0.5, mult=2.0, max=30.0` gives: 0.5s, 1s, 2s, 4s, 8s, ... 30s.

Tune for the dependency:
- Rate-limited APIs: respect `Retry-After` if available; else aggressive backoff (`base=2.0, mult=2.0`).
- Slow internal services: faster backoff (`base=0.1, mult=1.5`).

## Sizing tool concurrency

Per `ExternalToolConfig.max_concurrency`:
- External SaaS: 3-5 (most have low concurrent-call limits).
- Internal services: 10-20.
- Search/embeddings: limit set by the provider's rate plan.

Cross-tool concurrency on a worker:
```
worker_concurrency = sum(ExternalToolConfig.max_concurrency) ≤ asyncio task budget
```

Don't oversubscribe — one tool can starve another.

## Worker count

```
replicas = ceil(peak_qps × p95_latency / worker_concurrency)
```

Add 30% headroom. Set autoscaling targets on queue depth or response latency, not CPU (asyncio workers can be near-100% CPU on healthy load).

## Deadline propagation

Deadlines should propagate downstream:
- Set `Message.deadline_s` at ingress.
- Each node respects its own `timeout_s` AND the message's `deadline_s` (when present).
- For downstream messages, `model_copy(update=...)` preserves `deadline_s`. Don't construct fresh `Message(...)` mid-graph if you want deadlines to flow.

If a deadline is set, the runtime emits `deadline_skip` for messages that arrive at a node after expiration; for `Message` envelopes, a `FinalAnswer(text="Deadline exceeded")` reaches Rookery so callers see a result.

## Sample SLOs

| Workload | SLO |
|---|---|
| Chat agent | 99% < 30s end-to-end |
| Specialist agent (A2A) | 99.5% < 5s; p99 < 30s |
| Batch ETL | 99% complete within job deadline |
| Streaming | 99% TTFC < 2s |

Map each SLI to dashboard panels and alerts. See [[penguiflow-observability]] for the metric inventory.
