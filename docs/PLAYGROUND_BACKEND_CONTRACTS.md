# Playground Backend Contracts

This document describes the backend contracts that power the PenguiFlow Playground UI, so new agents and new UI features can integrate without guesswork.

Scope:
- The FastAPI backend in `penguiflow/cli/playground.py`
- The wrapper layer in `penguiflow/cli/playground_wrapper.py`
- The SSE framing in `penguiflow/cli/playground_sse.py`

For build/release mechanics of the UI bundle, see `docs/PLAYGROUND_DEV.md`.

---

## Core Concepts

### Two contexts, two responsibilities

The Playground separates:

- `llm_context`: data that is safe to be LLM-visible (serialized into prompts).
- `tool_context`: runtime-only data (tenant/user/session IDs, DB handles, caches, trace IDs, etc.).

The UI exposes a **Setup** tab to populate:
- `session_id` (scopes trajectories + STM)
- `tenant_id`, `user_id` (multi-tenant keying for STM and orchestrators)
- arbitrary JSON `tool_context`
- optional JSON `llm_context` (used for planner wrappers)

### What the backend wraps

The Playground can run either:

1. A project “orchestrator” class (preferred when present), or
2. A “planner builder” function (fallback when no orchestrator is found).

The wrapper layer normalizes both into the same `AgentWrapper` interface.

---

## Discovery Contract

Entry point: `discover_agent(project_root)` in `penguiflow/cli/playground.py`.

Discovery rules:
- The backend adds `project_root/src` (if present) to `sys.path`.
- It scans packages under `src/` (or the root) by checking for `__init__.py`.
- It attempts to import these modules (if present):
  - `{package}.orchestrator`
  - `{package}.planner`
  - `{package}.__main__`
  - `{package}.__init__`

Preference order:
1. First discovered orchestrator class
2. First discovered planner builder function

Config loading:
- If `{package}.config.Config.from_env()` exists, it is used.
- Otherwise, the backend attempts `Config()` if possible.

---

## Wrapper Contract (How Your Agent Should Look)

The backend wraps one of the following.

### Option A: Orchestrator

Your package exports a class with an `execute(...)` coroutine:

```python
class MyOrchestrator:
    async def execute(self, query: str, *, tenant_id: str, user_id: str, session_id: str):
        ...
```

Response shape:
- The wrapper reads:
  - `answer` (string or object with common keys like `raw_answer`)
  - `trace_id` (string, recommended)
  - `metadata` (mapping, optional)

Best practices:
- Store the internal planner on `self._planner` if you want the Playground to:
  - stream planner events (via planner callbacks)
  - recover the active trace id from the planner
- Implement an async `stop()` method for cleanup; the wrapper calls it on shutdown if present.

Tenant/user/session:
- Orchestrators should treat `tenant_id/user_id/session_id` as trusted runtime identifiers.
- Do not fetch them from `llm_context`.

### Option B: Planner builder (`build_planner`)

Your package exports a function that returns either:
- a `ReactPlanner`, or
- a bundle with a `.planner` attribute.

Example:

```python
def build_planner(config) -> ReactPlanner:
    return ReactPlanner(...)
```

In this mode:
- `llm_context` is passed to `planner.run(query=..., llm_context=...)`.
- `tool_context` is passed to `planner.run(..., tool_context=...)`.

The wrapper always injects:
- `session_id`
- `trace_id`

into `tool_context` for traceability and STM keying.

---

## HTTP API

### `GET /`

Serves the compiled UI (if `penguiflow/cli/playground_ui/dist/` exists).

### `GET /health`

Returns `{ "status": "ok" }`.

### `GET /ui/spec`

Returns the discovered spec (if any) from:
- `agent.yaml` / `agent.yml` / `spec.yaml` / `spec.yml`

Payload (shape):
- `content: str`
- `valid: bool`
- `errors: list[... ]`
- `path: str | None`

### `POST /ui/validate`

Validates a YAML spec without generating files.

Request:
```json
{ "spec_text": "..." }
```

Response: `SpecPayload` (same as `/ui/spec`).

### `POST /ui/generate`

Runs a **dry-run** generation for the provided spec text (used to preview the output paths).

Request:
```json
{ "spec_text": "..." }
```

Response:
```json
{ "success": true, "created": [...], "skipped": [...], "errors": [...] }
```

### `GET /ui/meta`

Returns metadata used by the UI’s left column:
- agent name/description/template/flags
- planner settings summary
- service flags (memory_iceberg/lighthouse/wayfinder)
- tool catalog listing (name/description/tags)

### `POST /chat`

Non-streaming chat endpoint.

Request:
```json
{
  "query": "hello",
  "session_id": "optional",
  "llm_context": {},
  "tool_context": {},
  "context": {}  // deprecated alias for llm_context
}
```

