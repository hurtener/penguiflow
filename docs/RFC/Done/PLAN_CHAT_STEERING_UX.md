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

### Phase 3: Backend - Endpoint Enhancement

**File: `penguiflow/cli/playground.py`**

Add endpoint to get current task state for frontend decision-making:

```python
class TaskStateResponse(BaseModel):
    foreground_task_id: str | None
    foreground_status: str | None
    background_tasks: list[dict[str, Any]]

@app.get("/session/{session_id}/task-state", response_model=TaskStateResponse)
async def get_task_state(session_id: str) -> TaskStateResponse:
    """Get current foreground/background task state for steering decisions."""
    session = await session_manager.get_or_create(session_id)

    foreground_id = session._foreground_task_id
    foreground_status = None
    if foreground_id:
        fg_state = await session.registry.get(foreground_id)
        foreground_status = fg_state.status.value if fg_state else None

    # Get active background tasks
    all_tasks = await session.registry.list_tasks()
    background_tasks = [
        {
            "task_id": t.task_id,
            "description": t.description,
            "status": t.status.value,
            "task_type": t.task_type.value,
            "priority": t.priority,
        }
        for t in all_tasks
        if t.task_type == TaskType.BACKGROUND and t.status in {TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.PAUSED}
    ]

    return TaskStateResponse(
        foreground_task_id=foreground_id if foreground_status == "RUNNING" else None,
        foreground_status=foreground_status,
        background_tasks=background_tasks,
    )
```

---

### Phase 4: Frontend - API Service

**File: `playground_ui/src/lib/services/api.ts`**

```typescript
export interface TaskStateResponse {
  foreground_task_id: string | null;
  foreground_status: string | null;
  background_tasks: Array<{
    task_id: string;
    description: string | null;
    status: string;
    task_type: string;
    priority: number;
  }>;
}

export async function getTaskState(sessionId: string): Promise<TaskStateResponse | null> {
  const result = await fetchWithErrorHandling<TaskStateResponse>(
    `${BASE_URL}/session/${sessionId}/task-state`
  );
  return result.ok ? result.data : null;
}

export async function sendUserSteering(
  sessionId: string,
  taskId: string,
  text: string,
  activeTasks: Array<{ task_id: string; description: string | null; status: string }>
): Promise<boolean> {
  return steerTask(sessionId, taskId, 'USER_MESSAGE', {
    text,
    active_tasks: activeTasks,
  });
}
```

---

### Phase 5: Frontend - Session Store Enhancement

**File: `playground_ui/src/lib/stores/features/session.svelte.ts`**

Add reactive task state:

```typescript
interface SessionState {
  sessionId: string;
  isSending: boolean;
  activeTraceId: string | null;
  // NEW
  foregroundTaskId: string | null;
  foregroundStatus: string | null;
  hasRunningForeground: boolean;
  hasActiveBackgroundTasks: boolean;
}

// Computed property
get hasRunningForeground(): boolean {
  return this.foregroundTaskId !== null && this.foregroundStatus === 'RUNNING';
}

// Method to refresh task state
async refreshTaskState(): Promise<void> {
  const state = await getTaskState(this.sessionId);
  if (state) {
    this.foregroundTaskId = state.foreground_task_id;
    this.foregroundStatus = state.foreground_status;
    this.hasActiveBackgroundTasks = state.background_tasks.length > 0;
  }
}
```

---

### Phase 6: Frontend - Chat Input Enhancement

**File: `playground_ui/src/lib/components/features/chat/ChatInput.svelte`**

