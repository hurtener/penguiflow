# AG-UI End to End Flow

This diagram shows the full request/response loop when the Playground UI
uses AG-UI.

```mermaid
sequenceDiagram
  participant User
  participant UI as Playground UI
  participant Client as @ag-ui/client HttpAgent
  participant API as POST /agui/agent
  participant Adapter as PenguiFlowAdapter
  participant Wrapper as AgentWrapper
  participant Planner as ReactPlanner

  User->>UI: Send message
  UI->>Client: RunAgentInput (threadId, runId, messages, forwardedProps)
  Client->>API: SSE request
  API->>Adapter: run(input)
  Adapter->>Wrapper: chat(query, llm_context, tool_context)
  Wrapper->>Planner: run(...)
  Planner-->>Wrapper: PlannerEvent stream
  Wrapper-->>Adapter: event_consumer callback
  Adapter-->>API: AG-UI events
  API-->>Client: SSE event stream
  Client-->>UI: Observable events
  UI-->>User: Render messages, tools, artifacts, pause
```

Notes:
- The legacy SSE path still exists via /chat/stream and is selected when the
  UI toggle is off.
- AG-UI uses POST with SSE framing from ag-ui-protocol.
