# SSE, WebSocket, and Session Bridges

There is no built-in SSE/WebSocket formatter; the wire format is a few lines of your own code. The pattern is always the same: the planner's synchronous `event_callback` enqueues events into a per-request `asyncio.Queue`, and the HTTP/WS handler drains it.

## Local helpers

```python
import json
from penguiflow.planner import PlannerEvent


def sse(event: str, data: str, *, event_id: str | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.extend(f"data: {line}" for line in data.splitlines() or [""])
    return "\n".join(lines) + "\n\n"


def event_to_json(event: PlannerEvent) -> str:
    return json.dumps(event.to_payload(), default=str)
```

## FastAPI SSE recipe (answer tokens + status + final)

```python
import asyncio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/chat")
async def chat(q: str, session_id: str):
    queue: asyncio.Queue[PlannerEvent | None] = asyncio.Queue()
    planner = make_planner(event_callback=queue.put_nowait)   # one planner per request

    async def gen():
        task = asyncio.create_task(planner.run(q, tool_context={"session_id": session_id}))
        task.add_done_callback(lambda _t: queue.put_nowait(None))
        try:
            while (event := await queue.get()) is not None:
                extra = event.extra
                if event.event_type == "llm_stream_chunk" and extra.get("channel") == "answer":
                    yield sse("chunk", extra["text"], event_id=str(extra.get("action_seq")))
                elif event.event_type == "tool_call_start":
                    yield sse("status", f"calling {extra.get('tool_name')}")
            result = await task
            yield sse("final", json.dumps({"reason": result.reason, "payload": getattr(result, "payload", None)}, default=str))
        finally:
            if not task.done():
                task.cancel()   # client disconnected

    return StreamingResponse(gen(), media_type="text/event-stream")
```

Browser side:

```javascript
const es = new EventSource(`/chat?q=${encodeURIComponent(q)}&session_id=s1`);
let seq = null, text = "";
es.addEventListener("chunk", (e) => { if (e.lastEventId !== seq) { seq = e.lastEventId; text = ""; } text += e.data; render(text); });
es.addEventListener("status", (e) => showStatus(e.data));
es.addEventListener("final", (e) => { renderFinal(JSON.parse(e.data)); es.close(); });
```

The `id` carries `action_seq`, so the client resets the text when the planner restarts its answer.

## FastAPI WebSocket recipe

```python
from fastapi import WebSocket

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    request = await websocket.receive_json()
    queue: asyncio.Queue[PlannerEvent | None] = asyncio.Queue()
    planner = make_planner(event_callback=queue.put_nowait)
    task = asyncio.create_task(planner.run(request["q"], tool_context={"session_id": request["session_id"]}))
    task.add_done_callback(lambda _t: queue.put_nowait(None))
    while (event := await queue.get()) is not None:
        await websocket.send_text(event_to_json(event))
    result = await task
    await websocket.send_json({"event": "final", "reason": result.reason, "payload": getattr(result, "payload", None)})
    await websocket.close()
```

## Sharing one planner instance

`event_callback` belongs to the planner instance. If you must share one instance across concurrent requests, route inside the callback with a `contextvars.ContextVar` set in the request's task before `planner.run()` (events are emitted from that task, so the variable is visible). Otherwise prefer a planner factory.

## Session bridge (`StreamingSession`)

Sessions remove the per-request plumbing: `PlannerTaskPipeline` builds a planner per task, installs its own callback, and projects events into `StateUpdate`s that clients subscribe to.

```python
import uuid
from penguiflow import PlannerTaskPipeline, SessionManager, UpdateType

manager = SessionManager()
TERMINAL = {"COMPLETE", "FAILED", "CANCELLED"}

@app.get("/sessions/{session_id}/chat")
async def chat(session_id: str, q: str):
    session = await manager.get_or_create(session_id)
    pipeline = PlannerTaskPipeline(planner_factory=make_planner)
    task_id = uuid.uuid4().hex

    async def gen():
        updates = await session.subscribe(task_ids=[task_id])   # subscribe before the task starts
        run = asyncio.create_task(session.run_task(pipeline, query=q, task_id=task_id))
        async for update in updates:
            yield sse(update.update_type.value, json.dumps(update.content, default=str), event_id=update.update_id)
            if update.update_type == UpdateType.STATUS_CHANGE and update.content.get("status") in TERMINAL:
                break
        await run

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- Answer/revision chunks arrive as `RESULT` updates, other channels as `THINKING`, tool calls as `TOOL_CALL`, step progress as `PROGRESS`.
- `subscribe(task_ids=[...], since_id=...)` replays persisted updates for reconnecting clients.
- For full duplex (updates out, steering in), implement the `Transport` protocol (`send(update)`, `receive() -> SteeringEvent | None`, `close()`) and call `await session.connect(transport)`.

## Choosing a bridge

| Need | Use |
|---|---|
| One request, one stream (chat endpoint) | per-request queue + planner factory |
| Reconnect / replay, background tasks, steering from the client | `StreamingSession` + `subscribe()` / `connect(transport)` |
| Another agent is the client | `A2AService` (`message/stream`) — see [[penguiflow-a2a-integration]] |

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| SSE frames merge in the browser | missing blank line terminator | each frame must end with `\n\n` |
| `EventSource` reconnects forever | server closed without a terminal event | always send a `final` (or `error`) event before closing |
| Client receives another user's tokens | shared planner + single callback | planner per request, or contextvar routing |
| Handler hangs after the run ends | no sentinel enqueued | `task.add_done_callback(lambda _t: queue.put_nowait(None))` |
| Planner keeps running after disconnect | task not cancelled | cancel the run task in `finally` |
