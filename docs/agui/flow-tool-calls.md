# Tool Call Lifecycle

Tool calls are emitted by the planner and become AG-UI tool call events.

```mermaid
sequenceDiagram
  participant Planner
  participant Adapter
  participant UI

  Planner->>Adapter: tool_call_start (tool_call_id, tool_name, args_json)
  Adapter->>UI: TOOL_CALL_START
  Adapter->>UI: TOOL_CALL_ARGS (args_json)
  Planner->>Adapter: tool_call_end (tool_call_id)
  Adapter->>UI: TOOL_CALL_END
  Planner->>Adapter: tool_call_result (result_json)
  Adapter->>UI: TOOL_CALL_RESULT
```

Notes:
- The UI attaches tool calls to the current assistant message.
- The result is stored on the tool call and can be displayed inline.
