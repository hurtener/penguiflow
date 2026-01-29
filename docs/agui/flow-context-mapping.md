# Context Split Mapping (llm_context vs tool_context)

AG-UI does not define llm_context or tool_context fields. The Playground
packages them into forwardedProps so the adapter can unpack them and preserve
the split.

```mermaid
flowchart TD
  Setup[Setup tab inputs]
  LLM[llm_context JSON]
  TOOL[tool_context JSON]
  Builder[ChatStreamManager builds RunAgentInput]
  API[POST /agui/agent]
  Adapter[PenguiFlowAdapter]
  Wrapper[AgentWrapper.chat]
  Planner[ReactPlanner.run]

  Setup --> LLM
  Setup --> TOOL
  LLM --> Builder
  TOOL --> Builder
  Builder -->|forwardedProps.penguiflow| API
  API --> Adapter
  Adapter -->|_extract_forwarded_contexts| Wrapper
  Wrapper --> Planner
```

Request body shape (simplified):

```json
{
  "threadId": "session-id",
  "runId": "trace-id",
  "messages": [ ... ],
  "state": {},
  "forwardedProps": {
    "penguiflow": {
      "llm_context": { ... },
      "tool_context": { ... }
    }
  }
}
```
