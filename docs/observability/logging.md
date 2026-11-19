# Logging

## What it is / when to use it

PenguiFlow uses standard Python logging. The planner logs every `PlannerEvent` on the `penguiflow.planner` logger and also hands it to your `event_callback`, so you can capture and forward agent activity to your logging stack.

Use this page when you want:

- consistent JSON logs for production,
- trace-correlated debugging (`trace_id`, `session_id`),
- a reliable way to observe planner steps, tool calls and finish reasons.

Start with **[Observability overview](overview.md)** if you need the full telemetry model across planner, LLM, and task/session layers.

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

### Planner events

Every `PlannerEvent` carries `event_type`, `ts`, `trajectory_step`, and when relevant `node_name`, `latency_ms`, `thought`, `error` and `extra`. `event.to_payload()` returns a flat dict that is safe to pass as logging `extra`.

The planner logs each event itself: `llm_stream_chunk` at DEBUG and everything else at INFO. To route events elsewhere, pass a callback:

```python
import logging

from penguiflow.planner import PlannerEvent, ReactPlanner

audit = logging.getLogger("myapp.agent")


def record(event: PlannerEvent) -> None:
    audit.info(event.event_type, extra=event.to_payload())


planner = ReactPlanner(llm_client=..., catalog=..., event_callback=record)
```

## Operational defaults (recommended)

- Turn on JSON logs in production: `configure_logging(structured=True)`.
- Log with **stable identifiers**: `trace_id`, `session_id`, `node_name` (tool), `tenant` if multi-tenant.
- Avoid logging large tool payloads; prefer artifacts and log references.
- Keep `llm_stream_chunk` at DEBUG; it is high volume.

## Failure modes & recovery

- **No planner events in logs**: the `penguiflow` logger isn't configured. Fix: call `configure_logging(...)` once at startup, or configure `penguiflow.planner` yourself.
- **Duplicate logs**: you configured both root logger handlers and `configure_logging`, or enabled propagation incorrectly. Fix: use one logging entrypoint per process.
- **Logs missing correlation ids**: pass `tenant`/`session_id` in `tool_context` and include them in your callback's log fields.
- **Sensitive data leaked**: you logged raw tool outputs or `tool_context`. Fix: redact at boundaries; log only ids and summaries.

## Observability (what to log)

Minimum recommended log events:

- planner:
  - `finish` (reason, cost) at INFO,
  - `tool_call_end` / `step_complete` with `latency_ms` at INFO,
  - `planner_args_invalid`, `planner_repair_attempt`, `guardrail_retry` at WARNING,
  - `steering_received`, `resume` at INFO.
- application:
  - request received (with `tenant`, `session_id`),
  - request completed (latency + finish reason),
  - external dependency failures (tool boundary).

## Security / multi-tenancy notes

- Never include secrets in log fields. Assume logs are broadly accessible in an enterprise.
- Do not tag metrics by `trace_id` (cardinality explosion); for logs, `trace_id` is correct and expected.
- Treat resume tokens and session ids as sensitive if they can be used to drive work in your app.

## Troubleshooting checklist

- Need the full event model: see **[Observability overview](overview.md)**.
- Need production event patterns: see **[Telemetry patterns](telemetry-patterns.md)**.
- Need dashboards + alerts: see **[Metrics & alerts](metrics-and-alerts.md)**.
