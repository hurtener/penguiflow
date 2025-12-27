# Pause and Resume Signaling

This diagram shows how pause events (HITL or OAuth) reach the UI.

```mermaid
sequenceDiagram
  participant Tool
  participant Planner
  participant Adapter
  participant UI

  Tool-->>Planner: PlannerPause(reason, payload, resume_token)
  Planner-->>Adapter: ChatResult.pause
  Adapter-->>UI: CUSTOM name=pause (payload)
  Adapter-->>UI: TEXT_MESSAGE_CONTENT "Planner paused ..."
  UI-->>UI: render auth link and resume token
```

Notes:
- Resume is handled by existing HITL primitives; AG-UI only carries the pause
  payload and a message for the UI.
