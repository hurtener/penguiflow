# Real-Time Status Streaming

The enterprise agent example now supports **real-time status streaming** via the `--stream` flag, simulating what a production WebSocket/SSE implementation would deliver to a frontend.

## Usage

```bash
# Run with real-time status updates
uv run python examples/planner_enterprise_agent/main.py --stream

# Stream a custom query
uv run python examples/planner_enterprise_agent/main.py --stream --query "Analyze deployment logs"

# Run without streaming (default)
uv run python examples/planner_enterprise_agent/main.py
```

## What It Does

When `--stream` is enabled, the agent displays status updates in real-time as they occur during execution:

```
================================================================================
Query 1: Analyze the latest deployment logs
================================================================================

  ┌─ Real-time Status Stream ─────────────
  │ 🤔 [THINKING] Determining workflow path
  │ ✅ [OK] [Step 1] ▶️  running Parsing repository sources
  │ ✅ [OK] [Step 1] ✓ ok Done!
  │ ✅ [OK] [Step 2] ▶️  running Launching metadata subflow
  │ ✅ [OK] [Step 2] ✓ ok Done!
  │ ✅ [OK] [Step 3] ▶️  running Summarizing findings
  │ ✅ [OK] [Step 3] ✓ ok Done!
  │ ✅ [OK] [Step 4] ▶️  running Assembling HTML report
  │ ✅ [OK] [Step 4] ✓ ok Done!
  │ ✅ [OK] [Step 99] ▶️  running Synthesizing final response
  │ ✅ [OK] [Step 99] ✓ ok Done!
  └───────────────────────────────────────

Route: documents
Answer: Analyzed 4 documents. Total size: 5900KB...
```

## How It Works

The streaming implementation mirrors the pattern from `examples/roadmap_status_updates_subflows/`:

1. **Status Publisher**: Nodes call `_publish_status()` to emit `StatusUpdate` events
2. **STATUS_BUFFER**: Updates are collected in a global buffer keyed by `trace_id`
3. **Background Monitor**: A background async task (`_monitor_and_stream_status`) polls STATUS_BUFFER and prints updates in real-time
4. **Terminal Output**: Updates are formatted with icons and timestamps, written to stderr

### Architecture

```python
# Node publishes status
_publish_status(
    ctx=ctx,
    status="thinking",
    roadmap_step_id=1,
    roadmap_step_status="running",
    message="Parsing repository sources"
)

# Background monitor detects and streams
async def _monitor_and_stream_status(stream_enabled: bool):
    while True:
        for trace_id, updates in STATUS_BUFFER.items():
            if new_updates:
                formatted = _format_status_for_terminal(update, trace_id)
                print(f"  │ {formatted}", file=sys.stderr, flush=True)
        await asyncio.sleep(0.05)
```

## Production Integration

This example demonstrates how to integrate status streaming with your production stack:

### WebSocket Server
```python
@app.websocket("/agent/stream/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Replace the terminal printer with WebSocket sender
    async def send_status_updates():
        last_count = 0
        while True:
            updates = STATUS_BUFFER.get(session_id, [])
            if len(updates) > last_count:
                for update in updates[last_count:]:
                    await websocket.send_json(update.model_dump())
                last_count = len(updates)
            await asyncio.sleep(0.05)

    stream_task = asyncio.create_task(send_status_updates())
    result = await agent.execute(query, trace_id=session_id)
    stream_task.cancel()
    await websocket.send_json({"type": "complete", "result": result.model_dump()})
```

### Server-Sent Events (SSE)
```python
@app.get("/agent/stream/{session_id}")
async def sse_endpoint(session_id: str):
    async def event_generator():
        last_count = 0
        while True:
            updates = STATUS_BUFFER.get(session_id, [])
            if len(updates) > last_count:
                for update in updates[last_count:]:
                    yield f"data: {update.model_dump_json()}\n\n"
                last_count = len(updates)
            await asyncio.sleep(0.05)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

## Status Update Fields

Each `StatusUpdate` contains:

```python
class StatusUpdate(BaseModel):
    status: Literal["thinking", "ok", "error"]
    message: str | None = None
    roadmap_step_id: int | None = None
    roadmap_step_status: Literal["running", "ok", "error"] | None = None
    roadmap: list[RoadmapStep] | None = None
```

### Status Icons

- 🤔 `thinking` - Agent is reasoning/planning
- ✅ `ok` - Operation completed successfully
- ❌ `error` - Operation failed

### Roadmap Step Status

- ▶️  `running` - Step is currently executing
- ✓ `ok` - Step completed successfully
- ✗ `error` - Step failed

## Comparison with roadmap_status_updates_subflows

| Feature | enterprise_agent | roadmap_status_updates |
|---------|------------------|------------------------|
| **Pattern** | ReactPlanner orchestration | PenguiFlow DAG |
| **Status Sink** | Terminal (via --stream) | status_collector node |
| **Streaming** | Background monitor | Fan-out to sink node |
| **Use Case** | LLM-driven agent workflows | Deterministic pipelines |

Both examples demonstrate the same core pattern:
1. Nodes emit `StatusUpdate` events
2. Updates are collected in buffers
3. Clients consume updates in real-time
4. Suitable for production WebSocket/SSE

## See Also

- [examples/roadmap_status_updates_subflows/](../roadmap_status_updates_subflows/) - PenguiFlow DAG with status streaming
- [examples/planner_enterprise_agent/README.md](./README.md) - Enterprise agent overview
