# PlannerEvent to AG-UI Event Mapping

This diagram shows how planner events are converted into AG-UI events by the
adapter.

```mermaid
flowchart LR
  subgraph PlannerEvents
    A[step_start / step_complete]
    B[llm_stream_chunk channel=answer]
    C[tool_call_start / end / result]
    D[artifact_stored]
    E[resource_updated]
  end

  subgraph AGUIEvents
    A1[STEP_STARTED / STEP_FINISHED]
    B1[TEXT_MESSAGE_START / CONTENT / END]
    C1[TOOL_CALL_START / ARGS / END / RESULT]
    D1[CUSTOM name=artifact_stored]
    E1[CUSTOM name=resource_updated]
  end

  A --> A1
  B --> B1
  C --> C1
  D --> D1
  E --> E1
```

Lifecycle events:
- RUN_STARTED and RUN_FINISHED always wrap the stream.
- RUN_ERROR is emitted on exceptions.
