# Implementation Plan: Chat-Based Steering UX

## Overview

Enable users to send messages while tasks are running, with the LLM interpreting intent and routing appropriately. The foreground task acts as supervisor with HITL capabilities for disambiguation.

---

## User Experience Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         USER SENDS MESSAGE                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ CASE 1: Foreground task running                                 │   │
│  │                                                                 │   │
│  │  [Chat Input] ──────────────────────────── [🔶 Steer]          │   │
│  │                                              (orange button)    │   │
│  │                                                                 │   │
│  │  → INJECT_CONTEXT to foreground task                           │   │
│  │  → LLM interprets: clarification? redirect? delegate to bg?    │   │
│  │  → If ambiguous (e.g., "cancel research") → HITL select_option │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ CASE 2: No foreground, but background tasks running             │   │
│  │                                                                 │   │
│  │  [Chat Input] ──────────────────────────── [🔵 Send]           │   │
│  │                                              (blue button)      │   │
│  │                                                                 │   │
│  │  → Spawn NEW foreground task                                    │   │
│  │  → Include active background task context in system prompt      │   │
│  │  → LLM decides: new work? or steer/query background tasks?     │   │
│  │  → If targeting specific bg task → HITL confirm if ambiguous   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ CASE 3: No tasks running                                        │   │
│  │                                                                 │   │
│  │  [Chat Input] ──────────────────────────── [🔵 Send]           │   │
│  │                                              (blue button)      │   │
│  │                                                                 │   │
│  │  → Normal chat flow (spawn foreground task)                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture

### Steering Flow

```
Frontend                    Backend                         Planner
────────                    ───────                         ───────

[User types message]
        │
        ▼
[Check task state]
        │
        ├─► Foreground running?
        │   YES → POST /steer
        │         {                               ─────────►  SteeringInbox.push()
        │           event_type: "USER_MESSAGE",              │
        │           task_id: <foreground_id>,                ▼
        │           payload: {                              _apply_steering()
        │             text: "...",                           │
        │             active_tasks: [...]                    ▼
        │           }                                       trajectory.steering_inputs
        │         }                                          │
        │                                                    ▼
        │                                                   LLM interprets
        │                                                    │
        │                                          ┌────────┴────────┐
        │                                          ▼                 ▼
        │                                    Continue work    Use HITL tools
        │                                    with context     to disambiguate
        │                                                            │
        │                                                            ▼
        │                                                    select_option/confirm
        │                                                            │
        │   ◄─────────────────────────────────────────────────────────
        │   (Interactive component rendered)
        │
        └─► NO foreground → POST /chat
            (normal flow, but with
             background context injected)
```

---

## Implementation Steps

### Phase 1: Backend - New Steering Event Type

**File: `penguiflow/steering.py`**

```python
class SteeringEventType(str, Enum):
    INJECT_CONTEXT = "INJECT_CONTEXT"
    REDIRECT = "REDIRECT"
    CANCEL = "CANCEL"
    PRIORITIZE = "PRIORITIZE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    USER_MESSAGE = "USER_MESSAGE"  # NEW: Raw user text, LLM interprets
```

**Update validation in `validate_steering_event()`:**

```python
elif event_type == SteeringEventType.USER_MESSAGE:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        errors.append("USER_MESSAGE requires non-empty 'text'")
    # active_tasks is optional context
    active_tasks = payload.get("active_tasks")
    if active_tasks is not None and not isinstance(active_tasks, list):
        errors.append("USER_MESSAGE 'active_tasks' must be a list")
```

---

### Phase 2: Backend - Enhanced Steering Injection

**File: `penguiflow/planner/react_runtime.py`**

Update `_apply_steering()` to handle USER_MESSAGE:

```python
if event.event_type == SteeringEventType.USER_MESSAGE:
    # Format as rich injection with task context
    injection = {
        "steering": {
            "event_id": event.event_id,
            "event_type": "USER_MESSAGE",
            "user_text": event.payload.get("text", ""),
            "active_background_tasks": event.payload.get("active_tasks", []),
            "instructions": (
                "The user has sent a message while you are working. "
                "Interpret their intent: "
                "1) If it's a clarification or additional context, incorporate it. "
                "2) If they want to change direction, adjust your approach. "
                "3) If they're asking about or want to control a background task, "
                "   use the tasks.* tools. If multiple tasks match, use select_option "
                "   to let the user choose which one."
            ),
            "created_at": event.created_at.isoformat(),
        }
    }
    trajectory.steering_inputs.append(json.dumps(injection, ensure_ascii=False))
```

---

### Phase 3: Prompt Engineering

**File: `penguiflow/planner/prompts.py`**

Add steering interpretation guidance to system prompt (when background_tasks enabled):

