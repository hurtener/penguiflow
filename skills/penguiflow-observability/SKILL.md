---
name: penguiflow-observability
description: Add structured observability to a ReactPlanner deployment — turn on JSON logging with `configure_logging(structured=True)`, consume `PlannerEvent`s (`step_start`/`step_complete`/`tool_call_*`/`finish`/`planner_args_invalid`/`guardrail_*`/...) through `event_callback` or the `penguiflow.planner` logger, derive counters/histograms with safe cardinality (never tag by `trace_id`/`session_id`), trace LLM calls with `PENGUIFLOW_LLM_TRACING`/`LLMTraceSink` and `TelemetryHooks`, record task lifecycle with a `TaskTelemetrySink`, read run cost/steps from `PlannerFinish.metadata`, and inspect persisted history with `penguiflow-admin`. Use when a user says "add observability", "logging", "metrics", "alerts", "trace this agent", "LLM tracing", "cost tracking", or asks how to monitor a production planner fleet.
---

# PenguiFlow Observability

## When to use
- Wiring structured logging into a production planner deployment.
- Defining the metric/alert baseline for planner workers.
- Tracing LLM calls (latency, tokens, cost) to logs or MLflow.
- Investigating a run after the fact (steps, errors, persisted history).

## When NOT to use
- Shipping events to frontend clients → use [[penguiflow-streaming]].
- Planner configuration (which features emit which events) → use [[penguiflow-reactplanner-config]].
- StateStore backends for durable history → use [[penguiflow-statestore]].
- Guardrail decision auditing in depth → use [[penguiflow-guardrails]].

## Hard boundaries
This skill is the **operator observability surface**: logs, planner events as metrics, LLM tracing, task telemetry, cost, and offline inspection. Client-facing event delivery lives in [[penguiflow-streaming]].

## Workflow

### 1) Turn on structured logging
```python
from penguiflow import configure_logging
configure_logging(level="INFO", structured=True)
```
Configures the `penguiflow` logger tree to emit JSON lines (`structured=False, include_extras=True` for readable dev output). Useful sub-loggers: `penguiflow.planner` (every planner event), `penguiflow.llm.telemetry` / `penguiflow.llm.tracing` (LLM calls), `penguiflow.tools.node` (ToolNode/MCP), `penguiflow_a2a` (A2A server).

### 2) Know what the planner already logs
Every `PlannerEvent` is logged on `penguiflow.planner` with `event.to_payload()` as `extra` — `llm_stream_chunk` at DEBUG, everything else at INFO. Lower that logger to WARNING in production if volume is a problem, and use step 3 for metrics.

### 3) Hook `event_callback` for metrics
```python
def on_event(event: PlannerEvent) -> None:      # synchronous, keep it cheap
    if event.event_type == "step_complete":
        STEP_LATENCY.labels(tool=event.node_name or "unknown").observe((event.latency_ms or 0) / 1000)
    elif event.event_type == "finish":
        FINISH.labels(reason=event.extra.get("reason", "unknown")).inc()

planner = ReactPlanner(..., event_callback=on_event)
```
Event catalog and fields in `references/flow-events-and-logging.md`.

### 4) Derive metrics with the right cardinality
**Never tag metrics by `trace_id`, `session_id`, `task_id` or `user_id`.** Safe tags: `event_type`, tool name (bounded by your catalog), finish `reason`, `env`, `service`. `tenant` only if bounded.

Recommended baseline:
- **Counters**: runs by finish reason (`answer_complete` / `no_path` / `budget_exhausted`), tool calls and tool errors by tool, `planner_args_invalid`, `finish_repair_*`, `guardrail_retry`, pauses.
- **Histograms**: `step_complete.latency_ms` by tool; run duration; steps per run (`PlannerFinish.metadata["step_count"]`).
- **Cost**: `PlannerFinish.metadata["cost"]` (also `finish` event `extra["cost"]`) per run.

Recipes in `references/metrics-and-alerts.md`.

### 5) Trace LLM calls
- Zero-code: `export PENGUIFLOW_LLM_TRACING=mlflow` (or `log`) — every native-adapter completion becomes a span with provider, model, tokens, cost and latency (never message contents).
- Explicit: pass `trace_sink=MlflowLLMTraceSink()` / `LoggingLLMTraceSink()` (from `penguiflow.llm.tracing`) when building the native adapter.
- Raw LLM events (`request_start`/`request_end`/`retry`/`error`): `get_telemetry_hooks().register(callback)` from `penguiflow.llm.telemetry`; `create_mlflow_callback()` / `create_prometheus_callback()` are ready-made.

Worked example: `examples/mlflow_llm_tracing/`.

### 6) Task lifecycle with sessions
`StreamingSession` / `SessionManager` accept `telemetry_sink=` — a `TaskTelemetrySink` receiving `TaskTelemetryEvent`s (spawned/completed/failed/cancelled, `duration_ms`, task type). `LoggingTaskTelemetrySink` logs them. See [[penguiflow-background-tasks]].

### 7) Inspect runs after the fact
- `PlannerFinish.metadata` carries `steps` (the full trajectory with observations/errors), `step_count`, `cost`, `sources`, `artifacts`, and repair counters.
- Persisted event history: `penguiflow-admin history <trace_id> --state-store module:factory` (or `replay --delay 0.2`) prints `StoredEvent`s from any `StateStore`.
- Export the tool catalog for reviews: `[spec.to_tool_record() for spec in catalog]` — see `references/topology-export.md`.

### 8) Don't leak secrets into logs/metrics
- Planner event payloads can include tool args (`tool_call_start.extra["args_json"]`) and observations — redact before shipping logs off-host.
- Keep secrets in `tool_context` (never sent to the LLM), not `llm_context`.
- Sanitize exception messages raised by tools; they surface in `tool_call_result` / step errors.

## Troubleshooting (fast checks)
- **No planner events in logs** — `configure_logging` not called, or the `penguiflow.planner` logger level is above INFO.
- **Duplicate logs** — root handlers and `configure_logging` both configured; pick one entrypoint.
- **`event_callback` metrics missing under load** — one shared planner instance with a per-request callback overwritten by another request; set a process-wide metrics callback, or build planners from a factory.
- **Metric backend OOMs** — tagged by `trace_id`/`session_id`; demote to a log field.
- **No LLM spans** — `PENGUIFLOW_LLM_TRACING` unset, MLflow not installed, or the planner uses the legacy LiteLLM client (tracing covers the native adapter).
- **`penguiflow-admin` prints nothing** — the store never received events for that trace id; check the session/state store wiring.
- **Sensitive data in logs** — tool args or observations logged verbatim; redact via a logging filter.

## Worked examples
- `examples/mlflow_llm_tracing/` — LLM tracing to MLflow.
- `examples/planner_enterprise_agent_v2/telemetry.py` — planner-event telemetry collector.
- `examples/streaming_llm/stream_answer.py` — `event_callback` wiring.

## References (load only as needed)
- `references/flow-events-and-logging.md` — `configure_logging` options, `PlannerEvent` catalog and fields, logging volume, redaction.
- `references/metrics-and-alerts.md` — counters/histograms, cardinality rules, Prometheus/OpenTelemetry recipes, dashboards, alerts.
- `references/topology-export.md` — exporting the tool catalog (`to_tool_record`) for docs, reviews and CI diffs.
