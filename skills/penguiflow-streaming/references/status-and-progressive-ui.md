# Status, Progressive UI, and Backpressure

## The three-channel pattern

Most production streaming UIs need three logical channels, and the planner already produces all three:

| Channel | Planner source |
|---|---|
| **Status** ("searching…", "calling tool foo") | `step_start`, `tool_call_start` / `tool_call_end` events (`extra["tool_name"]`) |
| **Content** (answer tokens) | `llm_stream_chunk` with `extra["channel"] == "answer"`, and tool `stream_chunk`s with `meta={"channel": "answer"}` |
| **Final** (canonical result) | the `PlannerFinish` returned by `planner.run()` (also a `finish` event) |

Map them to your wire format in the bridge (see `sse-and-websocket.md`):

```python
def classify(event: PlannerEvent) -> tuple[str, str] | None:
    extra = event.extra
    if event.event_type == "llm_stream_chunk":
        return ("content" if extra.get("channel") == "answer" else "thinking", extra.get("text", ""))
    if event.event_type == "stream_chunk":
        channel = (extra.get("meta") or {}).get("channel", "thinking")
        return ("content" if channel == "answer" else "thinking", extra.get("text", ""))
    if event.event_type == "tool_call_start":
        return ("status", f"calling {extra.get('tool_name')}…")
    return None
```

With sessions, the same split comes from `StateUpdate.update_type`: `RESULT` (content and final), `THINKING`, `TOOL_CALL`, `PROGRESS`, `STATUS_CHANGE`.

## Time-to-first-chunk (TTFC) tuning

User-perceived latency is dominated by TTFC, not total response time.

### Emit status early
Send a status frame as soon as the request is accepted — before `planner.run()` makes its first LLM call — and on every `tool_call_start`.

### Stream from slow tools
A tool taking more than ~500 ms should stream progress with `ctx.emit_chunk(...)` (channel `"thinking"` for progress, `"answer"` for user-facing text) so the UI never looks frozen.

### Enable final-answer streaming
`stream_final_response=True` with a streaming-capable client lets the answer appear while it is generated instead of after the last step.

### Reuse LLM client connections
Connection setup (TLS, auth) often dominates TTFC. Build clients once and share them across planner instances.

## Backpressure

The planner's `event_callback` is synchronous and must not block, so backpressure lives in your bridge:

- **Per-request queue** — an unbounded `asyncio.Queue` is fine for one chat turn; for very long runs, bound it and drop or coalesce `thinking` chunks when full (never drop the final event).
- **Sessions** — each subscriber gets a bounded queue (`SessionLimits(update_queue_size=500)` by default). When full, non-critical updates are dropped; critical ones (`RESULT`, `ERROR`, `NOTIFICATION`, `STATUS_CHANGE`) evict the oldest item instead. Raise the limit for chunk-heavy UIs.

### Coalesce chunks
If the producer is much faster than the network, coalesce small deltas before writing:

```python
buffer: list[str] = []

def flush() -> str:
    text = "".join(buffer)
    buffer.clear()
    return text

# in the drain loop
buffer.append(text)
if sum(map(len, buffer)) >= 32:
    yield sse("chunk", flush())
```

Trade-off: TTFC suffers slightly; throughput improves.

## Status taxonomy

Settle on a status vocabulary so frontend and backend agree:

| Kind | When |
|---|---|
| `status` | Progress hints ("searching…", "calling tool foo") |
| `content` | The answer |
| `thinking` | Reasoning trace (if exposed to users) |
| `tool_call` | Tool start/end details (often surfaced separately) |
| `citation` | Sources (from the final payload's `sources`) |

If you change it, version your stream protocol.

## Cancellation and streaming

When a client disconnects or presses stop:
1. Detect it in the handler (`asyncio.CancelledError` / `ClientDisconnect` for SSE, `WebSocketDisconnect` for WebSockets).
2. Stop the run cooperatively: push `SteeringEvent(event_type=SteeringEventType.CANCEL, ...)` into the `SteeringInbox` you passed to `planner.run(steering=...)`, or call `session.cancel_task(task_id)` with sessions. Cancelling the run's `asyncio.Task` also works as a hard stop.
3. The planner checks the inbox between steps and while tools run, then raises `SteeringCancelled`.

See [[penguiflow-hitl-pause-resume]] for steering and pause semantics.

## Error handling mid-stream

If the run fails mid-stream, the client may already have partial content.
- `planner.run()` raises (e.g. LLM unavailable): catch it in the bridge and send a terminal `error` frame, then close.
- The planner finishes without an answer: `PlannerFinish.reason` is `"no_path"` or `"budget_exhausted"` — send it in the `final` frame so the UI can show a graceful failure instead of the partial text.
- Sessions publish an `ERROR` update and a `STATUS_CHANGE` with `status == "FAILED"`.

Always end the stream with exactly one terminal frame (`final` or `error`); clients otherwise hang or reconnect forever.

## Observability for streams

Track:
- **TTFC** per request (p50/p95/p99) — by route, by tenant.
- **Chunk rate** — bottleneck indicator.
- **Stream duration** (accept → terminal frame).
- **Abandon rate** (streams without a terminal frame) — disconnects, errors.
- **Dropped updates** (session subscriber overflow) — capacity indicator.

Measure in the bridge (it sees every frame the user sees), not inside tools. See [[penguiflow-observability]].

## Anti-patterns

- **One frame per token over WAN** — coalesce.
- **Terminal frame only sometimes** — clients hang. Send it on success, failure and cancel.
- **One planner instance + one callback for all users** — tokens leak across requests. Use a planner per request or sessions.
- **Blocking work in `event_callback`** — stalls the planner loop. Enqueue only.
- **Appending across `action_seq` changes** — duplicated answers after repairs. Reset on a new `action_seq`.