Response:
```json
{
  "trace_id": "abc123",
  "session_id": "sess-1",
  "answer": "…",
  "metadata": { ... },
  "pause": { ... }  // present when paused
}
```

### `GET /chat/stream` (SSE)

Streaming chat endpoint that emits SSE events as the planner runs.

Query parameters:
- `query`: required
- `session_id`: optional (generated if missing)
- `tool_context`: optional JSON string (object)
- `llm_context`: optional JSON string (object)
- `context`: optional JSON string (deprecated alias for llm_context)

SSE event types:
- `chunk` (planner stream chunks)
- `llm_stream_chunk` (LLM token streaming)
- `artifact_chunk` (structured artifacts emitted during execution)
- `artifact_stored` (artifact stored with metadata)
- `resource_updated` (MCP resource cache invalidation)
- `step` (step_start / step_complete)
- `event` (all other planner events)
- `done` (final answer/pause envelope)
- `error` (terminal errors)

### `POST /agui/agent` (AG-UI)

AG-UI streaming endpoint using the official `ag-ui-protocol` encoder. This runs the same agent
wrapper as `/chat/stream` but emits AG-UI events instead of legacy SSE frames.

Request body (AG-UI `RunAgentInput`):
```json
{
  "threadId": "session-id",
  "runId": "trace-id",
  "messages": [ ... ],
  "tools": [],
  "state": {},
  "forwardedProps": {
    "penguiflow": {
      "llm_context": { ... },
      "tool_context": { ... }
    }
  }
}
```

Notes:
- `threadId` maps to PenguiFlow `session_id`.
- `runId` maps to PenguiFlow `trace_id`.
- `forwardedProps.penguiflow.llm_context/tool_context` preserves the context split.
- The Python SDK accepts snake_case aliases (`thread_id`, `run_id`, `forwarded_props`) if needed.

Standard AG-UI events:
- `RUN_STARTED` / `RUN_FINISHED` / `RUN_ERROR`
- `STEP_STARTED` / `STEP_FINISHED`
- `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END`
- `TOOL_CALL_START` / `TOOL_CALL_ARGS` / `TOOL_CALL_END` / `TOOL_CALL_RESULT`

Custom AG-UI events:
- `name="artifact_stored"` with `{ artifact, download_url }`
- `name="resource_updated"` with `{ namespace, uri, read_url }`
- `name="pause"` with the pause payload (HITL / OAuth flow)

### `GET /events` (SSE replay + follow)

Replays stored events for a `trace_id`, optionally in follow mode.

Query parameters:
- `trace_id`: required
- `session_id`: optional (used to validate the trace belongs to a session if provided)
- `follow`: `true|false` (subscribe to new events)

### `GET /trajectory/{trace_id}?session_id=...`

Returns the reconstructed trajectory (if stored).

---

## SSE Payload Shapes

The SSE encoder is `format_sse(event, data)` in `penguiflow/cli/playground_sse.py`.

Common fields (most events):
- `trace_id`
- `session_id`
- `ts` (timestamp)
- `step` (trajectory step index)

### `event: chunk`

Used for planner-level stream chunks:
- `stream_id`, `seq`, `text`, `done`, `meta`, `phase`

Notes:
- `phase` defaults to `"observation"` in the playground, so tool/progress streaming does not concatenate into the final answer bubble.

### `event: llm_stream_chunk`

Used for LLM streaming:
- `text`, `done`, `phase`

Known phases:
- `action`: LLM is selecting the next tool/action (UI renders typing dots).
- `answer`: streaming final answer text (UI renders in the main bubble).
- `revision`: streaming revised answer text (UI replaces the current answer).

### `event: artifact_chunk`

Used for artifacts:
- `stream_id`, `seq`, `chunk`, `done`, `artifact_type`, `meta`

### `event: step`

Used for step boundaries:
- `event`: `"step_start" | "step_complete"`
- `node`: node name
- `latency_ms`, `token_estimate`, `thought`, plus any extra fields

### `event: done`

Terminal envelope:
- `trace_id`, `session_id`
- `answer`
- `metadata`
- `pause` (when paused)

---

## Extending the Playground Safely

When adding UI features:
- Prefer adding new fields to `tool_context` (runtime-only) rather than `llm_context`.
- Keep JSON payloads small and stable; the UI stores recent event lists and artifacts in memory.
- Maintain backward compatibility:
  - keep accepting `context` as an alias for `llm_context`
  - new fields should be optional

When adding backend fields:
- Add them to the relevant response models in `penguiflow/cli/playground.py`
- Update the UI parser in `penguiflow/cli/playground_ui/src/App.svelte`
- Rebuild UI assets (`npm run build` in `penguiflow/cli/playground_ui/`) before packaging.
