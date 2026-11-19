---
name: penguiflow-streaming
description: Stream partial output (LLM answer tokens, tool progress, status updates) from a ReactPlanner to clients — enable `stream_final_response=True`, consume `PlannerEvent`s (`llm_stream_chunk` with `extra["channel"]`, `stream_chunk` from tools via `ctx.emit_chunk(stream_id, seq, text, ...)`, `artifact_chunk`, `step_*`/`tool_call_*`) through `event_callback`, bridge them to SSE or WebSocket with a per-request queue, or use `StreamingSession` + `PlannerTaskPipeline` + `session.subscribe()` for task-addressable `StateUpdate`s. Use when a user says "stream tokens", "SSE streaming", "WebSocket streaming", "progressive output", "stream_chunk", "llm_stream_chunk", or asks how to stream planner partials to a client.
---

# PenguiFlow Streaming (Planner Events)

## When to use
- Streaming the planner's final answer token-by-token to a UI.
- Streaming progress from inside a long-running tool (`ctx.emit_chunk`).
- Building an SSE or WebSocket bridge from planner events to a client.
- Showing status ("searching…", "calling tool foo") alongside content.

## When NOT to use
- Choosing an LLM client that supports streaming, or `JSONLLMClient.stream`/`on_stream_chunk` protocol details → see [[penguiflow-reactplanner-config]] step 7a.
- Streaming to another agent over A2A (`message/stream`) → see [[penguiflow-a2a-integration]]; `A2AService` forwards answer chunks for you.
- Persisting updates for replay → use [[penguiflow-statestore]] / session persistence.

## Hard boundaries
This skill is the **event-to-client layer**: which planner events carry partial output, how to route them per request, and how to put them on the wire. Planner LLM/streaming configuration lives in reactplanner-config; UI event contracts are your app's choice.

## Workflow

### 1) Turn on final-answer streaming
```python
planner = ReactPlanner(
    llm_client=...,             # must support callback streaming (native adapter, LiteLLM client, fallback wrappers)
    catalog=catalog,
    stream_final_response=True, # stream the synthesized answer
    event_callback=on_event,    # sync Callable[[PlannerEvent], None]
)
```
Intermediate planning steps stay non-streamed JSON; only the final answer (and thinking, when the model emits it) streams.

### 2) Know which events carry partial output
| `event_type` | Source | Key `extra` fields |
|---|---|---|
| `llm_stream_chunk` | planner LLM streaming | `text`, `done`, `channel` (`"answer"` / `"thinking"`), `phase`, `action_seq` |
| `stream_chunk` | a tool calling `await ctx.emit_chunk(stream_id, seq, text, done=..., meta=...)` | `stream_id`, `seq`, `text`, `done`, `meta` (`meta["channel"]` defaults to `"thinking"`) |
| `artifact_chunk` | a tool calling `await ctx.emit_artifact(stream_id, chunk, ...)` | partial structured artifact (e.g. chart config) |
| `step_start`, `tool_call_start`, `tool_call_end`, `tool_call_result`, `finish` | planner loop | status for progress UIs |

`event.to_payload()` returns a flat JSON-friendly dict (`event`, `ts`, `step`, plus `extra`) — the natural wire format. Details in `references/streamchunk-and-emit.md`.

### 3) Route events per request
`event_callback` is set **per planner instance** and is synchronous. For concurrent requests either:
- build one planner per request (a factory), giving each its own callback, or
- use `StreamingSession` + `PlannerTaskPipeline(planner_factory=...)` (step 5), which does this for you.

In the callback, never block: `queue.put_nowait(event)` into a per-request `asyncio.Queue` and let the HTTP/WS handler drain it.

### 4) Ship to the client (SSE / WebSocket)
```python
queue: asyncio.Queue[PlannerEvent | None] = asyncio.Queue()
planner = make_planner(event_callback=queue.put_nowait)

async def gen():
    task = asyncio.create_task(planner.run(query, tool_context={"session_id": sid}))
    task.add_done_callback(lambda _t: queue.put_nowait(None))
    while (event := await queue.get()) is not None:
        if event.event_type == "llm_stream_chunk" and event.extra.get("channel") == "answer":
            yield sse("chunk", event.extra["text"])
    result = await task
    yield sse("final", json.dumps({"payload": result.payload}, default=str))
```
`sse()` is a three-line local helper (no library helper ships for it). Full SSE and WebSocket recipes in `references/sse-and-websocket.md`.

### 5) Or use sessions for task-addressable updates
`StreamingSession` runs the planner as a task, projects every planner event into a `StateUpdate` (`RESULT` for answer chunks, `THINKING` for other channels, `TOOL_CALL`, `PROGRESS`, `STATUS_CHANGE`, `CHECKPOINT`, `ERROR`), and lets clients `subscribe()` with filters and replay. Wire a bidirectional client with `session.connect(transport)`. See `references/sse-and-websocket.md` and [[penguiflow-background-tasks]].

### 6) Treat the final result as canonical
Chunks are previews. The `PlannerFinish` payload is the answer. If the planner restarts its answer (a new `action_seq`, e.g. after a finish repair), reset the displayed text; `PlannerFinish.metadata["answer_action_seq"]` identifies the sequence that produced the final answer.

## Troubleshooting (fast checks)
- **No `llm_stream_chunk` events** — `stream_final_response` is False, the LLM client doesn't support callback streaming, or no `event_callback` is set (chunks are only emitted when a callback exists).
- **Answer text duplicated in the UI** — the planner re-streamed after a repair; reset on a new `action_seq`.
- **Chunks from one user appear in another user's stream** — one planner instance shared across requests with a single callback; use a planner per request or sessions.
- **Event loop stalls while streaming** — the callback does blocking work; only `put_nowait` in the callback.
- **Tool chunks show up as "thinking"** — `ctx.emit_chunk` defaults `meta["channel"]` to `"thinking"`; pass `meta={"channel": "answer"}` for user-facing text.
- **Session subscribers miss chunks** — the per-subscriber queue (`SessionLimits.update_queue_size`, default 500) overflowed; non-critical updates are dropped first. Drain faster or raise the limit.
- **Stream keeps going after cancel** — cancel through steering (`SteeringEventType.CANCEL` / `session.cancel_task`), not by closing the socket only.

## Worked examples
- `examples/streaming_llm/stream_answer.py` — tool-level `ctx.emit_chunk` surfaced through `PlannerEvent` callbacks.
- `examples/planner_enterprise_agent_v2/` — status updates and streaming in a larger agent (see its `STREAMING.md`).

## References (load only as needed)
- `references/streamchunk-and-emit.md` — planner stream events, `ctx.emit_chunk` / `ctx.emit_artifact` semantics, ordering, channels.
- `references/sse-and-websocket.md` — SSE/WebSocket bridge recipes for planner events and for `StreamingSession` updates.
- `references/status-and-progressive-ui.md` — status + content + final, time-to-first-chunk tuning, backpressure, cancellation, mid-stream errors.