```svelte
<script lang="ts">
  import { getChatStore, getSessionStore, getTasksStore } from '$lib/stores';
  import { sendUserSteering } from '$lib/services/api';

  interface Props {
    onsubmit: () => void;
  }

  let { onsubmit }: Props = $props();
  const chatStore = getChatStore();
  const sessionStore = getSessionStore();
  const tasksStore = getTasksStore();

  // Determine button mode
  const isSteerMode = $derived(sessionStore.hasRunningForeground);
  const buttonLabel = $derived(isSteerMode ? 'Steer' : 'Send');
  const buttonClass = $derived(isSteerMode ? 'steer-btn' : 'send-btn');

  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = async () => {
    const text = chatStore.input.trim();
    if (!text) return;

    if (isSteerMode && sessionStore.foregroundTaskId) {
      // Steer mode: send USER_MESSAGE to running foreground
      const backgroundTasks = tasksStore.tasks
        .filter(t => t.task_type === 'BACKGROUND' && ['RUNNING', 'PENDING', 'PAUSED'].includes(t.status))
        .map(t => ({
          task_id: t.task_id,
          description: t.description,
          status: t.status,
        }));

      chatStore.clearInput();
      chatStore.addUserMessage(text, { isSteeringMessage: true });

      await sendUserSteering(
        sessionStore.sessionId,
        sessionStore.foregroundTaskId,
        text,
        backgroundTasks
      );
    } else {
      // Normal mode: start new chat
      onsubmit();
    }
  };
</script>

<div class="chat-input">
  <textarea
    placeholder={isSteerMode ? "Send feedback to the running task..." : "Ask your agent something..."}
    bind:value={chatStore.input}
    onkeydown={handleKeydown}
  ></textarea>
  <button
    class={buttonClass}
    onclick={handleSubmit}
    disabled={!chatStore.input.trim()}
  >
    {buttonLabel}
  </button>
</div>

<style>
  /* ... existing styles ... */

  .steer-btn {
    width: 64px;
    height: 44px;
    border-radius: var(--radius-lg);
    background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
    color: white;
    font-size: 13px;
    font-weight: 600;
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .steer-btn:hover {
    background: linear-gradient(135deg, #d97706 0%, #b45309 100%);
  }

  .steer-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
```

---

### Phase 7: Frontend - Visual Feedback

**File: `playground_ui/src/lib/components/features/chat/ChatBody.svelte`**

Add visual indicator for steering messages:

```svelte
{#each chatStore.messages as message}
  <div class="message {message.role}" class:steering={message.isSteeringMessage}>
    {#if message.isSteeringMessage}
      <span class="steering-badge">Steering</span>
    {/if}
    <!-- ... rest of message rendering ... -->
  </div>
{/each}

<style>
  .message.steering {
    border-left: 3px solid #f59e0b;
    background: linear-gradient(90deg, rgba(245, 158, 11, 0.05) 0%, transparent 100%);
  }

  .steering-badge {
    font-size: 10px;
    font-weight: 600;
    color: #d97706;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
    display: block;
  }
</style>
```

---

### Phase 8: Prompt Engineering

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

### Phase 9: Steering Modal Notification

**File: `playground_ui/src/lib/components/features/chat/SteeringModal.svelte`**

```svelte
<script lang="ts">
  import { getSessionStore } from '$lib/stores';

  const sessionStore = getSessionStore();

  // Auto-dismiss when steering is acknowledged
  $effect(() => {
    if (!sessionStore.awaitingSteeringAck) {
      // Modal dismissed by acknowledgment
    }
  });

  const dismiss = () => {
    sessionStore.awaitingSteeringAck = false;
  };
</script>

{#if sessionStore.awaitingSteeringAck}
  <div class="steering-modal-overlay">
    <div class="steering-modal">
      <div class="spinner"></div>
      <p class="title">Steering message sent</p>
      <p class="subtitle">Waiting for agent to acknowledge...</p>
      <button class="dismiss-btn" onclick={dismiss}>Dismiss</button>
    </div>
  </div>
{/if}

<style>
  .steering-modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.3);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .steering-modal {
    background: white;
    border-radius: var(--radius-xl);
    padding: var(--space-xl);
    text-align: center;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    max-width: 320px;
  }

  .spinner {
    width: 32px;
    height: 32px;
    border: 3px solid #e5e7eb;
    border-top-color: #f59e0b;
    border-radius: 50%;
    animation: spin 1s linear infinite;
    margin: 0 auto var(--space-md);
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .title {
    font-weight: 600;
    color: var(--color-text);
    margin-bottom: var(--space-xs);
  }

  .subtitle {
    font-size: 13px;
    color: var(--color-text-muted);
    margin-bottom: var(--space-md);
  }

  .dismiss-btn {
    font-size: 12px;
    color: var(--color-text-muted);
    background: none;
    border: none;
    cursor: pointer;
    text-decoration: underline;
  }
</style>
```

**Update `session.svelte.ts`:**

