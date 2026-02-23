# Logging

## What it is / when to use it

PenguiFlow uses standard Python logging and emits structured runtime events (`FlowEvent`) that you can capture and forward to your logging/telemetry stack.

Use this page when you want:

- consistent JSON logs for production,
- trace-correlated debugging (`trace_id`),
- a reliable way to observe node lifecycle (success/error/timeout/retry).

## Non-goals / boundaries

- PenguiFlow does not ship a vendor-specific logging integration (Datadog, ELK, Splunk).
- The library does not automatically redact secrets/PII; you must avoid logging sensitive payloads.
- This page is about logs; metrics and alerting are covered separately.

## Contract surface

### `configure_logging(...)`

`configure_logging` configures the `penguiflow` logger (and its children) with either:

- **structured JSON lines** (`structured=True`), or
- human-readable logs, optionally appending `extra={...}` fields (`include_extras=True`).

```python
from penguiflow import configure_logging

configure_logging(level="INFO", structured=True)
```

### Runtime `FlowEvent`

The runtime emits `FlowEvent` objects with:

- `event_type` (e.g. `node_success`, `node_error`, `node_timeout`, `node_retry`, `deadline_skip`, …),
- queue depth (incoming/outgoing),
- latency (ms) when applicable,
- trace-level inflight/pending counts when a `trace_id` exists.

You can:

- log them directly (`event.to_payload()`), and/or
- derive metrics (`event.metric_samples()`).

### `log_flow_events(...)` middleware

`log_flow_events` is a ready-to-use middleware that logs node lifecycle events with structured `extra` payloads.

```python
import logging

from penguiflow import create, log_flow_events

flow = create(
    ...,
    middlewares=[log_flow_events(logging.getLogger("penguiflow.flow"))],
)
```

## Operational defaults (recommended)

- Turn on JSON logs in production: `configure_logging(structured=True)`.
- Log with **stable identifiers**:
  - `trace_id` (correlation),
  - `node_name` / `node_id`,
  - `tenant` (if multi-tenant).
- Avoid logging large payloads; prefer artifacts/resources and log references.
- Keep log volumes bounded (sampling or level tuning) for `node_start` events if needed.

## Failure modes & recovery

- **No FlowEvents in logs**: you didn’t attach middleware. Fix: pass `middlewares=[log_flow_events(...)]` to `create(...)`.
- **Duplicate logs**: you configured both root logger handlers and `configure_logging`, or you enabled propagation incorrectly. Fix: use one logging entrypoint; keep `penguiflow` logger configured once per process.
- **Logs missing `trace_id`**: you’re using payload-only messages or not setting/propagating trace ids. Fix: use envelopes (`Message`) and trace-scoped emit/fetch.
- **Sensitive data leaked**: you logged raw tool outputs/payloads. Fix: redact at boundaries; log only ids + summaries.

## Observability (what to log)

Minimum recommended log events:

- runtime:
  - `node_error`, `node_failed`, `node_timeout` at WARNING/ERROR,
  - `node_success` at INFO (optionally sampled),
  - `node_retry` at INFO,
  - `trace_cancel_start` / `trace_cancel_finish` at INFO,
  - `deadline_skip` at INFO.
- application:
  - job/request received (with `trace_id`, `tenant`),
  - job/request completed (latency + status),
  - external dependency failures (tool boundary).

## Security / multi-tenancy notes

- Never include secrets in log fields. Assume logs are broadly accessible in an enterprise.
- Do not tag metrics by `trace_id` (cardinality explosion); for logs, `trace_id` is correct and expected.
- Treat `trace_id` as sensitive if it can be used to fetch/cancel work in your app.

## Runnable example: structured FlowEvent logging

```python
from __future__ import annotations

import asyncio
import logging

from penguiflow import Node, NodePolicy, configure_logging, create, log_flow_events


async def nop(msg, _ctx):
    return msg


async def main() -> None:
    configure_logging(structured=True)

    logger = logging.getLogger("penguiflow.flow")
    node = Node(nop, name="nop", policy=NodePolicy(timeout_s=5, max_retries=0))
    flow = create(node.to(), middlewares=[log_flow_events(logger)])
    flow.run()
    await flow.emit({"hello": "world"})
    _ = await flow.fetch()
    await flow.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- Need production event patterns: see **[Telemetry patterns](telemetry-patterns.md)**.
- Need dashboards + alerts: see **[Metrics & alerts](metrics-and-alerts.md)**.
