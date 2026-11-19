---
name: penguiflow-streaming
description: Stream partial output (LLM tokens, progressive synthesis, status updates) from a PenguiFlow flow — emit `StreamChunk` messages via `ctx.emit_chunk(parent=msg, text=..., done=..., to=sink)` from a "compose" node, route them to a dedicated sink node, and ship them to clients via SSE (`format_sse_event`), WebSocket (`chunk_to_ws_json`), or programmatic iteration (`stream_flow`/`emit_stream_events`). Use when a user says "stream tokens", "SSE streaming", "WebSocket streaming", "progressive output", "StreamChunk", or asks how to stream partials to a client.
---

# PenguiFlow Streaming (Protocol-Agnostic)

## When to use
- Streaming raw tokens or progressive synthesis from a flow.
- Building an SSE or WebSocket bridge from a flow's stream sink to a client.
- Needing chunk ordering and `done`-flag semantics without committing to a specific UI event protocol.
- Streaming status updates ("downloading…", "synthesizing…") alongside content.

## When NOT to use
- ReactPlanner final-response streaming knob (`stream_final_response`) and `JSONLLMClient.stream`/`on_stream_chunk` LLM-protocol callbacks → see [[penguiflow-reactplanner-config]] step 7a.
- Persisting chunks for replay → use [[penguiflow-statestore]] event persistence.

## Hard boundaries
This skill is the **runtime chunk layer**: `StreamChunk`, `emit_chunk`, sink nodes, and the protocol-agnostic SSE/WebSocket helpers PenguiFlow ships. Higher-level UI event contracts are your app's choice; planner-internal streaming knobs are a sibling skill.

## Workflow

### 1) Switch the flow to envelope style
Streaming requires `Message` envelopes — payload-only flows have no `trace_id` to anchor `stream_id`. See [[penguiflow-core-flows]] (`references/messages-and-envelopes.md`).

```python
msg = Message(payload={"request": ...}, headers=Headers(tenant="acme"))
await flow.emit(msg, trace_id=msg.trace_id)
```

### 2) Design the topology: compose node + chunk sink + final node
```
compose_node --(StreamChunk)--> chunk_sink_node
            \--(FinalAnswer)--> final_node
```

Both successors live in adjacency:
```python
flow = create(
    compose.to(chunk_sink, final),
    chunk_sink.to(),
    final.to(),
)
```

Why a dedicated sink: chunks need their own queue and consumer. Don't rely on side effects in the compose node.

### 3) Emit chunks with `ctx.emit_chunk`
```python
async def compose(msg: Message, ctx) -> None:
    await ctx.emit_chunk(parent=msg, text="hello ", to=chunk_sink_node)
    await ctx.emit_chunk(parent=msg, text="world", done=True, to=chunk_sink_node)

    final = msg.model_copy(update={"payload": FinalAnswer(text="hello world")})
    await ctx.emit(final, to=final_node)
```

Rules:
- `parent` is required — chunks inherit `trace_id`, headers, deadline, and meta.
- Default `stream_id` is the parent's `trace_id`. Override only when you have multiple concurrent streams in the same trace (rare).
- `seq` is auto-assigned and monotonically increasing per `stream_id`. Don't supply it manually unless you really need to.
- `done=True` marks the end of the stream and resets internal sequence tracking. Emit it exactly once per stream.

### 4) Sink shape
```python
async def chunk_sink(msg: Message, _ctx) -> None:
    chunk: StreamChunk = msg.payload
    # forward to client (SSE, WebSocket, queue, etc.)
    await client.send(chunk.text)
    if chunk.done:
        await client.send_done()
```

The sink usually returns `None` (no further routing). It's the bridge between the flow and your transport.

### 5) Ship chunks to a client

Three first-class helpers in `penguiflow.streaming`:

| Helper | Use for |
|---|---|
| `format_sse_event(chunk)` | Format a `StreamChunk` as an SSE `data: ...` frame. |
| `chunk_to_ws_json(chunk)` | Serialize a `StreamChunk` to a JSON dict for WebSocket `send_json`. |
| `stream_flow(flow, trace_id, ...)` | Async iterator yielding chunks for a trace — useful for tests and HTTP handlers that want to consume programmatically. |
| `emit_stream_events(...)` | Lower-level helper to bridge sink output to async generators. |

Full SSE / WebSocket recipes in `references/sse-and-websocket.md`.

### 6) Emit a `FinalAnswer` to terminate the trace
Streaming chunks are progressive. Callers still need a final, canonical result. Emit `FinalAnswer(text=..., citations=...)` (or another typed payload) to a separate egress node so `fetch(trace_id=...)` returns the canonical answer.

Don't conflate the chunk sink with the final result. Two egress paths is the safe pattern.

## Troubleshooting (fast checks)
- **No chunks appear** — the compose node received a payload-only message; switch to `Message` envelopes. Or the sink isn't connected in `create(...)`.
- **Chunks come out of order** — multiple producers emit to the same `stream_id` concurrently; one producer per stream, or use distinct `stream_id`s per producer.
- **Duplicate `seq`** — you're supplying `seq` manually and re-emitting; let `emit_chunk` assign it.
- **Stream never ends** — no chunk emitted with `done=True`; emit one terminator per stream.
- **Streaming stalls** — chunk queue is full (backpressure working); make the sink faster, or raise `queue_maxsize` for the chunk edge.
- **Chunks leak across tenants** — same `trace_id` reused for different tenants; always generate fresh `trace_id` per request and set `Headers.tenant`.
- **`fetch(trace_id=...)` hangs** — chunks reach the sink but no `FinalAnswer` reaches Rookery; ensure the compose node emits a final to the `final_node`.
- **Chunks survive cancel** — `flow.cancel(trace_id)` drops queued messages but in-flight tool tasks can still emit; check the cancellation event before emitting.

## Worked examples
- `examples/streaming_llm/flow.py` — token streaming with SSE bridge.
- `examples/roadmap_status_updates/flow.py` — status + chunks + final answer.
- `examples/roadmap_status_updates_subflows/flow.py` — same pattern with subflow decomposition.

## References (load only as needed)
- `references/streamchunk-and-emit.md` — `StreamChunk` schema, `ctx.emit_chunk(...)` semantics, ordering rules, multi-stream patterns.
- `references/sse-and-websocket.md` — `format_sse_event`, `chunk_to_ws_json`, `stream_flow`, `emit_stream_events`, FastAPI bridge recipes.
- `references/status-and-progressive-ui.md` — status updates + content chunks + final, time-to-first-chunk tuning, backpressure for chunk edges.
