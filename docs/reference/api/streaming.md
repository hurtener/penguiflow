# Streaming

Helpers for consuming a flow as a stream of events and adapting stream chunks to
SSE or WebSocket transports.

The `StreamChunk` payload type itself is documented under
[Messages & types](messages.md).

::: penguiflow.streaming
    options:
      members:
        - stream_flow
        - emit_stream_events
        - chunk_to_ws_json
        - format_sse_event
