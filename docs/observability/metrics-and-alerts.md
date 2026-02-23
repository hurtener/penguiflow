# Metrics & alerts

## What it is / when to use it

PenguiFlow ships structured runtime events (`FlowEvent`), but it does not ship a metrics exporter. This page shows:

- which metrics are worth emitting in production,
- how to derive them from `FlowEvent`,
- what dashboards and alerts to build for “on-call ready” operations.

Use it when you are deploying a worker/service fleet and want predictable detection for saturation, timeouts, and error storms.

## Non-goals / boundaries

- This page does not require Prometheus/Datadog/etc. (the patterns apply to any backend).
- It does not define your SLOs; it provides signals you can map to SLOs.
- It is runtime-focused; planner-specific metrics belong in planner observability docs.

## Contract surface (what you can measure)

### `FlowEvent.metric_samples()` + `FlowEvent.tag_values()`

`FlowEvent` provides:

- numeric samples: queue depths, latency (when present), attempt counts, trace inflight/pending counts,
- tags: event type, node identifiers, and safe string extras.

### Cardinality rules (critical)

- **Never** tag metrics by `trace_id` (unbounded cardinality).
- Be cautious tagging by `tenant` unless tenant count is bounded and expected.
- Prefer tagging by:
  - `event_type`,
  - `node_name` (bounded set),
  - `env` / `service` (your platform labels).

## Operational defaults (recommended metric set)

### Counters

- node lifecycle totals:
  - `node_success_total`, `node_error_total`, `node_timeout_total`, `node_retry_total`, `node_failed_total`
- control-plane totals:
  - `trace_cancel_start_total`, `trace_cancel_finish_total`, `deadline_skip_total`

### Histograms

- node latency (ms), by `node_name` and outcome (`success` / `error` / `timeout`).

### Gauges

- queue depth:
  - `queue_depth_in`, `queue_depth_out`, `queue_depth_total`
- optional:
  - `trace_inflight` (careful: only meaningful for envelope/trace-scoped systems)

## Failure modes & recovery

- **Everything looks “green” but users complain**: you don’t have saturation signals. Fix: add queue depth + node latency histograms.
- **Metrics backend melts**: you tagged by `trace_id` or unbounded extras. Fix: reduce labels; move correlation to logs.
- **You can’t explain retries**: you only measure errors. Fix: measure retries and retry delays (`node_retry`).

## Observability (dashboards and alerts)

### Dashboards (recommended)

1. **System overview**
   - throughput (jobs/s or requests/s)
   - error/timeout rates
   - p50/p95/p99 node latency (top N nodes)
2. **Saturation**
   - queue depth over time (total + by edge if you expose it)
   - trace inflight counts (if applicable)
3. **Reliability**
   - retries over time
   - `node_failed` counts (terminal failures)
4. **Control plane**
   - cancellations and deadline skips (rate + spikes)

### Alerts (starter set)

- **Saturation**: queue depth stays above threshold for N minutes.
- **Timeout regression**: `node_timeout_total` rate exceeds baseline.
- **Error regression**: `node_error_total` or `node_failed_total` rate exceeds baseline.
- **Retry storm**: `node_retry_total / node_success_total` ratio spikes.
- **Deadline misconfiguration**: `deadline_skip_total` spikes after a deploy.
- **Cancellation spike**: `trace_cancel_start_total` spikes (could be user-driven or systemic).

## Security / multi-tenancy notes

- Metrics often have broader internal visibility than logs; keep labels low-sensitivity.
- Avoid emitting raw payload-derived labels (PII).
- Prefer tenant-aggregated metrics only when the tenant set is small and contractually safe.

## Runnable example: deriving Prometheus metrics from `FlowEvent`

This example uses `prometheus_client` (optional dependency) to expose counters/histograms based on runtime events.

```python
from __future__ import annotations

import asyncio

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from penguiflow import FlowEvent, Node, NodePolicy, create


NODE_EVENTS = Counter(
    "penguiflow_node_events_total",
    "Runtime node events",
    labelnames=("event_type", "node_name"),
)
NODE_LATENCY_MS = Histogram(
    "penguiflow_node_latency_ms",
    "Runtime node latency (ms)",
    labelnames=("event_type", "node_name"),
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
)
QUEUE_DEPTH = Gauge(
    "penguiflow_queue_depth_total",
    "Combined incoming+outgoing queue depth",
    labelnames=("node_name",),
)


async def metrics_middleware(event: FlowEvent) -> None:
    node = event.node_name or "unknown"
    NODE_EVENTS.labels(event.event_type, node).inc()
    QUEUE_DEPTH.labels(node).set(float(event.queue_depth))
    if event.latency_ms is not None:
        NODE_LATENCY_MS.labels(event.event_type, node).observe(float(event.latency_ms))


async def nop(msg, _ctx):
    return msg


async def main() -> None:
    start_http_server(8000)

    node = Node(nop, name="nop", policy=NodePolicy(timeout_s=5, max_retries=0))
    flow = create(node.to(), middlewares=[metrics_middleware])
    flow.run()

    await flow.emit({"hello": "world"})
    _ = await flow.fetch()
    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- **No metrics**: ensure your middleware is attached to the flow and your exporter is running.
- **Too many time series**: remove labels (never add `trace_id`).
- **Latency numbers look wrong**: ensure you only observe latency when `event.latency_ms` is present.
