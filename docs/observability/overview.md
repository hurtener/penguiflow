# Observability overview

## What it is / when to use it

This page explains the **full observability model** in PenguiFlow:

- which telemetry surfaces exist,
- what each surface is for,
- how correlation works across runtime, planner, LLM, and session/task layers,
- what PenguiFlow expects you to wire into your own logging and monitoring stack.

Use this page first when someone asks:

- "How does telemetry work in PenguiFlow?"
- "What should we integrate with our observability platform?"
- "Which event stream should we capture for this use case?"

## The model in one sentence

PenguiFlow is **event-driven and integration-first** for observability:

- the runtime emits `FlowEvent`,
- the planner emits `PlannerEvent`,
- the LLM layer emits `LLMEvent`,
- the session/task layer emits `TaskTelemetryEvent`,
- your application decides which of those to log, persist, transform into metrics, or forward to external systems.

## Non-goals / what PenguiFlow does not ship

PenguiFlow intentionally does **not** ship:

- a first-party OpenTelemetry span model or exporter,
- a built-in metrics backend or always-on metrics server,
- a vendor-owned observability integration for Datadog, New Relic, Grafana Cloud, Honeycomb, or similar platforms.

MLflow is available as an optional tracing integration for planner-based
agents. Other vendor integrations in the codebase are **examples and hooks**,
not platform guarantees. If you need production observability, wire PenguiFlow
events into your own stack deliberately.

## Contract surface

### Shared correlation rules

Use these keys consistently:

- `trace_id`: primary correlation key across runtime execution, planner activity, LLM operations, and many task/session events
- `Headers.tenant`: tenant boundary for multi-tenant envelope flows
- `session_id` / `task_id`: session-scoped and task-scoped correlation for background execution and interactive agents

Operational rule:

- include `trace_id` in logs and event payloads,
- do **not** use `trace_id` as a metric label,
- keep tenant identifiers low-cardinality and contractually safe before using them in metrics.

### Telemetry surfaces

| Surface | Event type / API | Emission point | Correlation key | Persistence path | Intended consumer |
|---|---|---|---|---|---|
| Runtime flow execution | `FlowEvent` | Core runtime around node execution, retries, cancellation, queueing | `trace_id` | logs via middleware; optional app-owned event persistence | operators, service owners, incident response |
| Planner/tool orchestration | `PlannerEvent` via `event_callback` | `ReactPlanner` steps, tool calls, pauses, finish reasons, stream chunks | `trace_id` | app-owned callback sink; optional `StateStore.save_planner_event(...)` | agent developers, Playground/UI, replay/debug tooling |
| Native LLM layer | `LLMEvent` via `TelemetryHooks` | provider request start/end, retries, streaming, usage, errors | `trace_id` when supplied | callback-driven only; examples for MLflow/Prometheus | platform teams, LLM cost/latency monitoring |
| Session/background-task layer | `TaskTelemetryEvent` via `TaskTelemetrySink` | task spawn, completion, failure, cancellation, task groups | `session_id`, `task_id`, optional `trace_id` | sink-defined; built-in logging sink or custom sink | agent platform teams, async task operators |

## Surface details

### Runtime: `FlowEvent`

`FlowEvent` is the canonical runtime event primitive. It captures:

- node lifecycle,
- queue depth,
- attempts/retries,
- latency,
- trace inflight/pending state,
- cancellation/deadline-related behavior.

It is designed for:

- structured logs via `to_payload()`,
- derived numeric metrics via `metric_samples()`,
- tag extraction via `tag_values()`.

Use runtime events to answer:

- "Which node is slow or failing?"
- "Is the system saturated?"
- "Did cancellation or a deadline suppress execution?"

See:

- **[Logging](logging.md)**
- **[Metrics & alerts](metrics-and-alerts.md)**
- **[Telemetry patterns](telemetry-patterns.md)**

### Planner: `PlannerEvent`

`PlannerEvent` is the planner-native execution stream emitted through `event_callback`.

Typical planner events include:

- `step_start`, `step_complete`
- `llm_call`
- `tool_call_start`, `tool_call_end`, `tool_call_result`
- `pause`, `resume`, `finish`
- `stream_chunk`, `llm_stream_chunk`, `artifact_chunk`
- planner safety and orchestration events such as `observation_clamped`, `steering_received`, `guardrail_retry`

Use planner events to answer:

- "What did the agent decide to do?"
- "Which tool calls happened and how long did they take?"
- "Why did the planner pause, stop, or exhaust budget?"

