# Planner Stream Events and `ctx.emit_chunk`

All partial output from a `ReactPlanner` reaches you as `PlannerEvent`s through the planner's `event_callback`:

```python
from penguiflow.planner import PlannerEvent

def on_event(event: PlannerEvent) -> None:   # synchronous; never block here
    ...
```

`PlannerEvent` fields: `event_type`, `ts`, `trajectory_step`, `thought`, `node_name`, `latency_ms`, `token_estimate`, `error`, `extra`. `event.to_payload()` flattens them (`event`, `ts`, `step`, optional fields, then `extra` keys) into a JSON-friendly dict.

## `llm_stream_chunk` — the planner's own LLM output

Emitted while the LLM is generating, when `stream_final_response=True` and the client supports callback streaming.

| `extra` key | Meaning |
|---|---|
| `text` | the new text delta |
| `done` | `True` on the last chunk of a sequence |
| `channel` | `"answer"` (user-facing final answer) or `"thinking"` (reasoning, when the model emits it) |
| `phase` | finer-grained source (`"answer"`, `"thinking"`, `"args"`, `"revision"`) |
| `action_seq` | id of the planner step producing the text |

Rules:
- Show `channel == "answer"` to end users; `thinking` belongs in a collapsible panel, if anywhere.
- A **new `action_seq`** means the planner restarted its answer (e.g. a finish-repair re-streams the corrected answer). Replace, don't append.
- `PlannerFinish.metadata["answer_action_seq"]` names the sequence that produced the final answer.
- These events are only emitted when an `event_callback` is set.

## `stream_chunk` — progress from inside a tool

Tools receive a `ToolContext` and can stream while they run:

```python
@tool(desc="Summarize a long document")
async def summarize(args: SummarizeArgs, ctx) -> Summary:
    parts = []
    async for seq, token in aenumerate(llm.stream(args.text)):
        parts.append(token)
        await ctx.emit_chunk("summary", seq, token, meta={"channel": "answer"})
    await ctx.emit_chunk("summary", len(parts), "", done=True, meta={"channel": "answer"})
    return Summary(text="".join(parts))
```

Signature: `await ctx.emit_chunk(stream_id, seq, text, *, done=False, meta=None)`.
- `stream_id` — your name for the stream; use distinct ids for concurrent streams.
- `seq` — supplied by the tool; keep it monotonically increasing per `stream_id`.
- `meta["channel"]` defaults to `"thinking"`; set `"answer"` for user-facing text.
- The event's `extra` is `{"stream_id", "seq", "text", "done", "meta"}`.
- Chunks are also recorded on the trajectory: `result.metadata["steps"][i]["streams"][stream_id]`.

## `artifact_chunk` — partial structured output

`await ctx.emit_artifact(stream_id, chunk, *, done=False, artifact_type=None, meta=None)` streams partial structured data (e.g. a chart config being built). Consumers merge chunks per `stream_id`.

## Status events

For progress indicators, use the loop events: `step_start` / `step_complete`, `tool_call_start` / `tool_call_end` / `tool_call_result` (`extra["tool_name"]`, `extra["tool_call_id"]`), `finish` (with `extra["reason"]`). See `status-and-progressive-ui.md`.

## Ordering guarantees
- Events are delivered synchronously, in emission order, from the task running `planner.run()`.
- Within one `stream_id` (tool chunks) or one `action_seq` (LLM chunks), order is preserved.
- Parallel tool calls can interleave chunks from different `stream_id`s — group by id on the client.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| No `llm_stream_chunk` | `stream_final_response=False`, non-streaming client, or no `event_callback` | Enable all three |
| Answer shown twice | Re-stream after repair | Reset on new `action_seq` |
| Tool chunks hidden from users | default `channel="thinking"` | Pass `meta={"channel": "answer"}` |
| Out-of-order tool chunks | `seq` reused or concurrent producers on one `stream_id` | One producer per `stream_id`, increasing `seq` |
| Event loop stalls | blocking work in `event_callback` | Only enqueue in the callback |

## Observability

Every event is also logged by the planner (`llm_stream_chunk` at DEBUG, the rest at INFO) with `to_payload()` as `extra`. See [[penguiflow-observability]].
