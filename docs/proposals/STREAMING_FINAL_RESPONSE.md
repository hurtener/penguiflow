# Proposal: Streaming Final Response for ReactPlanner

> **Status**: Draft
> **Scope**: LiteLLM path only (DSPy path deferred)
> **Difficulty**: Medium

---

## Problem Statement

When ReactPlanner decides to end a conversation (i.e., `action.next_node is None`), the final LLM response is returned as a complete string. There is no way to stream the final answer token-by-token to the user.

This creates a poor UX for longer responses — users see nothing until the entire answer is generated.

### Current Behavior

```
User Query → [Tool calls stream chunks] → Final Answer (blocks until complete)
```

### Desired Behavior

```
User Query → [Tool calls stream chunks] → Final Answer (streams token-by-token)
```

---

## Why This Matters

1. **User Experience** — Streaming provides immediate feedback; users see the response forming in real-time
2. **Perceived Latency** — Even if total time is the same, streaming feels faster
3. **Long Responses** — For detailed answers, waiting 10+ seconds for a wall of text is frustrating
4. **Parity with Tools** — Tool outputs already support streaming via `ctx.emit_chunk()`; the final LLM response should too

---

## Current Architecture

### Flow When Planner Ends

```
ReactPlanner.run()
    ↓
_run_loop() detects action.next_node is None
    ↓
_build_final_payload() extracts answer
    ↓
_finish() returns PlannerFinish (no streaming)
```

### LLM Call Path

```
ReactPlanner.step()
    ↓
self._client.complete(messages, response_format)
    ↓
_LiteLLMJSONClient.complete()
    ↓
litellm.acompletion(**params)  ← stream=False (implicit)
    ↓
Returns full response as dict
```

**Key Gap**: No `stream=True` is ever passed to LiteLLM. The response is awaited as a single object.

---

## Proposed Solution

Make streaming an **opt-in feature** via a new `stream_final_response` parameter on ReactPlanner.

### API Design

```python
# Non-streaming (default, backward compatible)
planner = ReactPlanner(
    llm="gpt-4o",
    nodes=my_nodes,
)

# With streaming enabled
planner = ReactPlanner(
    llm="gpt-4o",
    nodes=my_nodes,
    stream_final_response=True,
    event_callback=my_handler,  # receives llm_stream_chunk events
)
```

### Event Callback Integration

Streaming chunks will be emitted via the existing `event_callback` mechanism:

```python
def my_handler(event: PlannerEvent) -> None:
    if event.event_type == "llm_stream_chunk":
        print(event.extra["text"], end="", flush=True)

planner = ReactPlanner(
    llm="gpt-4o",
    stream_final_response=True,
    event_callback=my_handler,
)
```

This reuses the existing infrastructure rather than adding a new callback parameter.

---

## Implementation Overview

### Changes Required

| Component | Change |
|-----------|--------|
| `ReactPlanner.__init__` | Add `stream_final_response: bool = False` parameter |
| `ReactPlanner.__init__` | Propagate flag when creating `_LiteLLMJSONClient` |
| `_LiteLLMJSONClient.__init__` | Accept `stream_final_response` and optional chunk callback |
| `_LiteLLMJSONClient.complete` | Conditionally enable `stream=True` and iterate chunks |
| `ReactPlanner._run_loop` | Emit `llm_stream_chunk` events during final answer generation |

### Propagation Chain

```
ReactPlanner(stream_final_response=True)
        │
        ▼
    stores self._stream_final_response
        │
        ▼
    _LiteLLMJSONClient(stream_final_response=True, on_chunk=...)
        │
        ▼
    litellm.acompletion(**params, stream=True)
        │
        ▼
    async for chunk in response: emit via callback
```

### Streaming Logic in LiteLLM Client

When `stream_final_response=True`:

1. Pass `stream=True` to `litellm.acompletion()`
2. Iterate the async response generator
3. Extract `delta.content` from each chunk
4. Invoke the chunk callback (which emits `PlannerEvent`)
5. Accumulate text for the final return value
6. Extract cost from the final chunk's metadata

When `stream_final_response=False`:

- Current behavior unchanged

---

## What This Does NOT Cover

| Item | Reason |
|------|--------|
| DSPyLLMClient | DSPy does not natively expose streaming; requires separate investigation |
| Custom `llm_client` | Users providing custom clients must implement streaming themselves |
| Summarizer/Reflection LLMs | These are internal; streaming not needed (keep non-streaming for efficiency) |
| Tool streaming | Already implemented via `ctx.emit_chunk()` |

---

## Backward Compatibility

- `stream_final_response` defaults to `False`
- Existing code continues to work unchanged
- `JSONLLMClient` protocol remains the same (no breaking changes)
- Event callback receives a new event type (`llm_stream_chunk`) which can be ignored

---

## Testing Strategy

1. **Unit tests** for `_LiteLLMJSONClient`:
   - Streaming path with mocked LiteLLM response
   - Chunk accumulation correctness
   - Cost extraction from streaming metadata

2. **Integration tests** for `ReactPlanner`:
   - Verify `stream_final_response=False` works as before
   - Verify `stream_final_response=True` emits chunk events
   - Verify final answer content matches accumulated chunks

3. **Example** in `examples/streaming_llm/`:
   - Demonstrate streaming final response to console/SSE

---

## Open Questions with answers

1. **Should streaming apply to all LLM calls or just the final answer?**
   Recommendation: Only the final answer. Intermediate `step()` calls produce structured JSON for tool routing — streaming those adds complexity with little benefit.

2. **How to handle JSON mode with streaming?**
   LiteLLM supports streaming with `response_format={"type": "json_object"}`. The final accumulated content is still valid JSON. No special handling needed.

3. **Error handling during streaming?**
   If streaming fails mid-way, raise the exception as usual. Partial chunks already emitted cannot be retracted, but the final `PlannerFinish` will not be returned.

---

## Summary

| Aspect | Value |
|--------|-------|
| **Goal** | Stream the final LLM response token-by-token |
| **Scope** | LiteLLM path only |
| **Difficulty** | Medium |
| **Breaking Changes** | None |
| **New Parameter** | `stream_final_response: bool = False` |
| **Delivery Mechanism** | `event_callback` with `event_type="llm_stream_chunk"` |

### UI/Playground implementation (current state)
- `llm_stream_chunk` carries `phase`: `"action"` for planner JSON vs `"answer"` for user-facing text.
- Action-phase chunks drive a thinking indicator; answer-phase chunks stream into the chat bubble. Planner events/trajectory remain unchanged.
- SSE backend forwards phase; the Svelte UI consumes it and stops overwriting the final answer once streaming finishes.