```typescript
interface SessionState {
  // ... existing ...
  awaitingSteeringAck: boolean;
}

// Set when steering sent
awaitingSteeringAck = true;

// Clear when steering_received event arrives or agent responds
```

**Update `session-stream.ts` to detect acknowledgment:**

```typescript
// When receiving planner events, check for steering acknowledgment
if (update.extra?.event_type === 'steering_received') {
  sessionStore.awaitingSteeringAck = false;
}
```

---

### Phase 10: Configurable Steering Queue Limit

**File: `penguiflow/cli/spec.py`** (update PlannerBackgroundTasksSpec)

```python
class PlannerBackgroundTasksSpec(BaseModel):
    # ... existing fields ...
    max_pending_steering: int = Field(default=2, ge=1, le=10)
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

**File: Config templates** (add to config.py.jinja)

```jinja
{% if with_background_tasks %}
    # ... existing background_tasks fields ...
    background_tasks_max_pending_steering: int = 2
{% endif %}
```

---

### Phase 11: Session Stream Integration

**File: `playground_ui/src/lib/services/session-stream.ts`**

Update to track foreground task state:

```typescript
// When receiving STATUS_CHANGE updates, check if it's the foreground task
if (update.update_type === 'STATUS_CHANGE') {
  const content = update.content as { status: string; task_type?: string };

  // Update session store's foreground tracking
  if (content.task_type === 'FOREGROUND') {
    if (content.status === 'RUNNING') {
      sessionStore.foregroundTaskId = update.task_id;
      sessionStore.foregroundStatus = 'RUNNING';
    } else if (['COMPLETE', 'FAILED', 'CANCELLED'].includes(content.status)) {
      if (sessionStore.foregroundTaskId === update.task_id) {
        sessionStore.foregroundTaskId = null;
        sessionStore.foregroundStatus = null;
      }
    }
  }
}
```

---

## Testing Plan

### Unit Tests

1. **steering.py**: Test USER_MESSAGE validation
2. **react_runtime.py**: Test USER_MESSAGE injection format
3. **playground.py**: Test `/session/{id}/task-state` endpoint

### Integration Tests

1. Send USER_MESSAGE while foreground running → verify injection
2. Send USER_MESSAGE with ambiguous task reference → verify HITL triggered
3. Send message with only background tasks → verify new foreground spawned

### E2E Tests

1. Start long-running task → send steering → verify LLM receives it
2. Spawn background task → send "cancel the task" → verify select_option appears
3. Complete foreground → verify button changes back to "Send"

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
| 3 | `playground.py` | Add `/session/{id}/task-state` endpoint |
| 4 | `api.ts` | Add `getTaskState()`, `sendUserSteering()` |
| 5 | `session.svelte.ts` | Track foreground task state + `awaitingSteeringAck` |
| 6 | `ChatInput.svelte` | Steer mode with orange button, different placeholder |
| 7 | `ChatBody.svelte` | Visual indicator (orange left border) for steering messages |
| 8 | `prompts.py` | Steering interpretation guidance for HITL disambiguation |
| 9 | `SteeringModal.svelte` | Modal notification while awaiting acknowledgment |
| 10 | `steering.py` + `spec.py` | Configurable `max_pending_steering` (default: 2) |
| 11 | `session-stream.ts` | Real-time foreground state + steering ack detection |

---

## Design Decisions (Resolved)

1. **Steering queue overflow**: Configurable cap on pending steering messages.
   - Default: 2 messages max
   - Configurable via `background_tasks_max_pending_steering: int = 2`
   - Oldest messages dropped if exceeded (or reject with user feedback)

2. **Latency feedback**: Modal notification while waiting for LLM acknowledgment.
   - Show modal: "Steering message sent. Waiting for agent to acknowledge..."
   - Dismiss automatically when agent responds (steering_received event or text acknowledgment)
   - User can dismiss manually if needed

3. **Direct background routing**: No. Always route through foreground.
   - Even if user clearly targets a background task, spawn/use foreground
   - Foreground LLM provides acknowledgment with creative details
   - Example: "Got it! I'm checking on the market research task now... It's currently 60% complete, analyzing competitor pricing data."
   - This maintains supervision model and provides better UX (conversational acknowledgment)
