# `PlannerEvent` Catalog and Structured Logging

## `configure_logging(...)`

```python
from penguiflow import configure_logging

configure_logging(
    level="INFO",            # standard logging level
    structured=True,         # JSON lines; False = human-readable
    include_extras=True,     # when structured=False, append extra fields
    logger_name="penguiflow",
)
```

Configures the `penguiflow` logger and its children. Useful sub-loggers:
- `penguiflow.planner` — every `PlannerEvent` (`llm_stream_chunk` at DEBUG, others at INFO), plus planner warnings (`deadline_exhausted`, `event_callback_error`, ...).
- `penguiflow.llm.telemetry`, `penguiflow.llm.tracing` — LLM call telemetry and spans.
- `penguiflow.tools.node` — ToolNode / MCP / UTCP connections and calls.
- `penguiflow_a2a` — the A2A server (failed planner runs are logged with `task_id`).

Call once at process startup. Re-calling resets handlers.

## `PlannerEvent`

```python
@dataclass(frozen=True, slots=True)
class PlannerEvent:
    event_type: str
    ts: float
    trajectory_step: int
    thought: str | None = None
    node_name: str | None = None
    latency_ms: float | None = None
    token_estimate: int | None = None
    error: str | None = None
    extra: Mapping[str, Any] = {}
```

`event.to_payload()` returns `{"event": event_type, "ts", "step", ...optional fields, **extra}` (reserved `logging` keys are filtered out of `extra`). That is what the planner logs and what you should ship to log pipelines.

Receive events with `ReactPlanner(..., event_callback=fn)`, where `fn(event)` is synchronous. Exceptions in the callback are logged (`event_callback_error`) and swallowed.

## Event type catalog

### Loop lifecycle
| `event_type` | When | Useful fields |
|---|---|---|
| `step_start` | a planning step begins | `extra["action_seq"]` |
| `step_complete` | a tool step finished | `node_name` (tool), `latency_ms`, `thought` |
| `finish` | the run finished | `extra["reason"]`, `extra["cost"]`, `extra["error"]`, `thought` |
| `resume` | a paused run resumed | `extra["user_input"]` |
| `steering_received` | a steering event was consumed | `extra["event_type"]`, `extra["source"]` |

Pauses don't emit an event — `planner.run()` returns a `PlannerPause`.

### Tool calls
| `event_type` | Useful fields |
|---|---|
| `tool_call_start` | `tool_name`, `tool_call_id`, `args_json`, `action_seq` |
| `tool_call_end` | `tool_name`, `tool_call_id`, `action_seq` |
| `tool_call_result` | `tool_name`, `tool_call_id`, `result_json` |
| `tool_activated`, `tool_activation_denied`, `tool_search_query`, `tool_get` | tool discovery |
| `observation_clamped` | an observation exceeded its size guardrail |

### Quality and repair
`planner_args_invalid` (`extra["tool"]`, `extra["error"]`), `planner_args_suspect`, `planner_repair_attempt`, `arg_fill_failure`, `finish_repair_success` / `finish_repair_failure`, `graceful_failure_attempt` / `graceful_failure_success`, `multi_action_fallback_used`, `reflection_critique`, `guardrail_retry`.

### Streaming
`llm_stream_chunk`, `stream_chunk`, `artifact_chunk` — see [[penguiflow-streaming]].

### Context and skills
`llm_before`, `llm_context_hook_applied` / `llm_context_hook_failed`, `skills_retrieved`, `skill_pack_loaded`, `skill_directory_rendered`, `tool_directory_rendered`, `tool_hints_generated`.

Grep the planner for `event_type="` when you need the exact payload of a rarer event.

## Reducing volume
- Keep `penguiflow.planner` at INFO in staging; in production raise it to WARNING and rely on `event_callback` metrics plus sampled logs.
- `llm_stream_chunk` is already DEBUG.
- Drop `tool_call_start.args_json` / `tool_call_result.result_json` from shipped logs if they can be large or sensitive.

## Run-level record

The returned `PlannerFinish.metadata` is the best single record of a run: `steps` (trajectory history with actions, observations, errors, streams), `step_count`, `cost`, `sources`, `artifacts`, `trajectory_metadata`, and repair counters (`validation_failures_count`, `repair_attempts`, `finish_repair_success_count`, ...). Log a summary of it (not the whole thing) per run.

## Trace correlation
- Put a request/trace id in `tool_context` (e.g. `trace_id`, `session_id`) — sessions do this for you (`tool_context["trace_id"]`, `["task_id"]`).
- Add it to your log records with a `logging.Filter` or `LoggerAdapter` in the code that calls `planner.run()`.
- Never use it as a metric label.

## Redaction

PenguiFlow does **not** redact secrets. Common leak vectors:
- `tool_call_start.extra["args_json"]` and `tool_call_result.extra["result_json"]`.
- Exception messages from tools (surface in step errors).
- `llm_context` (sent to the LLM *and* returned in `PlannerFinish.metadata["llm_context"]`).

```python
SECRET_KEYS = {"authorization", "api_key", "token", "cookie", "password"}

def redact(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in SECRET_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value

def on_event(event):
    log.info(event.event_type, extra=redact(event.to_payload()))
```

At the boundary: keep secrets in `tool_context` (never shown to the LLM), and sanitize exception messages raised by tools.

## Operational defaults
- `configure_logging(structured=True)` in production.
- One metrics `event_callback` per planner (or per planner factory).
- Tag logs with `service`, `env`, `worker_id`.
- Index logs by your trace/session id.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| No planner events | logging not configured or level too high | `configure_logging(...)`; check `penguiflow.planner` level |
| Duplicate logs | root handler + `configure_logging` | one entrypoint per process |
| Callback metrics missing | callback replaced per request on a shared planner | process-wide callback or planner factory |
| Event loop stalls | blocking I/O in `event_callback` | enqueue and export asynchronously |
| Secrets in logs | args/results logged verbatim | redact in the callback / logging filter |
