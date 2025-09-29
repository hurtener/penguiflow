# Streaming LLM Demo

This example shows how a node can emit streaming chunks using `Context.emit_chunk`
and how to format those chunks for clients. A mock LLM node breaks the response into
tokens and sends them downstream, while an SSE-style sink renders each chunk with
`format_sse_event` and emits a final JSON payload via `chunk_to_ws_json` that can be
sent to WebSocket listeners.

## Running

```bash
uv run python examples/streaming_llm/flow.py
```

Expected output (timestamps will vary, `<id>` omits the actual identifier):

```
event: chunk
id: 0
data: Penguins 

...

event: done
id: 4
data: warm
{"stream_id": "<id>", "seq": 4, "text": "warm", "done": true, "meta": {"token_index": 4}}
final: Penguins huddle to stay warm
```

The sink receives every `StreamChunk` in order, prints SSE-compatible lines, and
returns the assembled string to the Rookery once `done=True`, demonstrating
backpressure-friendly streaming end-to-end.