If you need replay/debugging beyond logs, persist planner events through a state store implementation that supports planner event storage.

See:

- **[Planner observability](../planner/observability.md)**

### LLM layer: `LLMEvent`

The native LLM layer exposes low-overhead hook-based telemetry through `TelemetryHooks`.

Typical LLM events include:

- `request_start`
- `request_end`
- `stream_chunk`
- `retry`
- `error`

Use LLM telemetry to answer:

- "Which provider/model is slow or expensive?"
- "Are retries increasing?"
- "Are token usage or cost patterns changing?"

Important boundary:

- LLM telemetry is **not** a full tracing system,
- built-in callbacks are examples,
- registration is explicit and application-owned.

See:

- **[Native LLM layer](../planner/native-llm.md)**

### Session and background tasks: `TaskTelemetryEvent`

The session/task layer exposes task lifecycle telemetry through `TaskTelemetrySink`.

Typical task events include:

- `task_spawned`
- `task_completed`
- `task_failed`
- `task_cancelled`
- `task_group_completed`
- `task_group_failed`

Use task telemetry to answer:

- "Are background tasks completing?"
- "Which sessions are spawning too much concurrent work?"
- "Are task groups failing or stalling?"

Important boundary:

- the default sink can be `NoOpTaskTelemetrySink`,
- task telemetry is not automatically persisted,
- you must provide a sink if you want logs, metrics, or external forwarding.

See:

- **[Background tasks](../planner/background-tasks.md)**

### Optional MLflow tracing

Install `penguiflow[mlflow]` and use `penguiflow.observability.AgentTelemetry`
with `mlflow_enabled=True` to trace agent runs and tool calls. Set
`PENGUIFLOW_LLM_TRACING=mlflow` to enable provider-call spans for the built-in
native and LiteLLM clients. LLM spans contain metadata and provider-reported
token counts, not prompts or completions. See
**[MLflow tracing for existing agents](mlflow-existing-agents.md)**.

## Recommended capture strategy

If you want a practical, production-usable baseline:

1. Capture runtime `FlowEvent` in structured logs for every flow.
2. Capture planner `PlannerEvent` for every `ReactPlanner` instance.
3. Register LLM telemetry hooks if you care about provider latency, tokens, retries, or cost.
4. Attach a task telemetry sink if you use sessions or background tasks.
5. Persist planner events and task state only when you need replay, audit, or UI hydration.

## Reference integration

This is the closest thing to a "wire the whole system" reference pattern that exists in the repo today:

- runtime flows attach `log_flow_events(...)`,
- planners attach `event_callback=...`,
- native LLM telemetry uses `get_telemetry_hooks().register(...)`,
- session/background-task systems pass `telemetry_sink=...`.

You can see these patterns in:

- `examples/planner_enterprise_agent_v2/telemetry.py`
- generated orchestrator templates under `penguiflow/templates/new/*/src/__package_name__/orchestrator.py.jinja`

Minimal combined example:

```python
from __future__ import annotations

import logging

from penguiflow import create, log_flow_events
from penguiflow.llm.telemetry import get_telemetry_hooks
from penguiflow.planner import ReactPlanner
from penguiflow.sessions import SessionManager
from penguiflow.sessions.telemetry import LoggingTaskTelemetrySink


logger = logging.getLogger("penguiflow.app")
planner_logger = logging.getLogger("penguiflow.app.planner")


async def record_flow_event(event):
    logger.info(event.event_type, extra=event.to_payload())


def record_planner_event(event) -> None:
    planner_logger.info(event.event_type, extra=event.to_payload())


def record_llm_event(event) -> None:
    logger.info(
        "llm_event",
        extra={
            "event_type": event.event_type,
            "provider": event.provider,
            "model": event.model,
            "trace_id": event.trace_id,
            **(event.extra or {}),
        },
    )


hooks = get_telemetry_hooks()
hooks.register(record_llm_event)

flow = create(
    ...,
    middlewares=[
        log_flow_events(logging.getLogger("penguiflow.flow")),
        record_flow_event,
    ],
)

planner = ReactPlanner(
    ...,
    event_callback=record_planner_event,
)

session_manager = SessionManager(
    telemetry_sink=LoggingTaskTelemetrySink(logger=logger),
)
```

Notes:

- This example is intentionally integration-first: it matches the repo’s middleware/callback/sink model.
- If you only run flows, you do not need planner, LLM, or task telemetry.
- If you only run planners without session-backed tasks, you do not need `SessionManager`.

## Minimum recommended setups

