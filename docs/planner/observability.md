# Planner observability

## What this is / when to use it

This page documents how to observe and operate `ReactPlanner` in production:

- structured event streams (planner-level)
- what to log and what to redact
- what to monitor and alert on

## Non-goals / boundaries

- This page does not mandate a specific telemetry backend (Datadog, Prometheus, OpenTelemetry).
- This page does not cover runtime-level `FlowEvent` in detail (see `docs/observability/*`).

## Contract surface

### `event_callback`

`ReactPlanner` accepts an `event_callback` that receives `PlannerEvent` objects:

- `penguiflow.planner.models.PlannerEvent`

Common event types include:

- `step_start`, `step_complete`
- `llm_call`
- `tool_call_start`, `tool_call_end`, `tool_call_result`
- `pause`, `resume`, `finish`
- `stream_chunk`, `artifact_chunk`, `llm_stream_chunk`
- `observation_clamped` (planner-level safety net for oversized observations)
- `steering_received` (when steering inbox events are drained)
- `guardrail_retry` (when a guardrail requests an LLM retry)

## Runnable example: logging PlannerEvent

```python
import logging

from penguiflow.planner.models import PlannerEvent


log = logging.getLogger("penguiflow.planner.events")


def on_event(ev: PlannerEvent) -> None:
    # Avoid reserved logging keys; PlannerEvent.to_payload() filters them.
    log.info(ev.event_type, extra=ev.to_payload())
```

## Operational defaults (what to record)

At minimum record:

- finish reason (`answer_complete` / `no_path` / `budget_exhausted`)
- planner step count and hop budget usage
- LLM call latency and retries
- tool call latency and error rates
- pause frequency and resume latency
- observation clamping/truncation events (if enabled)

## Failure modes & recovery

### “Works locally, fails in production”

Common causes:

- missing env vars for ToolNode (`${VAR}` substitution is fail-fast)
- tool visibility differs by tenant/user
- state store not configured for distributed pause/resume

### Silent data leaks in logs

Do not log:

- raw `llm_context` (may contain user text or derived sensitive content)
- raw tool outputs for external APIs

Prefer:

- artifact references (ids + metadata)
- redacted summaries

## Security / multi-tenancy notes

- Treat `tool_context` as privileged: it may contain secrets and clients.
- Keep per-tenant isolation in:
  - memory keys
  - tool visibility policies
  - artifact scopes and access checks

## Troubleshooting checklist

- **No planner events**: confirm `event_callback` is passed and not overwritten by per-session dispatch.
- **Missing stream chunks**: verify `stream_final_response` and tool streaming usage; confirm UI is wired to the stream sink.
- **High tool error rate**: tighten retries/timeouts and reduce concurrency to respect rate limits.
