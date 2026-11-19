---
name: penguiflow-observability
description: Add structured observability to a PenguiFlow runtime — turn on JSON logging with `configure_logging(structured=True)`, attach `log_flow_events` middleware to capture `FlowEvent` lifecycle (`node_start`/`node_success`/`node_error`/`node_timeout`/`node_retry`/`node_failed`/`deadline_skip`/`trace_cancel_*`), derive counters/histograms/gauges via `FlowEvent.metric_samples()`/`tag_values()` while obeying cardinality rules (never tag by `trace_id`), wire `FlowError`/`FlowErrorCode` for traceable failures, and export topology with `flow_to_mermaid`/`flow_to_dot`. Use when a user says "add observability", "logging", "metrics", "alerts", "trace this flow", "visualize", or asks how to monitor a production worker fleet.
---

# PenguiFlow Observability

## When to use
- Wiring structured logging into a production deployment.
- Defining the metric/alert baseline for a worker fleet.
- Debugging by visualizing graph topology.
- Surfacing traceable errors (`FlowError`) to logs and dashboards.

## When NOT to use
- Planner-internal events (`PlannerEvent`, tool-call lifecycle) → use [[penguiflow-reactplanner-config]] for the planner observability surface.
- Shipping events to frontend clients → use [[penguiflow-streaming]] (SSE/WebSocket helpers).
- StateStore-backed audit log → use [[penguiflow-statestore]] (events get persisted via `save_event`).
- Streaming chunk metrics → use [[penguiflow-streaming]] for time-to-first-chunk instrumentation.

## Hard boundaries
This skill is the **runtime observability surface**: logs, `FlowEvent`, middleware-derived metrics, topology export. Planner-side observability (`PlannerEvent`) is the planner skill's job. Frontend event delivery (SSE/WebSocket) lives in [[penguiflow-streaming]].

## Workflow

### 1) Turn on structured logging
```python
from penguiflow import configure_logging
configure_logging(level="INFO", structured=True)
```
Configures the `penguiflow` logger to emit JSON lines. For human-readable output during dev: `configure_logging(structured=False, include_extras=True)`. The `penguiflow.core` logger receives `FlowEvent` payloads when middleware is attached.

### 2) Attach `log_flow_events` middleware
```python
import logging
from penguiflow import create, log_flow_events

flow = create(
    ...,
    middlewares=[log_flow_events(logging.getLogger("penguiflow.flow"))],
)
```
The middleware logs `node_start`/`node_success`/`node_error` only (defaults: INFO/INFO/ERROR; configurable via `start_level`/`success_level`/`error_level`). Payload is `event.to_payload()`; `node_error` also injects `error_payload`. All other event types (timeouts, retries, deadline skips, cancellations) pass through silently — wire a sibling middleware to log them.

### 3) Know your `FlowEvent` catalog
| `event_type` | When |
|---|---|
| `node_start` | Worker picked up a message for this node. |
| `node_success` | Node returned normally. |
| `node_error` | Node raised (will retry if budget allows). |
| `node_timeout` | `NodePolicy.timeout_s` fired. |
| `node_retry` | Retry scheduled with backoff. |
| `node_failed` | Retries exhausted; `FlowError` materialized. |
| `deadline_skip` | Expired `Message(deadline_s=...)` skipped. |
| `trace_cancel_start` | `flow.cancel(trace_id)` invoked. |
| `trace_cancel_drop` | Runtime dropped queued messages for the trace. |
| `node_trace_cancelled` | In-flight node was cancelled. |

Each `FlowEvent` carries: `event_type`, `ts`, `node_name`, `node_id`, `trace_id`, `attempt`, `latency_ms`, `queue_depth_in`, `queue_depth_out`, `outgoing_edges`, `queue_maxsize`, `trace_pending`, `trace_inflight`, `trace_cancelled`, `extra`. `queue_depth_total` is a **computed property**, not a field. Use `event.to_payload()` for logging (it renames `event_type`→`event`, depth keys to `q_depth_*`, and merges `extra` flat), `event.metric_samples()` + `event.tag_values()` for metrics, and `event.error_payload` for structured `FlowError` data on `node_error`.