### Runtime-only service

Use this when you are running plain flows and nodes without `ReactPlanner`.

Minimum:

1. attach `log_flow_events(...)`
2. emit structured logs
3. derive counters/histograms from `FlowEvent`

You do **not** need:

- `PlannerEvent`
- `LLMEvent`
- `TaskTelemetryEvent`

### Planner-based agent

Use this when you run `ReactPlanner` with tools but no background tasks.

Minimum:

1. keep the runtime `FlowEvent` capture
2. add `event_callback` for `PlannerEvent`
3. log finish reason, tool-call latency, pause/resume, and planner errors

You probably do **not** need:

- task telemetry

You may need:

- planner event persistence if you support replay/debugging or a UI that rehydrates execution history

### Planner + native LLM

Use this when you run the native `penguiflow.llm` layer and care about provider behavior.

Minimum:

1. runtime `FlowEvent`
2. planner `PlannerEvent`
3. `get_telemetry_hooks().register(...)` for `LLMEvent`

Capture at least:

- request latency
- retry count
- token usage
- cost when available

Do not assume planner telemetry alone is enough for provider-level troubleshooting.

### Planner + background tasks or sessions

Use this when you spawn subagents, tool jobs, or other session-backed async work.

Minimum:

1. runtime `FlowEvent`
2. planner `PlannerEvent`
3. `TaskTelemetrySink` via `telemetry_sink=...`

Capture at least:

- task spawn/completion/failure/cancellation
- session/task identifiers
- task-group outcomes if you use groups

This is the pattern already used by generated orchestrator templates that wire `LoggingTaskTelemetrySink(logger=_LOGGER)` into `SessionManager(...)`.

## Persistence matrix

Use this as the default decision table unless you have a stronger product requirement.

| Telemetry surface | Keep in logs | Derive metrics | Persist durably | Why |
|---|---|---|---|---|
| `FlowEvent` | yes | yes | sometimes | best for runtime health, alerts, and incident debugging; durable persistence is optional and app-specific |
| `PlannerEvent` | yes | sometimes | often when using replay/UI/debugging | best source for agent execution history, tool/LLM sequencing, and planner replay |
| `LLMEvent` | yes | yes | rarely | best for provider latency, retries, tokens, and cost; durable storage is usually unnecessary unless you have finance/audit requirements |
| `TaskTelemetryEvent` | yes | yes | only when task ops matter | best for background-task operations and concurrency support; durable storage depends on whether async task operations are part of your product |

Repo-grounded persistence hooks:

- runtime audit history uses `StateStore.save_event(...)` / `load_history(...)`
- planner event persistence uses `SupportsPlannerEvents.save_planner_event(...)`
- task/session durability uses `SupportsTasks.save_task(...)` and `save_update(...)`
- LLM telemetry has callback hooks, but no built-in durable store

Practical default:

- send `FlowEvent`, `PlannerEvent`, and `TaskTelemetryEvent` to logs first,
- derive metrics from `FlowEvent`, `LLMEvent`, and task events,
- persist `PlannerEvent` only when you need trace replay, UI hydration, or deep debugging,
- persist task state only when background execution is a first-class operational concern.

## Choosing the right surface

- Use `FlowEvent` for system health, node failures, queue depth, retries, cancellation, and alerts.
- Use `PlannerEvent` for agent reasoning flow, tool execution, pause/resume, and UI/event replay.
- Use `LLMEvent` for provider latency, retries, tokens, and cost.
- Use `TaskTelemetryEvent` for background concurrency, task lifecycle, and async operational support.

## Common mistakes

- Treating `trace_id` as a metric label.
- Assuming planner telemetry replaces runtime telemetry.
- Assuming LLM hooks are enabled and exported automatically.
- Assuming background-task telemetry is persisted without a custom sink.
- Logging raw prompts, tool payloads, or secret-bearing context objects.

## Security / multi-tenancy notes

- Treat logs, event stores, and replay data as sensitive.
- Keep tenant boundaries explicit in envelopes, task contexts, and storage scopes.
- Avoid raw prompt/tool payload logging unless you have explicit retention and redaction controls.
- Treat `tool_context` and provider config as privileged inputs.

## Where to go next

- Need runtime log wiring: **[Logging](logging.md)**
- Need runtime dashboards and alerts: **[Metrics & alerts](metrics-and-alerts.md)**
- Need runtime production patterns: **[Telemetry patterns](telemetry-patterns.md)**
- Need planner-specific event guidance: **[Planner observability](../planner/observability.md)**
