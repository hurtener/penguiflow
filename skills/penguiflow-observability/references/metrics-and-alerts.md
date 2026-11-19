# Metrics, Dashboards, and Alerts

PenguiFlow doesn't ship a metrics exporter for the planner — you derive metrics from `PlannerEvent`s in an `event_callback` and from the returned `PlannerFinish`. LLM-level metrics can come from `penguiflow.llm.telemetry` (`create_prometheus_callback()`). This reference covers the recommended baseline.

## Cardinality rules (read first)

**Never** tag metrics by `trace_id`, `session_id`, `task_id` or `user_id`. They're unbounded.

**Be cautious** with:
- `tenant` — only if tenant count is bounded and small.
- `error_class` — only if exception types are a closed set.
- `model` — fine when you run a handful of models.

**Safe** tags:
- `event_type`
- tool name (bounded by your catalog)
- finish `reason` (`answer_complete` / `no_path` / `budget_exhausted`)
- `env`, `service`, `worker_id`

Move correlation to logs, not metrics.

## Counters

| Counter | Source | Tags |
|---|---|---|
| `pf_runs_total` | `PlannerFinish.reason` (or `finish` event `extra["reason"]`); `PlannerPause` → `reason="paused"` | `reason` |
| `pf_tool_calls_total` | `tool_call_start` | `tool` (`extra["tool_name"]`) |
| `pf_tool_errors_total` | steps whose history entry has an `error` | `tool` |
| `pf_args_invalid_total` | `planner_args_invalid` | `tool` (`extra["tool"]`) |
| `pf_finish_repairs_total` | `finish_repair_success` / `finish_repair_failure` | `outcome` |
| `pf_guardrail_retries_total` | `guardrail_retry` | — |
| `pf_steering_total` | `steering_received` | `kind` (`extra["event_type"]`) |

## Histograms

| Histogram | Source | Tags | Buckets |
|---|---|---|---|
| `pf_step_latency_ms` | `step_complete.latency_ms` | `tool` (`node_name`) | `5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000` |
| `pf_run_duration_s` | wall clock around `planner.run()` | `reason` | `0.5, 1, 2, 5, 10, 30, 60, 120` |
| `pf_run_steps` | `PlannerFinish.metadata["step_count"]` | `reason` | `1, 2, 3, 5, 8, 13, 21` |
| `pf_run_cost_usd` | `PlannerFinish.metadata["cost"]["total_cost_usd"]` | — | per your pricing |

## Prometheus recipe

```python
import time
from prometheus_client import Counter, Histogram

RUNS = Counter("pf_runs_total", "Planner runs", ["reason"])
TOOL_CALLS = Counter("pf_tool_calls_total", "Tool calls", ["tool"])
STEP_LATENCY = Histogram(
    "pf_step_latency_ms", "Tool step latency", ["tool"],
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
)
RUN_DURATION = Histogram("pf_run_duration_s", "Run duration", ["reason"])

def on_event(event: PlannerEvent) -> None:
    if event.event_type == "tool_call_start":
        TOOL_CALLS.labels(tool=event.extra.get("tool_name", "unknown")).inc()
    elif event.event_type == "step_complete" and event.latency_ms is not None:
        STEP_LATENCY.labels(tool=event.node_name or "unknown").observe(event.latency_ms)

async def run(planner, query, **kwargs):
    started = time.monotonic()
    result = await planner.run(query, **kwargs)
    reason = result.reason if isinstance(result, PlannerFinish) else "paused"
    RUNS.labels(reason=reason).inc()
    RUN_DURATION.labels(reason=reason).observe(time.monotonic() - started)
    return result
```

Attach with `ReactPlanner(..., event_callback=on_event)`. Keep the callback cheap — it runs inline in the planner loop.

## LLM metrics

```python
from penguiflow.llm.telemetry import create_prometheus_callback, get_telemetry_hooks

get_telemetry_hooks().register(create_prometheus_callback())
```

Exports `llm_requests_total` (by provider/model/status), `llm_request_latency_seconds` and `llm_tokens_total` from `LLMEvent`s (`request_start`, `request_end`, `retry`, `error`). Requires `prometheus_client`. For spans with tokens and cost, set `PENGUIFLOW_LLM_TRACING=mlflow` (or `log`).

## OpenTelemetry recipe

```python
from opentelemetry import metrics
meter = metrics.get_meter("penguiflow")

step_latency = meter.create_histogram("pf.step.latency_ms", "ms")
tool_calls = meter.create_counter("pf.tool.calls")

def on_event(event: PlannerEvent) -> None:
    if event.event_type == "tool_call_start":
        tool_calls.add(1, {"tool": event.extra.get("tool_name", "unknown")})
    elif event.event_type == "step_complete" and event.latency_ms is not None:
        step_latency.record(event.latency_ms, {"tool": event.node_name or "unknown"})
```

## Dashboards (recommended)

### 1. Outcomes
- Runs/s by finish reason; `no_path` + `budget_exhausted` share.
- Pauses (HITL) per hour.

### 2. Latency
- Run duration p50/p95/p99.
- Step latency per top-N tools.
- LLM latency per model (from LLM telemetry).

### 3. Quality
- `planner_args_invalid`, finish repairs, guardrail retries per run.
- Steps per run distribution.

### 4. Cost
- Cost per run and per tenant (aggregate in logs if tenant is unbounded).
- Tokens per model.

## Alerts (starter set)

| Alert | Condition | Suggested threshold |
|---|---|---|
| Failure spike | `(no_path + budget_exhausted) / all runs` | >5% sustained 10m |
| Tool error spike | `rate(pf_tool_errors_total[5m])` by tool | above baseline + 3σ |
| Repair storm | `rate(pf_args_invalid_total[5m]) / rate(pf_tool_calls_total[5m])` | >20% |
| Latency regression | `pf_run_duration_s` p95 | above SLO for 15m |
| LLM errors | LLM `error` events per model | above baseline |
| Cost surge | cost per run p95 | 2× baseline |

Always combine with SLO-burn alerting for the user-visible SLIs (answer success rate, end-to-end latency p95).

## Common mistakes

- Tagging by `trace_id` / `session_id`. Cardinality explosion.
- Tagging by `error_class` for unbounded exception types.
- Doing blocking I/O (pushing to a remote metrics API) inside `event_callback`.
- Counting `PlannerPause` as a failure — it's a HITL checkpoint.
- Alerting on absolute counts instead of rates — false alerts at low traffic.

## Cost shape

- Counters: cheap.
- Histograms: moderately expensive (bucket count × cardinality).

Tune histogram buckets to your latency distribution; agent runs are often seconds to minutes.