```python
STEERING_INTERPRETATION_PROMPT = """
## Real-Time User Steering

During execution, the user may send steering messages. When you receive a steering input:

1. **Clarification/Context**: If the user is providing additional information or clarifying their request, incorporate it into your current work.

2. **Direction Change**: If the user wants you to change approach or focus on something different, acknowledge and adjust.

3. **Background Task Control**: If the user mentions a background task (e.g., "cancel the research", "check on the analysis"):
   - Use `tasks.list()` to see active background tasks
   - If the reference is ambiguous (multiple matching tasks), use `select_option` to let the user choose:
     ```
     select_option(
       prompt="Which task would you like to cancel?",
       options=[
         {"value": "task-123", "label": "Research: Market Analysis"},
         {"value": "task-456", "label": "Research: Competitor Review"},
       ]
     )
     ```
   - Then use `tasks.cancel()`, `tasks.prioritize()`, or `tasks.get()` as appropriate

4. **Status Query**: If the user asks about progress, provide a summary of current and background work.

Always acknowledge steering messages to confirm you received and understood them.
"""
```

---

### Phase 4: Configurable Steering Queue Limit

**File: `penguiflow/planner/models.py`** (update BackgroundTasksConfig)

```python
class BackgroundTasksConfig(BaseModel):
    # ... existing fields ...
    max_pending_steering: int = 2
```

**File: `penguiflow/steering.py`** (update SteeringInbox)

```python
class SteeringInbox:
    def __init__(self, *, maxsize: int = 100, max_pending_user_messages: int = 2) -> None:
        self._queue: asyncio.Queue[SteeringEvent] = asyncio.Queue(maxsize=maxsize)
        self._max_pending_user_messages = max_pending_user_messages
        self._pending_user_message_count = 0
        # ... rest unchanged ...

    async def push(self, event: SteeringEvent) -> bool:
        # ... existing cancel/pause/resume handling ...

        # Enforce limit on USER_MESSAGE events
        if event.event_type == SteeringEventType.USER_MESSAGE:
            if self._pending_user_message_count >= self._max_pending_user_messages:
                # Drop oldest USER_MESSAGE or reject
                return False
            self._pending_user_message_count += 1

        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            if event.event_type == SteeringEventType.USER_MESSAGE:
                self._pending_user_message_count -= 1
            return False

    def drain(self) -> list[SteeringEvent]:
        events = []
        while True:
            try:
                event = self._queue.get_nowait()
                if event.event_type == SteeringEventType.USER_MESSAGE:
                    self._pending_user_message_count -= 1
                events.append(event)
            except asyncio.QueueEmpty:
                break
        return events
```

---

## Testing Plan

### Unit Tests

1. **steering.py**: Test USER_MESSAGE validation
2. **react_runtime.py**: Test USER_MESSAGE injection format

### Integration Tests

1. Send USER_MESSAGE while foreground running → verify injection
2. Send USER_MESSAGE with ambiguous task reference → verify HITL triggered
3. Send message with only background tasks → verify new foreground spawned

### E2E Tests

1. Start long-running task → send steering → verify LLM receives it
2. Spawn background task → send "cancel the task" → verify select_option appears

---

## Migration & Rollout

### Feature Flag

```python
# In config
background_tasks_chat_steering_enabled: bool = True
```

### Gradual Rollout

1. **Phase 1**: Backend changes (no user-facing impact)
2. **Phase 2**: Frontend with feature flag (opt-in)
3. **Phase 3**: Enable by default for background_tasks users

---

## Summary

| Phase | Component | Changes |
|-------|-----------|---------|
| 1 | `steering.py` | Add `USER_MESSAGE` event type + validation |
| 2 | `react_runtime.py` | Handle `USER_MESSAGE` with task context + instructions |
| 3 | `prompts.py` | Steering interpretation guidance for HITL disambiguation |
| 4 | `steering.py` + `models.py` | Configurable `max_pending_steering` (default: 2) |

---

## Design Decisions (Resolved)

1. **Steering queue overflow**: Configurable cap on pending steering messages.
   - Default: 2 messages max
   - Configurable via `BackgroundTasksConfig.max_pending_steering` (default: 2)
   - Oldest messages dropped if exceeded (or reject with user feedback)

2. **Latency feedback**: The UI client shows a notification while waiting for LLM acknowledgment.
   - Example: "Steering message sent. Waiting for agent to acknowledge..."
   - Dismiss automatically when agent responds (steering_received event or text acknowledgment)
   - User can dismiss manually if needed

3. **Direct background routing**: No. Always route through foreground.
   - Even if user clearly targets a background task, spawn/use foreground
   - Foreground LLM provides acknowledgment with creative details
   - Example: "Got it! I'm checking on the market research task now... It's currently 60% complete, analyzing competitor pricing data."
   - This maintains supervision model and provides better UX (conversational acknowledgment)
