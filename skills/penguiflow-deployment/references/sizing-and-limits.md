# Sizing and Limits

Concrete recipes for runtime knobs by workload type. All numbers are starting points — measure and tune.

## Knob inventory

| Knob | Scope | Purpose |
|---|---|---|
| `max_iters` | `ReactPlanner` | Max planning steps per run. |
| `deadline_s` | `ReactPlanner` | Wall-clock budget per run (finishes with `budget_exhausted`). |
| `hop_budget` | `ReactPlanner` | Max tool hops per run. |
| `token_budget` | `ReactPlanner` | Prompt token budget (trajectory compression kicks in). |
| `llm_timeout_s` / `llm_max_retries` | `ReactPlanner` | Per-LLM-call timeout and retries. |
| `repair_attempts` / `max_consecutive_arg_failures` | `ReactPlanner` | Bound self-repair loops. |
| `absolute_max_parallel` | `ReactPlanner` | Cap on parallel tool calls in one step. |
| `ExternalToolConfig.timeout_s` / `max_concurrency` / `retry_policy` | per ToolNode | Bound remote tool latency, parallelism and retries. |
| `asyncio.timeout(...)` inside local tools | per tool | Local tools have no runtime-enforced timeout. |
| `BackgroundTasksConfig(max_concurrent_tasks, task_timeout_s, ...)` | planner | Bound background tasks. |
| `SessionLimits(max_concurrent_tasks, max_task_runtime_s, update_queue_size, ...)` | sessions | Per-session concurrency, runtime and subscriber queues. |
| `A2AAgentToolset(default_timeout_s=...)` / tool `timeout_s` | manager side | Bound remote agent calls. |
| Queue visibility timeout | external queue | Time before redelivery. |
| Worker concurrency | platform | Concurrent runs per worker process. |
| Replica count | platform | Horizontal scale. |

## Recipe: chat agent (user-facing)

| Knob | Value |
|---|---|
| `max_iters` | 6-8 |
| `deadline_s` | 60s |
| `llm_timeout_s` / `llm_max_retries` | 60s / 2 |
| `stream_final_response` | True |
| ToolNode `timeout_s` | 15s |
| `ExternalToolConfig.max_concurrency` | 3-5 |
| `SessionLimits.max_concurrent_tasks` | 2-4 |
| Worker concurrency | 20-50 runs (mostly awaiting the LLM) |
| Replicas | start 2 (HA), scale on latency / in-flight runs |

SLO target: p95 end-to-end < 30s; TTFC < 2s.

## Recipe: batch / back-office worker

| Knob | Value |
|---|---|
| `max_iters` | 10-15 |
| `deadline_s` | 600s per job |
| `llm_timeout_s` / `llm_max_retries` | 120s / 3 |
| ToolNode `timeout_s` | 60-120s |
| `hop_budget` | set to cap cost |
| Worker concurrency | 4-8 |
| Replicas | scale on queue depth |

Longer budgets; lower concurrency per worker (tools are usually I/O- and memory-heavy).

## Recipe: streaming service (SSE / WebSocket)

| Knob | Value |
|---|---|
| `stream_final_response` | True |
| `SessionLimits.update_queue_size` | 500-2000 for chunk-heavy UIs |
| `deadline_s` | generous (e.g. 120s) |
| Worker concurrency | 50-200 (many idle connections) |

High concurrency is fine — most of the time is spent awaiting the LLM or the client. Coalesce chunks in the bridge if the network is the bottleneck ([[penguiflow-streaming]]).

## Recipe: A2A specialist

| Knob | Value |
|---|---|
| `deadline_s` | match the caller's SLA minus network headroom |
| `max_iters` | low (4-6) — specialists should be focused |
| `A2AConfig.agent_url` | set (enables binding persistence) |
| `tool_visibility` | hide UI-only / rich-output tools |
| Worker concurrency | 20-50 |

Conversations are keyed by the A2A `contextId` (`session_id`): calls within one conversation are serialized, different conversations run in parallel. Size replicas for concurrent conversations, not requests.

## Recipe: HITL workflow (low traffic, long sessions)

| Knob | Value |
|---|---|
| `pause_enabled` | True |
| `deadline_s` | per active segment (the clock doesn't run while paused) |
| `BackgroundTasksConfig.task_timeout_s` | 3600s |
| Pause state TTL (StateStore) | 24h - 7d depending on UX |

Long sessions need durable state (`save_planner_state` / `load_planner_state`). Don't set deadlines that race the user's attention span.

## Sizing tool timeouts

Formula: `timeout_s = p99(latency) × 1.5` with a hard ceiling per workload type.

| Workload | Hard ceiling |
|---|---|
| Sync user-facing | 30s |
| Async batch | 300s |
| Long-running jobs | run them as background tasks instead |

Don't trust upstream "this is fast" without measurement.

## Sizing retries

| Operation | Retries |
|---|---|
| LLM calls (`llm_max_retries`) | 2-3 (rate limits: prefer `llm_fallback` to another model) |
| Idempotent network tool (HTTP GET, search) | 2-3 via `RetryPolicy` |
| Idempotent with idempotency key (POST + key) | 2-3 |
| Non-idempotent write | 0 — gate behind HITL or guardrails instead |
| Validation failures | handled by planner repair (`repair_attempts`), not tool retries |

Retries amplify failures during incidents: 3 workers × 3 retries turns a 1% transient error rate into ~9× load on the dependency.

## Sizing tool concurrency

Per `ExternalToolConfig.max_concurrency` (default 10):
- External SaaS: 3-5 (most have low concurrent-call limits).
- Internal services: 10-20.
- Search/embeddings: limit set by the provider's rate plan.

Across a worker: `sum(max_concurrency)` should fit your connection pools. One tool must not starve another.

## Worker count

```
replicas = ceil(peak_concurrent_runs / worker_concurrency)
peak_concurrent_runs ≈ peak_qps × p95_run_duration
```

Add 30% headroom. Autoscale on in-flight runs, queue depth or latency, not CPU (asyncio workers idle on I/O).

## Deadlines across agents

- Set `deadline_s` on the entry planner.
- Give A2A tool calls a `timeout_s` below the remaining budget, so the manager can still answer when a specialist is slow.
- Specialists should use a shorter `deadline_s` than their callers' tool timeouts.

When a deadline expires, the planner finishes with `reason="budget_exhausted"` and the last observation as payload, so callers always get a result.

## Sample SLOs

| Workload | SLO |
|---|---|
| Chat agent | 99% < 30s end-to-end |
| Specialist agent (A2A) | 99.5% < 10s; p99 < 30s |
| Batch | 99% complete within job deadline |
| Streaming | 99% TTFC < 2s |

Map each SLI to dashboard panels and alerts. See [[penguiflow-observability]] for the metric inventory.