### 4) Derive metrics with the right cardinality
**Critical rule: never tag metrics by `trace_id`.** It's unbounded. Safe tags: `event_type`, `node_name`, `env`, `service`. `tenant` only if bounded. `error_class` only if bounded.

Recommended baseline:
- **Counters**: `node_success_total`, `node_error_total`, `node_timeout_total`, `node_retry_total`, `node_failed_total`, `trace_cancel_start_total`, `trace_cancel_finish_total`, `deadline_skip_total`.
- **Histograms**: node latency (ms) by `node_name` + outcome.
- **Gauges**: `queue_depth_in`, `queue_depth_out`, `queue_depth_total`, `trace_inflight`.

Recipes for Prometheus / StatsD / OpenTelemetry adapters in `references/metrics-and-alerts.md`.

### 5) Wire alerts on the right SLIs
Starter alert set: error rate spike, retry storm, timeout spike, queue saturation (`queue_depth_total` trending up), `node_failed` accumulation, `deadline_skip` spike. Specific thresholds + dashboards in `references/metrics-and-alerts.md`.

### 6) Use `FlowError` for terminal failures
When retries exhaust, runtime builds a `FlowError(trace_id, node_name, node_id, code, message, original_exc, ...)`. Code values from `FlowErrorCode` (e.g., `NODE_EXCEPTION`, `NODE_TIMEOUT`). To surface to callers via `fetch()`: `create(..., emit_errors_to_rookery=True)` — but only if `FlowError.message` is safe to expose.

For audit persistence, attach a `StateStore` — every `FlowEvent` gets a `save_event(StoredEvent(...))` call (best-effort). See [[penguiflow-statestore]].

### 7) Export topology with `flow_to_mermaid` / `flow_to_dot`
```python
from penguiflow import flow_to_mermaid, flow_to_dot
print(flow_to_mermaid(flow, direction="LR"))
print(flow_to_dot(flow, rankdir="TB"))
```
Mermaid for docs and PR reviews; DOT when you want Graphviz post-processing. Topology only — no runtime state.

### 8) Don't leak secrets into logs/metrics
- Avoid logging raw payloads — use ids and summaries.
- Don't put secrets in `Message.meta` if events are persisted or logged.
- Strip exception representations of URLs/headers/tokens at a middleware boundary if you can't trust upstream code.

## Troubleshooting (fast checks)
- **No `FlowEvent` in logs** — `log_flow_events` middleware not attached; pass it in `create(..., middlewares=[...])`.
- **Duplicate logs** — both root handlers and `configure_logging` configured; pick one entrypoint.
- **Logs missing `trace_id`** — payload-only flow; switch to `Message` envelopes.
- **Metric backend OOMs** — you tagged by `trace_id` or other unbounded label; demote to a log field.
- **Latency histograms missing** — you're sampling `node_success` only; some events have no latency (`node_start`); guard for `None`.
- **Topology export shows anonymous labels** — nodes lack `name=`; always set it.
- **`FlowError` doesn't reach callers** — `emit_errors_to_rookery=False` (default); enable if safe.
- **Sensitive data in logs** — raw payloads or `repr(exc)` containing URLs/tokens; redact at middleware boundary.

## Worked examples
- `examples/visualizer/flow.py` — Mermaid/DOT export.
- `examples/mlflow_metrics/flow.py` — MLflow exporter pattern.
- `examples/traceable_errors/flow.py` — `FlowError` to Rookery and through events.
- `examples/reliability_middleware/flow.py` — custom middleware on top of `log_flow_events`.

## References (load only as needed)
- `references/flow-events-and-logging.md` — full `FlowEvent` catalog, `configure_logging` options, custom middleware patterns, redaction.
- `references/metrics-and-alerts.md` — counters/histograms/gauges, cardinality rules, Prometheus/StatsD recipes, dashboards, alerts.
- `references/topology-export.md` — `flow_to_mermaid`/`flow_to_dot` knobs, CI integration, "as-built" docs pattern.
