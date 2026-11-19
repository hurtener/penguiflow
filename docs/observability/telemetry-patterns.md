# Telemetry patterns

## What it is / when to use it

This page collects **production patterns** for observing PenguiFlow agents, with a bias toward:

- incident debugging ("why is this stuck/slow/failing?"),
- multi-tenant safety (what not to log/store),
- building dashboards and alerts from planner events.

If you only set up one thing: capture `PlannerEvent` through `event_callback` and correlate by `trace_id` / `session_id`.

Start with **[Observability overview](overview.md)** if you need the full telemetry model across planner, LLM, and task/session layers.

## Non-goals / boundaries

- This page does not mandate a telemetry vendor or SDK.
- This is not a complete spec for every event; it documents practical patterns.

## Contract surface

### The canonical event primitive: `PlannerEvent`

The planner emits `PlannerEvent` for steps (`step_start`, `step_complete`), tool calls (`tool_call_start`, `tool_call_end`, `tool_call_result`), repairs and guardrails (`planner_args_invalid`, `planner_repair_attempt`, `guardrail_retry`), steering (`steering_received`, `resume`), streaming (`llm_stream_chunk`, `artifact_chunk`) and completion (`finish`).

`PlannerEvent.to_payload()` returns a flat dict for structured logs.

### Correlation keys

- `trace_id` and `session_id` correlate requests, streamed chunks and final answers.
- **logs**: include them,
- **metrics**: do not tag by them (cardinality explosion).

### Tenant boundary

Pass `tenant` in `tool_context`. It should be present in logs and (low-cardinality) metric tags.

## Operational defaults (recommended)

- Emit/record `trace_id`, `session_id`, `tenant`, `event_type`, `node_name`, `latency_ms`, and the finish reason.
- Prefer structured logs (JSON) and callback-driven capture (avoid ad-hoc prints).
- Persist planner events via a `StateStore` that supports planner events when you need audit/replay without log access.

## Patterns (concrete)

### Pattern: capture planner events centrally

- Pass one `event_callback` to every `ReactPlanner` that forwards events to logs and your metric pipeline.
- When serving over A2A, `A2AService` chains your callback; it does not replace it.

See **[Logging](logging.md)**.

### Pattern: spot slow or stuck runs

- long gaps between `step_start` and `step_complete` → slow tool or LLM call,
- many steps without `finish` → the planner is looping (tighten tool descriptions / budgets),
- `steering_received` without progress → check cancellation and pause handling.

### Pattern: classify failures by category

- tool failures: `tool_call_result` with an error / tool exceptions,
- validation and repair: `planner_args_invalid`, `planner_repair_attempt`, `finish_repair_failure`,
- outcome: `finish` with `no_path` or `budget_exhausted`.

This maps to remediation playbooks:

- tool failures → fix the dependency, timeouts, or retries in `ToolNode` config,
- validation/repair → improve schemas and tool descriptions,
- no path / budget → revisit budgets and the tool catalog.

### Pattern: protect sensitive payloads

- do not log raw tool payloads or `tool_context`,
- do not store raw model prompts/responses unless you have explicit retention and redaction,
- log hashes, ids and summaries; store large content as artifacts and log references.

## Failure modes & recovery

- **You can't debug without reading code**: you're missing planner events or correlation ids. Fix: structured logging + `event_callback` + `tenant`/`session_id` in `tool_context`.
- **Telemetry is too expensive**: `llm_stream_chunk` logged at INFO. Fix: keep it at DEBUG or sample.
- **Metrics are unusable**: you tagged metrics with `trace_id` or `session_id`. Fix: keep correlation in logs only.

## Observability (incident debugging flow)

1. Find the request's `trace_id` / `session_id` (ingress log).
2. Filter logs by it: look at the last `step_complete`, failing `tool_call_result`, and the `finish` reason.
3. Check tool latency histograms for the slow tool and LLM telemetry for provider issues.
4. Decide remediation: tighten timeouts, adjust budgets, or gate a problematic tool.

## Security / multi-tenancy notes

- Treat logs and event stores as sensitive; assume broad internal access.
- Keep tenant boundaries explicit and avoid cross-tenant sessions.

## Runnable examples

```bash
uv run python examples/streaming_llm/stream_answer.py
uv run python examples/planner_enterprise_agent_v2/main.py
```

## Troubleshooting checklist

- Need the cross-layer model: see **[Observability overview](overview.md)**.
- Need log setup: see **[Logging](logging.md)**.
- Need alert recommendations: see **[Metrics & alerts](metrics-and-alerts.md)**.
