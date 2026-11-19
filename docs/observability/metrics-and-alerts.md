# Metrics & alerts

## What it is / when to use it

PenguiFlow emits structured planner events (`PlannerEvent`) and LLM telemetry (`LLMEvent`), but it does not ship a metrics exporter. This page shows:

- which metrics are worth emitting in production,
- how to derive them from planner events,
- what dashboards and alerts to build for "on-call ready" operations.

Start with **[Observability overview](overview.md)** if you need the full telemetry model across planner, LLM, and task/session layers.

## Non-goals / boundaries

- This page does not require Prometheus/Datadog/etc. (the patterns apply to any backend).
- It does not define your SLOs; it provides signals you can map to SLOs.
- Provider-level latency, tokens and cost come from LLM telemetry hooks; see **[Native LLM layer](../planner/native-llm.md)**.

## Contract surface (what you can measure)

From `PlannerEvent`:

- tool latency: `step_complete` / `tool_call_end` carry `node_name` and `latency_ms`,
- outcomes: `finish` carries `extra["reason"]` (`answer_complete`, `no_path`, `budget_exhausted`) and `extra["cost"]`,
- quality signals: `planner_args_invalid`, `planner_repair_attempt`, `finish_repair_failure`, `guardrail_retry`.

### Cardinality rules (critical)

- **Never** tag metrics by `trace_id` or `session_id` (unbounded cardinality).
- Be cautious tagging by `tenant` unless tenant count is bounded and expected.
- Prefer tagging by `event_type`, `node_name` (bounded tool set), finish `reason`, and your `env` / `service` labels.

## Operational defaults (recommended metric set)

### Counters

- `planner_runs_total` by finish reason,
- `planner_tool_calls_total` by `node_name`,
- `planner_repairs_total` (args invalid, repair attempts, finish repair failures).

### Histograms

- tool latency (ms) by `node_name`,
- steps per run (`trajectory_step` at `finish`).

### Gauges

- in-flight runs (increment on your request start, decrement on finish), and background tasks if you use sessions.

## Failure modes & recovery

- Prefer tenant-aggregated metrics only when the tenant set is small and contractually safe.
- A rising `budget_exhausted` or `no_path` rate usually means tool descriptions or budgets need attention, not infrastructure.

## Observability (dashboards and alerts)

### Dashboards (recommended)

- finish reasons over time,
- p50/p95 tool latency by tool,
- repair/validation failure rate,
- LLM latency and cost (from LLM telemetry).

### Alerts (starter set)

- `no_path` + `budget_exhausted` share above your baseline,
- p95 latency for a critical tool above its timeout budget,
- sustained spike in `planner_args_invalid`.

## Security / multi-tenancy notes

- Keep metric labels free of user content (queries, tool args, answers).

## Runnable example: deriving Prometheus metrics from `PlannerEvent`

This example uses `prometheus_client` (optional dependency).

```python
from __future__ import annotations

from prometheus_client import Counter, Histogram, start_http_server

from penguiflow.planner import PlannerEvent, ReactPlanner

RUNS = Counter("penguiflow_planner_runs_total", "Planner runs", labelnames=("reason",))
TOOL_LATENCY_MS = Histogram(
    "penguiflow_tool_latency_ms",
    "Tool latency (ms)",
    labelnames=("node_name",),
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
)


def metrics_callback(event: PlannerEvent) -> None:
    if event.event_type == "finish":
        RUNS.labels(str(event.extra.get("reason", "unknown"))).inc()
    elif event.event_type == "step_complete" and event.latency_ms is not None:
        TOOL_LATENCY_MS.labels(event.node_name or "unknown").observe(float(event.latency_ms))


start_http_server(8000)
planner = ReactPlanner(llm_client=..., catalog=..., event_callback=metrics_callback)
```

## Troubleshooting checklist

- **No metrics**: ensure the callback is passed as `event_callback` and your exporter is running.
- **Too many time series**: remove labels (never add `trace_id` or `session_id`).
- **Latency numbers look wrong**: only observe latency when `event.latency_ms` is present.
