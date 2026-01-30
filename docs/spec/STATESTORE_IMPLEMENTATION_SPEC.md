# StateStore Implementation Specification

> **Version:** 2.11.x (current: 2.11.5)
> **Audience:** Downstream teams implementing custom StateStore backends
> **Last Updated:** January 2026

This document provides a complete specification for implementing a StateStore backend compatible with PenguiFlow. It covers the protocol definition, data types, method contracts, integration points, distributed deployment patterns, and production best practices.

> **Forward-looking backlog:** For known gaps and the roadmap to make this surface even more robust, see `docs/RFC/ToDo/RFC_STATESTORE_STANDARD_FOLLOWUPS.md`.

---

## Table of Contents

1. [Overview](#overview)
2. [Protocol Definition](#protocol-definition)
3. [Data Types](#data-types)
4. [Required Methods](#required-methods)
5. [Optional Duck-Typed Methods](#optional-duck-typed-methods)
6. [Context Versioning & Divergence Detection](#context-versioning--divergence-detection)
7. [Steering Payload Sanitization](#steering-payload-sanitization)
8. [Merge Strategies](#merge-strategies)
9. [Memory Health States & Recovery](#memory-health-states--recovery)
10. [Artifact Store](#artifact-store)
11. [Integration Points](#integration-points)
12. [Distributed Deployment](#distributed-deployment)
13. [Method Contracts & Semantics](#method-contracts--semantics)
14. [Reference Implementations](#reference-implementations)
15. [Compatibility Adapters](#compatibility-adapters)
16. [Telemetry Integration](#telemetry-integration)
17. [Database Schema Recommendations](#database-schema-recommendations)
18. [Testing Your Implementation](#testing-your-implementation)
19. [Error Handling](#error-handling)
20. [Performance Considerations](#performance-considerations)
21. [Implementation Checklist](#implementation-checklist)

---

## Overview

### What is StateStore?

`StateStore` is a **Protocol** (duck-typed interface) that enables PenguiFlow to persist:

- **Runtime events** (node execution, errors, retries) for audit/replay
- **Remote bindings** (A2A worker associations) for distributed flows
- **Planner pause state** (OAuth/HITL flows) for distributed resume
- **Memory state** (conversation history) for session persistence
- **Task lifecycle** (foreground/background tasks) for session management
- **Steering events** (user interventions) for bidirectional control
- **Trajectories** (execution history) for replay and debugging
- **Artifacts** (binary/large-text outputs) for rich tool results

### Why Implement a StateStore?

| Feature | Without StateStore | With StateStore |
|---------|-------------------|-----------------|
| Event audit trail | In-memory only, lost on restart | Persisted, queryable |
| Trace replay | Not available | Full replay capability |
| Distributed resume (OAuth/HITL) | Single-worker only | Multi-worker support |
| Session memory persistence | In-memory, per-instance | Shared across instances |
| Remote binding tracking | Not tracked | Persisted for debugging |
| Background task recovery | Lost on restart | Hydrated from storage |
| Steering event history | Ephemeral | Full audit trail |

### Design Philosophy

1. **Optional but recommended** - Flows work without a StateStore, but persistence features require one
2. **Protocol-based (duck-typing)** - No inheritance required; just implement the methods
3. **Fail-safe where it matters** - Core runtime audit-log persistence is best-effort (errors are logged and execution continues). Session/Planner subsystems *expect stores to be reliable*, so downstream implementations should treat all methods as "must not throw" and instead degrade gracefully (timeouts, circuit breakers, dead-letter queues, etc.)
4. **Async-only** - All methods must be async/awaitable
5. **Idempotent writes** - `save_event` may be called multiple times for the same event (retries)
6. **Capability detection** - Optional features detected via `hasattr()` at runtime

---

## Protocol Definition

**Location:** `penguiflow/state/protocol.py` (import path remains `penguiflow.state`)

```python
from typing import Protocol, Sequence, Any, Mapping, runtime_checkable
from dataclasses import dataclass

@runtime_checkable
class StateStore(Protocol):
    """Protocol for durable state adapters used by PenguiFlow."""

    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime event."""
        ...

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Return the ordered history for a trace id."""
        ...

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Persist the mapping between a trace and an external worker."""
        ...
```

### Optional Capability Protocols

PenguiFlow defines additional protocols for optional features:

```python
@runtime_checkable
class SupportsPlannerState(Protocol):
    async def save_planner_state(self, token: str, payload: dict[str, Any]) -> None: ...
    async def load_planner_state(self, token: str) -> dict[str, Any] | None: ...

@runtime_checkable
class SupportsMemoryState(Protocol):
    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None: ...
    async def load_memory_state(self, key: str) -> dict[str, Any] | None: ...

@runtime_checkable
class SupportsTasks(Protocol):
    async def save_task(self, state: TaskState) -> None: ...
    async def list_tasks(self, session_id: str) -> Sequence[TaskState]: ...
    async def save_update(self, update: StateUpdate) -> None: ...
    async def list_updates(self, session_id: str, *, task_id: str | None = None,
                           since_id: str | None = None, limit: int = 500) -> Sequence[StateUpdate]: ...

@runtime_checkable
class SupportsSteering(Protocol):
    async def save_steering(self, event: SteeringEvent) -> None: ...
    async def list_steering(self, session_id: str, *, task_id: str | None = None,
                            since_id: str | None = None, limit: int = 500) -> Sequence[SteeringEvent]: ...

@runtime_checkable
class SupportsTrajectories(Protocol):
    async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None: ...
    async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None: ...
    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]: ...

@runtime_checkable
class SupportsPlannerEvents(Protocol):
    async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None: ...
    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]: ...

@runtime_checkable
class SupportsArtifacts(Protocol):
    @property
    def artifact_store(self) -> ArtifactStore | None: ...
```

### Protocol Checking

PenguiFlow validates StateStore implementations at runtime. The admin CLI (`penguiflow-admin`) uses this check:

```python
required = ("save_event", "load_history", "save_remote_binding")
if not all(hasattr(instance, attr) for attr in required):
    raise TypeError("StateStore must implement required methods")
```

### Capability Detection Helpers

```python
from penguiflow.state import missing_capabilities, require_capabilities

# Check which methods are missing
missing = missing_capabilities(store, ["save_task", "list_tasks"])

# Fail fast if critical methods are missing
require_capabilities(store, feature="sessions", methods=["save_task", "list_tasks"])
```

---

## Data Types

### StoredEvent

Represents a persisted runtime event. Created via `StoredEvent.from_flow_event()`.

```python
@dataclass(slots=True)
class StoredEvent:
    trace_id: str | None       # Trace identifier (nullable for global events)
    ts: float                  # Unix timestamp (time.time())
    kind: str                  # Event type (see Event Types below)
    node_name: str | None      # Node that emitted the event
    node_id: str | None        # Unique node instance ID
    payload: Mapping[str, Any] # Structured event data

    @classmethod
    def from_flow_event(cls, event: FlowEvent) -> StoredEvent:
        """Factory method to convert FlowEvent to StoredEvent."""
```

#### Event Types (`kind` field)

`StoredEvent.kind` is a **string discriminator**. In v2.11.x, PenguiFlow persists multiple *families* of events.

Important: `kind` values are an **open set**. StateStore backends MUST accept novel/unknown kinds and persist them as opaque strings.

1) **Core runtime events** (`FlowEvent.event_type`): emitted by the `PenguiFlow` runtime around node execution and trace lifecycle.

| Event Kind | Description |
|------------|-------------|
| `node_start` | Node began processing |
| `node_success` | Node completed successfully |
| `node_timeout` | Node exceeded deadline/budget |
| `node_retry` | Node retry cycle started |
| `node_error` | Node raised an exception |
| `node_failed` | Node failure after retries exhausted |
| `node_cancelled` | Node cancelled locally (steering/budget) |
| `node_trace_cancelled` | Node skipped because trace is cancelled |
| `deadline_skip` | Work skipped due to deadline pre-check |
| `trace_cancel_start` | Trace cancellation started |
| `trace_cancel_finish` | Trace cancellation finished |
| `trace_cancel_drop` | Cancellation requested for unknown/finished trace |

2) **Remote transport events** (`FlowEvent.event_type`): emitted by the runtime when a `RemoteNode` performs remote calls/streams.

| Event Kind | Description |
|------------|-------------|
| `remote_call_start` | Remote call initiated |
| `remote_call_success` | Remote call completed successfully |
| `remote_call_error` | Remote call failed |
| `remote_call_cancelled` | Remote call cancelled due to trace cancellation |
| `remote_cancel_error` | Failed to cancel a remote task |
| `remote_stream_event` | Remote streaming event observed |

3) **Session/task pseudo-events**: when a session store is adapted onto a core `StateStore`, it persists session state as audit-log entries under `trace_id="session:{session_id}"`.

| Event Kind | Description |
|------------|-------------|
| `session.task` | Task lifecycle snapshot persisted |
| `session.update` | Task streaming/progress update persisted |
| `session.steering` | Steering event persisted |

4) **Planner/tool streaming events are *not* `StoredEvent`s.** They are `PlannerEvent`s (see `SupportsPlannerEvents`) and have their own `event_type` values such as `stream_chunk`, `llm_stream_chunk`, and `artifact_chunk`.

> **Callout (important):** If you want to persist tool/LLM streaming events for the Playground UI or trace replay, implement `SupportsPlannerEvents` (`save_planner_event` / `list_planner_events`). Do **not** attempt to cram these into `save_event` as `StoredEvent.kind="stream_chunk"`; PenguiFlow treats those as different channels.

#### Payload Structure

The `payload` dict contains event-specific fields. Common fields:

```python
{
    "ts": 1702857600.123,          # Timestamp
    "event": "node_success",       # Event type (same as kind)
    "node_name": "llm_node",       # Node name
    "node_id": "llm_node_abc123",  # Node instance ID
    "trace_id": "trace_xyz",       # Trace ID
    "latency_ms": 1523.45,         # Execution latency
    "q_depth_in": 0,               # Input queue depth
    "q_depth_out": 2,              # Output queue depth
    "q_depth_total": 2,            # Combined queue depth
    "outgoing": 3,                 # Number of downstream edges
    "queue_maxsize": 1000,         # Queue capacity
    "attempt": 1,                  # Retry attempt number
    "trace_inflight": 5,           # Messages in-flight for trace
    "trace_cancelled": False,      # Whether trace is cancelled
    # Error-specific (when kind="node_error"):
    "flow_error": {
        "code": "TOOL_EXECUTION_FAILED",
        "message": "API returned 500",
        # Additional provider-specific fields may be included (exception type, stack summary, etc.)
    }
}
```

### RemoteBinding

Associates a trace with a remote A2A worker.

```python
@dataclass(slots=True)
class RemoteBinding:
    trace_id: str           # Trace being processed
    context_id: str | None  # Optional context within trace
    task_id: str            # A2A task ID
    agent_url: str          # Remote worker URL
```

### TaskState

Represents the complete state of a foreground or background task.

```python
@dataclass(slots=True)
class TaskState:
    task_id: str                           # Unique task identifier
    session_id: str                        # Session this task belongs to
    status: TaskStatus                     # Current lifecycle state
    task_type: TaskType                    # FOREGROUND or BACKGROUND
    priority: int                          # Scheduling priority (higher = more urgent)
    context_snapshot: TaskContextSnapshot  # Full execution context at spawn time
    trace_id: str | None = None            # Distributed tracing ID
    result: Any | None = None              # Task result payload
    error: str | None = None               # Error message if failed
    description: str | None = None         # Human-readable task description
    progress: dict[str, Any] | None = None # Progress tracking metadata
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def update_status(self, status: TaskStatus) -> None:
        """Update status and refresh updated_at timestamp."""
        self.status = status
        self.updated_at = _utc_now()
```

#### TaskStatus Enum

```python
class TaskStatus(str, Enum):
    PENDING = "PENDING"       # Awaiting execution
    RUNNING = "RUNNING"       # Currently executing
    PAUSED = "PAUSED"         # Paused for approval
    COMPLETE = "COMPLETE"     # Completed successfully
    FAILED = "FAILED"         # Execution failed
    CANCELLED = "CANCELLED"   # Cancelled by user/parent
```

#### TaskType Enum

```python
class TaskType(str, Enum):
    FOREGROUND = "FOREGROUND"  # Interactive, blocks session
    BACKGROUND = "BACKGROUND"  # Async, runs in parallel
```

### TaskContextSnapshot

Captures the full execution context at task spawn time. Used for context isolation and divergence detection.

```python
class TaskContextSnapshot(BaseModel):
    session_id: str                                    # Session identifier
    task_id: str                                       # Task identifier
    trace_id: str | None = None                        # Distributed tracing ID
    spawned_from_task_id: str = "foreground"           # Parent task ID
    spawned_from_event_id: str | None = None           # Triggering event ID
    spawned_at: datetime = Field(default_factory=_utc_now)
    spawn_reason: str | None = None                    # Why task was created
    query: str | None = None                           # User query if applicable
    propagate_on_cancel: str = "cascade"               # "cascade" or "isolate"
    notify_on_complete: bool = True                    # Emit notification on completion

    # Context versioning for divergence detection
    context_version: int | None = None                 # Version at spawn time
    context_hash: str | None = None                    # SHA256 hash at spawn time

    # Deep copies of context at spawn time
    llm_context: dict[str, Any] = Field(default_factory=dict)
    tool_context: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
```

### StateUpdate

Represents a streaming progress update from a task.

```python
class StateUpdate(BaseModel):
    session_id: str                        # Session identifier
    task_id: str                           # Task identifier
    trace_id: str | None = None            # Distributed tracing ID
    update_id: str                         # UUID for deduplication
    update_type: UpdateType                # Type of update
    content: Any                           # Dynamic payload
    step_index: int | None = None          # Current step number
    total_steps: int | None = None         # Total expected steps
    created_at: datetime = Field(default_factory=_utc_now)
```

#### UpdateType Enum

```python
class UpdateType(str, Enum):
    THINKING = "THINKING"           # Internal agent reasoning
    PROGRESS = "PROGRESS"           # Step-by-step progress
    TOOL_CALL = "TOOL_CALL"         # Tool invocation events
    RESULT = "RESULT"               # Final results or answer chunks
    ERROR = "ERROR"                 # Error occurred
    CHECKPOINT = "CHECKPOINT"       # Approval/pause points
    STATUS_CHANGE = "STATUS_CHANGE" # Task status transitions
    NOTIFICATION = "NOTIFICATION"   # UI notifications
```

### SteeringEvent

Represents a bidirectional control event (user intervention).

```python
class SteeringEvent(BaseModel):
    session_id: str                                   # Session identifier
    task_id: str                                      # Target task
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    event_type: SteeringEventType                     # Type of steering
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None                       # Distributed tracing ID
    source: str = "user"                              # Event origin ("user", "system", "agent")
    created_at: datetime = Field(default_factory=_utc_now)

    def to_injection(self) -> str:
        """Convert to JSON injection for LLM prompt context."""
```

#### SteeringEventType Enum

```python
class SteeringEventType(str, Enum):
    INJECT_CONTEXT = "INJECT_CONTEXT"   # Inject contextual information
    REDIRECT = "REDIRECT"               # Change execution goal/instruction
    CANCEL = "CANCEL"                   # Cancel execution with optional reason
    PRIORITIZE = "PRIORITIZE"           # Adjust task priority
    PAUSE = "PAUSE"                     # Pause execution
    RESUME = "RESUME"                   # Resume paused execution
    APPROVE = "APPROVE"                 # Approve pending decision/patch
    REJECT = "REJECT"                   # Reject pending decision/patch
    USER_MESSAGE = "USER_MESSAGE"       # User sends message while task is working
```

### FlowEvent (Source Type)

The runtime emits `FlowEvent` objects which are converted to `StoredEvent` via the factory method:

```python
@dataclass(frozen=True, slots=True)
class FlowEvent:
    event_type: str
    ts: float
    node_name: str | None
    node_id: str | None
    trace_id: str | None
    attempt: int
    latency_ms: float | None
    queue_depth_in: int
    queue_depth_out: int
    outgoing_edges: int
    queue_maxsize: int
    trace_pending: int | None
    trace_inflight: int
    trace_cancelled: bool
    extra: Mapping[str, Any]  # Extension fields
```

---

## Required Methods

### 1. `save_event(event: StoredEvent) -> None`

**Purpose:** Persist a runtime event for audit/replay.

**Contract:**
- MUST be idempotent (duplicate calls for same event should not fail)
- MUST NOT block indefinitely (use timeouts)
- SHOULD handle `trace_id=None` for global events (recommended: normalise to a sentinel like `"__global__"`)
- MAY store events asynchronously (eventual consistency acceptable)

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `StoredEvent` | The event to persist |

**Example Implementation:**
```python
async def save_event(self, event: StoredEvent) -> None:
    trace_id = event.trace_id or "__global__"
    raw = json.dumps(
        {
            "trace_id": trace_id,
            "ts": event.ts,
            "kind": event.kind,
            "node_name": event.node_name,
            "node_id": event.node_id,
            "payload": dict(event.payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    event_fp = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    await self._pool.execute(
        """
        INSERT INTO flow_events (trace_id, ts, kind, node_name, node_id, event_fp, payload)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (trace_id, event_fp) DO NOTHING  -- Idempotent
        """,
        trace_id,
        event.ts,
        event.kind,
        event.node_name,
        event.node_id,
        event_fp,
        json.dumps(dict(event.payload)),
    )
```

### 2. `load_history(trace_id: str) -> Sequence[StoredEvent]`

**Purpose:** Retrieve all events for a trace, ordered by timestamp.

**Contract:**
- MUST return events in chronological order (ascending `ts`)
- MUST return empty sequence if trace not found (NOT raise exception)
- MUST return a `Sequence[StoredEvent]` (list, tuple, etc.)

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `trace_id` | `str` | The trace identifier to query |

**Returns:** `Sequence[StoredEvent]` - Ordered list of events

**Example Implementation:**
```python
async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
    rows = await self._pool.fetch(
        """
        SELECT trace_id, ts, kind, node_name, node_id, payload
        FROM flow_events
        WHERE trace_id = $1
        ORDER BY ts ASC, id ASC  -- Secondary sort for same-ts events
        """,
        trace_id,
    )
    return [
        StoredEvent(
            trace_id=row["trace_id"],
            ts=row["ts"],
            kind=row["kind"],
            node_name=row["node_name"],
            node_id=row["node_id"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
        )
        for row in rows
    ]
```

### 3. `save_remote_binding(binding: RemoteBinding) -> None`

**Purpose:** Persist A2A remote worker associations.

**Contract:**
- MUST be idempotent (same binding may be saved multiple times)
- SHOULD support upsert semantics (update if exists)
- MAY use composite key of `(trace_id, context_id, task_id)`

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `binding` | `RemoteBinding` | The binding to persist |

**Example Implementation:**
```python
async def save_remote_binding(self, binding: RemoteBinding) -> None:
    await self._pool.execute(
        """
        INSERT INTO remote_bindings (trace_id, context_id, task_id, agent_url)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (trace_id, task_id) DO UPDATE
        SET agent_url = EXCLUDED.agent_url,
            context_id = EXCLUDED.context_id
        """,
        binding.trace_id,
        binding.context_id,
        binding.task_id,
        binding.agent_url,
    )
```

---

## Optional Duck-Typed Methods

These methods are **not part of the Protocol** but are detected at runtime via `getattr()`. Implement them to enable advanced features.

### 4. `save_planner_state(token: str, payload: dict) -> None`

**Purpose:** Persist planner pause state for distributed resume (OAuth/HITL).

**When Called:** When `ReactPlanner` pauses execution (e.g., waiting for OAuth callback).

**Contract:**
- MUST be idempotent (token is unique per pause)
- SHOULD set expiration (recommended: 1 hour)
- MUST serialize `payload` as JSON

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `token` | `str` | Unique pause token (UUID format) |
| `payload` | `dict` | Serialized pause state |

**Payload Structure:**
```python
{
    "trajectory": {...},           # Trajectory.serialise() payload (includes steps + context)
    "reason": "await_input",       # Pause reason string
    "payload": {...},              # Caller-provided pause payload (OAuth/HITL metadata)
    "constraints": {...} | None,   # Constraint tracker snapshot (optional)
    "tool_context": {...} | None,  # JSON-serialisable tool_context snapshot (optional)
}
```

> **Callout (gold standard):** Treat the pause payload as a *wire format*, not an internal object dump.
> The runtime currently persists `Trajectory.serialise()` plus minimal metadata (see `penguiflow/planner/pause_management.py`).
> Downstream stores should store and return the payload **losslessly** (no lossy JSON coercion beyond ensuring JSON-serialisability).

**Example Implementation:**
```python
async def save_planner_state(self, token: str, payload: dict) -> None:
    await self._pool.execute(
        """
        INSERT INTO planner_pauses (token, payload, expires_at)
        VALUES ($1, $2, NOW() + INTERVAL '1 hour')
        ON CONFLICT (token) DO UPDATE
        SET payload = EXCLUDED.payload,
            expires_at = NOW() + INTERVAL '1 hour'
        """,
        token,
        json.dumps(payload),
    )
```

### 5. `load_planner_state(token: str) -> dict | None`

**Purpose:** Load planner pause state for resume.

**When Called:** When `ReactPlanner.resume(token)` is called after OAuth/HITL completion.

**Contract:**
- SHOULD return `None` if token not found or expired
- MUST NOT raise exceptions for missing tokens
- SHOULD delete/expire token after successful load (one-time use)

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `token` | `str` | The pause token to load |

**Returns:** `dict | None` - The stored payload, or `None` if not found

> **Callout (missing token semantics):** `ReactPlanner` treats `None` as the clean "not found/expired" signal.
> Returning `{}` for missing tokens tends to produce confusing downstream errors (`KeyError: 'trajectory'`) instead of a
> clear missing-token path. Prefer returning `None`.

**Example Implementation:**
```python
async def load_planner_state(self, token: str) -> dict | None:
    row = await self._pool.fetchrow(
        """
        DELETE FROM planner_pauses
        WHERE token = $1 AND expires_at > NOW()
        RETURNING payload
        """,
        token,
    )
    if not row:
        return None
    return json.loads(row["payload"])
```

### 6. `save_memory_state(key: str, state: dict[str, Any]) -> None`

**Purpose:** Persist short-term memory state for session continuity.

**When Called:** After each planner run when `short_term_memory` is configured.

**Contract:**
- MUST be idempotent (same key can be saved multiple times)
- SHOULD use upsert semantics
- Key format: `"{tenant_id}:{user_id}:{session_id}"` (composite key)

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | `str` | Composite memory key |
| `state` | `dict[str, Any]` | Memory state to persist |

**State Structure:**
```python
{
    "version": 1,                  # Schema version
    "health": "healthy",           # Memory health state
    "summary": "...",              # Rolling summary (if using rolling_summary strategy)
    "turns": [                     # Recent conversation turns
        {
            "user_message": "...",
            "assistant_response": "...",
            "trajectory_digest": {...},
            "ts": 1702857600.0
        }
    ],
    "pending": [...],              # Turns awaiting summarization
    "backlog": [...],              # Recovery buffer during degradation
    "config_snapshot": {...}       # Configuration at save time
}
```

**Example Implementation:**
```python
async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
    await self._pool.execute(
        """
        INSERT INTO memory_states (key, state, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE
        SET state = EXCLUDED.state,
            updated_at = NOW()
        """,
        key,
        json.dumps(state),
    )
```

### 7. `load_memory_state(key: str) -> dict[str, Any] | None`

**Purpose:** Load persisted memory state for session hydration.

**When Called:** At the start of each planner run when `short_term_memory` is configured.

**Contract:**
- MUST return `None` if key not found (NOT raise exception)
- SHOULD return the exact state that was saved

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | `str` | Composite memory key |

**Returns:** `dict[str, Any] | None` - The stored state, or `None` if not found

**Example Implementation:**
```python
async def load_memory_state(self, key: str) -> dict[str, Any] | None:
    row = await self._pool.fetchrow(
        "SELECT state FROM memory_states WHERE key = $1",
        key,
    )
    if not row:
        return None
    return json.loads(row["state"])
```

---

### 8. Task Persistence (`save_task`, `list_tasks`, `save_update`, `list_updates`)

**Purpose:** Persist background/foreground task lifecycle state and streaming updates.

**Location (integration):** `penguiflow/sessions/session.py`

**Contracts:**
- `save_task()` MUST be idempotent or upsert-safe (same task is updated many times).
- `list_tasks()` MUST return all tasks for the session (order not important).
- `save_update()` SHOULD be append-only, idempotent via `update_id` if possible.
- `list_updates()` SHOULD return updates in stable order and treat `since_id` as an *exclusive* cursor.
- Reads MUST return an empty sequence on "not found" (do not raise).

**Signature:**
```python
async def save_task(self, state: TaskState) -> None: ...
async def list_tasks(self, session_id: str) -> Sequence[TaskState]: ...
async def save_update(self, update: StateUpdate) -> None: ...
async def list_updates(
    self,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> Sequence[StateUpdate]: ...
```

**Notes:**
- `StateUpdate.update_id` is generated client-side; use it as the natural idempotency key.
- If `since_id` is unknown, treat it as "no cursor".
- To preserve cursor semantics, avoid filtering after applying limit.

### 9. Steering Persistence (`save_steering`, `list_steering`)

**Purpose:** Persist bidirectional steering/intervention events (USER_MESSAGE, CANCEL, APPROVE, etc.).

**Location (integration):** `penguiflow/sessions/session.py`

**Contracts:**
- Steering payloads are untrusted; persist the sanitized form (see [Steering Payload Sanitization](#steering-payload-sanitization)).
- `list_steering()` SHOULD return events in stable order and treat `since_id` as an *exclusive* cursor.

**Signature:**
```python
async def save_steering(self, event: SteeringEvent) -> None: ...
async def list_steering(
    self,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> Sequence[SteeringEvent]: ...
```

### 10. Trajectory Persistence (`save_trajectory`, `get_trajectory`, `list_traces`)

**Purpose:** Persist planner trajectories for replay, debugging, and UI inspection.

**Location (integration):** `penguiflow/cli/playground.py`

**Contracts:**
- `get_trajectory()` MUST return `None` when not found or when session_id does not match.
- `list_traces()` MUST return trace IDs for the session, most recent first.

**Signature:**
```python
async def save_trajectory(self, trace_id: str, session_id: str, trajectory: Trajectory) -> None: ...
async def get_trajectory(self, trace_id: str, session_id: str) -> Trajectory | None: ...
async def list_traces(self, session_id: str, limit: int = 50) -> list[str]: ...
```

### 11. Planner Event Persistence (`save_planner_event`, `list_planner_events`)

**Purpose:** Persist structured planner/tool execution events for UI streaming and auditing.

**Contract:**
- Planner events are append-only.
- If your backend supports it, store an insertion sequence to preserve stable ordering.

**Signature:**
```python
async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None: ...
async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]: ...
```

---

## Context Versioning & Divergence Detection

Background tasks receive a **snapshot** of the session context at spawn time. During execution, the foreground may modify the context. PenguiFlow detects this divergence when merging results.

### SessionContext Fields

```python
@dataclass(slots=True)
class SessionContext:
    llm_context: dict[str, Any] = field(default_factory=dict)
    tool_context: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0                # Incremented on every update
    context_hash: str | None = None # SHA256 of llm_context
```

### Context Hash Computation

```python
def _hash_context(payload: dict[str, Any]) -> str | None:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

### ContextPatch for Background Task Results

When a background task completes, it creates a `ContextPatch`:

```python
class ContextPatch(BaseModel):
    task_id: str
    spawned_from_event_id: str | None = None
    source_context_version: int | None = None   # Version at spawn time
    source_context_hash: str | None = None      # Hash at spawn time
    context_diverged: bool = False              # Set if divergence detected
    completed_at: datetime = Field(default_factory=_utc_now)

    # Patch content
    digest: list[str] = Field(default_factory=list)          # Summary text
    facts: dict[str, Any] = Field(default_factory=dict)      # Extracted facts
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
```

### Divergence Detection Logic

```python
async def apply_context_patch(self, *, patch: ContextPatch, strategy: MergeStrategy) -> str | None:
    diverged = False

    # Check version mismatch
    if patch.source_context_version is not None:
        if patch.source_context_version != self._context.version:
            diverged = True

    # Check hash mismatch
    if patch.source_context_hash:
        if patch.source_context_hash != self._context.context_hash:
            diverged = True

    if diverged and not patch.context_diverged:
        patch = patch.model_copy(update={"context_diverged": True})
        # Emit warning notification to user
        self._emit_notification("Context changed during background task execution")
```

### Implications for StateStore

When persisting `TaskState`, ensure:
- `TaskContextSnapshot.context_version` and `context_hash` are preserved
- Deep copies of `llm_context`, `tool_context`, `memory`, `artifacts` are stored
- These fields enable divergence detection on resume/hydration

---

## Steering Payload Sanitization

Steering payloads are **untrusted user input**. PenguiFlow sanitizes them before processing and persistence.

### Size Limits

```python
MAX_STEERING_PAYLOAD_BYTES = 16_384  # 16 KB
MAX_STEERING_DEPTH = 6               # Nesting depth
MAX_STEERING_KEYS = 64               # Dict keys per level
MAX_STEERING_LIST_ITEMS = 50         # List items
MAX_STEERING_STRING = 4_096          # String character length
```

### Sanitization Process

1. **Validate JSON serializability**
2. **Truncate nested structures** at configurable depth
3. **Drop keys** beyond limit
4. **Truncate long strings**
5. **Return truncated summary** if final size exceeds bytes limit

### Event-Specific Payload Validation

| Event Type | Required Fields | Optional Fields |
|------------|-----------------|-----------------|
| `INJECT_CONTEXT` | `text` (non-empty string) | `scope` ("foreground"/"task_only"), `severity` ("note"/"correction") |
| `REDIRECT` | `instruction\|goal\|query` (non-empty string) | `constraints` (dict) |
| `CANCEL` | - | `reason` (string), `hard` (bool) |
| `PRIORITIZE` | `priority` (int) | - |
| `APPROVE/REJECT` | `resume_token\|patch_id\|event_id` | `decision` (string) |
| `PAUSE/RESUME` | - | `reason` (string) |
| `USER_MESSAGE` | `text` (non-empty string) | `active_tasks` (list of task_ids) |

### StateStore Implications

- **Always persist sanitized payloads** - never raw user input
- The `InMemoryStateStore` reference implementation sanitizes in `save_steering()`
- Failed validation raises `SteeringValidationError` before persistence

---

## Merge Strategies

When background tasks complete, their results can be merged into the session context using different strategies.

### MergeStrategy Enum

```python
class MergeStrategy(str, Enum):
    APPEND = "append"           # Add to background_results[] list
    REPLACE = "replace"         # Overwrite background_result key
    HUMAN_GATED = "human_gated" # Queue for user approval
```

### APPEND Strategy

Results are appended to `llm_context["background_results"]` as a list:

```python
if "background_results" not in llm_context:
    llm_context["background_results"] = []
llm_context["background_results"].append(patch_content)
```

### REPLACE Strategy

Results overwrite `llm_context["background_result"]` as a single value:

```python
llm_context["background_result"] = patch_content
```

### HUMAN_GATED Strategy

Results are queued for user approval via a `CHECKPOINT` update:

```python
# Store pending patch
self._pending_patches[patch_id] = PendingContextPatch(
    patch=patch,
    strategy=strategy,
    queued_at=datetime.now(UTC),
)

# Emit checkpoint for user approval
self._emit_update(
    update_type=UpdateType.CHECKPOINT,
    content={
        "type": "context_patch_approval",
        "patch_id": patch_id,
        "digest": patch.digest,
        "options": ["approve", "reject"],
    }
)
```

### Handling Approval/Rejection

```python
async def apply_pending_patch(self, patch_id: str, strategy: MergeStrategy | None = None) -> bool:
    pending = self._pending_patches.pop(patch_id, None)
    if pending is None:
        return False
    await self.apply_context_patch(patch=pending.patch, strategy=strategy or MergeStrategy.APPEND)
    return True

async def reject_pending_patch(self, patch_id: str) -> bool:
    pending = self._pending_patches.pop(patch_id, None)
    if pending is None:
        return False
    self._emit_notification(f"Context patch {patch_id} rejected")
    return True
```

---

## Memory Health States & Recovery

The short-term memory system tracks health and supports graceful degradation.

### MemoryHealth Enum

```python
class MemoryHealth(str, Enum):
    HEALTHY = "healthy"       # Summarization working normally
    RETRY = "retry"           # Attempting recovery with backoff
    DEGRADED = "degraded"     # Fallback to truncation only
    RECOVERING = "recovering" # Recovering from backlog
```

### Health Transitions

```
HEALTHY → RETRY (on summarizer failure)
RETRY → DEGRADED (after max retries exceeded)
DEGRADED → RECOVERING (on periodic recovery attempt)
RECOVERING → HEALTHY (on successful backlog summarization)
RECOVERING → DEGRADED (on recovery failure)
```

### Memory Strategies

| Strategy | Description | When to Use |
|----------|-------------|-------------|
| `"none"` | No memory persistence | Stateless agents |
| `"truncation"` | Keep N recent turns, discard older | Simple use cases |
| `"rolling_summary"` | Summarize old turns + keep recent | Long conversations |

### MemoryBudget Configuration

```python
@dataclass
class MemoryBudget:
    full_zone_turns: int = 5          # Recent turns kept in full
    summary_max_tokens: int = 1000    # Summary size limit
    total_max_tokens: int = 10000     # Total memory budget
    overflow_policy: Literal[
        "truncate_summary",           # Drop oldest from summary
        "truncate_oldest",            # Drop oldest turn
        "error"                       # Raise MemoryBudgetExceeded
    ] = "truncate_oldest"
```

### MemoryIsolation (Multi-Tenant)

```python
@dataclass
class MemoryIsolation:
    tenant_key: str = "tenant_id"     # Dot-path in tool_context
    user_key: str = "user_id"
    session_key: str = "session_id"
    require_explicit_key: bool = True  # Fail if key not found
```

Memory key format: `{tenant_id}:{user_id}:{session_id}`

### StateStore Implications

When implementing `save_memory_state`/`load_memory_state`:
- Store the `health` field to track state across sessions
- Preserve `pending` and `backlog` lists for recovery
- Store `config_snapshot` for debugging

---

## Artifact Store

### ArtifactStore Protocol

```python
@runtime_checkable
class ArtifactStore(Protocol):
    async def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef: ...

    async def put_text(
        self,
        text: str,
        *,
        mime_type: str = "text/plain",
        filename: str | None = None,
        namespace: str | None = None,
        scope: ArtifactScope | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef: ...

    async def get(self, artifact_id: str) -> bytes | None: ...
    async def get_ref(self, artifact_id: str) -> ArtifactRef | None: ...
    async def delete(self, artifact_id: str) -> bool: ...
    async def exists(self, artifact_id: str) -> bool: ...
```

### ArtifactRef (Compact Reference)

Only `ArtifactRef` objects (~100 bytes) are passed to LLMs, never raw binary data:

```python
class ArtifactRef(BaseModel):
    id: str                            # Typically "{namespace}_{sha256[:12]}", but not required
    mime_type: str | None = None       # Content type (best-effort)
    size_bytes: int | None = None      # Original content size (best-effort)
    filename: str | None = None        # Suggested download name
    sha256: str | None = None          # Full content hash (best-effort)
    scope: ArtifactScope | None = None # Access control metadata
    source: dict[str, Any] = Field(default_factory=dict)  # Tool name, warnings
```

### ArtifactScope (Access Control)

```python
class ArtifactScope(BaseModel):
    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None
```

**Note:** Scope is stored by ArtifactStore but **enforcement happens at HTTP layer**.

### ArtifactRetentionConfig

```python
class ArtifactRetentionConfig(BaseModel):
    ttl_seconds: int = 3600              # 1-hour default expiration
    max_artifact_bytes: int = 50_000_000 # 50MB per artifact
    max_session_bytes: int = 500_000_000 # 500MB per session
    max_trace_bytes: int = 100_000_000   # 100MB per trace
    max_artifacts_per_trace: int = 100
    max_artifacts_per_session: int = 1000
    cleanup_strategy: Literal["lru", "fifo", "none"] = "lru"
```

### StateStore Integration

The `artifact_store` property provides access to the underlying store:

```python
@property
def artifact_store(self) -> ArtifactStore | None:
    return self._artifact_store
```

Discovery helper:

```python
from penguiflow.artifacts import discover_artifact_store

store = discover_artifact_store(state_store)  # Returns ArtifactStore or None
```

---

## Integration Points

### 1. PenguiFlow Core Runtime

**Location:** `penguiflow/core.py`

```python
flow = PenguiFlow(
    (node1, [node2, node3]),
    state_store=your_store,  # Injected here
)
```

**Integration Behavior:**
- `save_event()` called automatically on every `FlowEvent` emission
- `save_remote_binding()` called when RemoteNode establishes A2A connections
- `load_history()` called via `flow.load_history(trace_id)`
- `save_event()` / `save_remote_binding()` failures are logged and treated as **best-effort** (core runtime continues)
- `load_history()` is an explicit caller action; errors may propagate to the caller (admin/observability tooling should handle exceptions)

### 2. ReactPlanner

**Location:** `penguiflow/planner/react.py`

```python
planner = ReactPlanner(
    llm_client=client,
    catalog=tools,
    state_store=your_store,  # Enables pause/resume and memory
    pause_enabled=True,      # Required for OAuth/HITL
    short_term_memory=ShortTermMemoryConfig(...),  # Enables memory persistence
)
```

**Integration Behavior:**
- `save_planner_state()` called when planner pauses
- `load_planner_state()` called during `planner.resume(token)`
- `save_memory_state()` called after each run (if memory configured)
- `load_memory_state()` called at start of each run (if memory configured)
- Methods detected via `getattr()` - missing methods are skipped (planner continues without that capability)
- Persistence method failures are **logged**; pause records always have an in-memory fallback, but resume may fail if the pause token cannot be loaded

### 3. StreamingSession / SessionManager

**Location:** `penguiflow/sessions/session.py`

**Integration Behavior:**
- `save_task()` called on task lifecycle transitions
- `save_update()` called for streaming updates emitted by tasks
- `save_steering()` called for inbound steering events
- `list_tasks()` used to hydrate a session on startup
- `list_updates()` / `list_steering()` used for polling and UI backfills

### 4. Session Hydration Flow

When a session is created or resumed:

```python
async def hydrate(self) -> None:
    """Restore session state from persistence."""
    if self._hydrated:
        return

    # Load all persisted tasks
    tasks = await self._state_store.list_tasks(self.session_id)

    # Seed task registry
    await self._registry.seed_tasks(tasks)

    # Restore foreground task ID
    for task in tasks:
        if task.task_type == TaskType.FOREGROUND and task.status == TaskStatus.RUNNING:
            self._foreground_task_id = task.task_id
            break

    self._hydrated = True
```

### 5. Admin CLI

**Location:** `penguiflow/admin.py`

```bash
# View trace history
penguiflow-admin history --state-store mypackage.stores:create_store trace_abc123

# Replay trace events
penguiflow-admin replay --state-store mypackage.stores:create_store trace_abc123
```

**Factory Specification:**
- Format: `"module.path:callable_name"`
- Callable must return a StateStore instance (sync or async)
- Example: `"myapp.infrastructure.stores:create_postgres_store"`

---

## Distributed Deployment

### Stateless Worker Pool (Recommended)

For horizontal scaling, use a stateless worker pool pattern:

```python
async def worker_loop(job_queue: JobQueue, state_store: StateStore):
    while True:
        job = await job_queue.dequeue()

        # Fresh flow instance per job
        flow = PenguiFlow(
            build_graph(),
            state_store=state_store,
        )

        try:
            result = await flow.run_once(job.input, trace_id=job.trace_id)
            await job_queue.ack(job.id)
        except Exception as e:
            await job_queue.nack(job.id, error=str(e))
```

**Benefits:**
- Horizontal scalability (10+ workers)
- Easy failure isolation
- No shared state between jobs
- Simple container orchestration

### Long-Lived Flow Worker (Advanced)

For expensive initialization or shared resources:

```python
class FlowWorker:
    def __init__(self, state_store: StateStore):
        self._flow = PenguiFlow(
            build_graph(),
            state_store=state_store,
        )
        self._failure_count = 0
        self._max_failures = 5

    async def process(self, job: Job) -> Result:
        try:
            result = await self._flow.run_once(job.input, trace_id=job.trace_id)
            self._failure_count = 0
            return result
        except Exception:
            self._failure_count += 1
            if self._failure_count >= self._max_failures:
                await self._restart_flow()
            raise

    async def _restart_flow(self) -> None:
        await self._flow.stop()
        self._flow = PenguiFlow(build_graph(), state_store=self._state_store)
        self._failure_count = 0
```

### Multi-Worker Consistency Model

| Data Type | Consistency | Notes |
|-----------|-------------|-------|
| Events (`save_event`) | Eventual | Events may arrive out of order |
| Remote bindings | Eventual | Upsert semantics handle conflicts |
| Planner state (`pause/resume`) | Strong | Must be consistent for distributed resume |
| Memory state | Eventual | Last-write-wins acceptable |
| Task state | Strong | For accurate UI status |
| Steering events | Strong | For real-time interventions |

### Trace-Based Causality

All distributed work is associated via `trace_id`:

```python
# Worker A: Process initial request
result = await flow.run_once(input, trace_id="trace_abc123")

# Worker B: Resume after OAuth callback
result = await planner.resume(token, user_input=oauth_token)
# Internally uses same trace_id from pause record
```

### Cascade Cancellation

Parent task cancellation propagates to children:

```python
async def _cascade_cancel_children(self, parent_task_id: str, reason: str) -> None:
    children = await self._registry.list_children(parent_task_id)
    for child_id in children:
        task = await self._registry.get_task(child_id)
        if task and task.context_snapshot.propagate_on_cancel != "isolate":
            # Create cancellation event
            event = SteeringEvent(
                session_id=self.session_id,
                task_id=child_id,
                event_type=SteeringEventType.CANCEL,
                payload={"reason": reason, "cascaded_from": parent_task_id},
            )
            await self._steer(event)
            # Recurse to grandchildren
            await self._cascade_cancel_children(child_id, reason)
```

---

## Method Contracts & Semantics

### Idempotency Requirements

| Method | Idempotent? | Reason |
|--------|-------------|--------|
| `save_event` | YES | Retries can emit duplicate events |
| `load_history` | YES | Read-only operation |
| `save_remote_binding` | YES | Same binding may be saved multiple times |
| `save_planner_state` | YES | Pause may be saved redundantly |
| `load_planner_state` | NO | Should consume/delete token on read |
| `save_memory_state` | YES | Same key updated repeatedly |
| `load_memory_state` | YES | Read-only operation |
| `save_task` | YES | Task state is updated repeatedly |
| `list_tasks` | YES | Read-only operation |
| `save_update` | YES | Retries can re-emit the same update_id |
| `list_updates` | YES | Read-only operation |
| `save_steering` | YES | Steering events can be retried |
| `list_steering` | YES | Read-only operation |
| `save_trajectory` | YES | Trace can be saved multiple times |
| `get_trajectory` | YES | Read-only operation |
| `list_traces` | YES | Read-only operation |
| `save_planner_event` | YES | Retries can duplicate planner events |
| `list_planner_events` | YES | Read-only operation |
| `artifact_store` | N/A | Property (capability discovery) |

### Consistency Model

- **Eventual consistency is acceptable** for event storage
- **Strong consistency required** for planner state (pause/resume)
- **Eventual consistency acceptable** for memory state (last-write-wins)
- **Strong consistency recommended** for task/steering reads in interactive UIs

### Ordering Guarantees

- `load_history()` MUST return events in `ts` ascending order
- For same-`ts` events, secondary ordering by insertion ID is recommended
- No ordering guarantees for remote bindings
- `list_updates()` / `list_steering()` SHOULD use stable ordering and support cursor semantics via `since_id`
- `list_planner_events()` SHOULD preserve emission order

---

## Reference Implementations

### Minimal In-Memory (Testing)

```python
from collections import defaultdict
from penguiflow.state import StateStore, StoredEvent, RemoteBinding

class InMemoryStateStore(StateStore):
    def __init__(self):
        self._events: dict[str, list[StoredEvent]] = defaultdict(list)
        self._bindings: list[RemoteBinding] = []
        self._planner_state: dict[str, dict] = {}
        self._memory_state: dict[str, dict] = {}

    async def save_event(self, event: StoredEvent) -> None:
        key = event.trace_id or "__global__"
        self._events[key].append(event)

    async def load_history(self, trace_id: str) -> list[StoredEvent]:
        return sorted(self._events.get(trace_id, []), key=lambda e: e.ts)

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        self._bindings.append(binding)

    # Optional methods
    async def save_planner_state(self, token: str, payload: dict) -> None:
        self._planner_state[token] = payload

    async def load_planner_state(self, token: str) -> dict | None:
        return self._planner_state.pop(token, None)

    async def save_memory_state(self, key: str, state: dict) -> None:
        self._memory_state[key] = state

    async def load_memory_state(self, key: str) -> dict | None:
        return self._memory_state.get(key)
```

### PostgreSQL (Production)

See full implementation in `docs/tools/statestore-guide.md`.

Key considerations:
- Use connection pooling (`asyncpg.Pool`)
- Add indexes on `trace_id` and `ts`
- Set TTL on pause records
- Consider partitioning for high-volume event tables

### Redis + PostgreSQL (Hybrid)

```python
class HybridStateStore:
    """Redis for hot paths (pause/resume), PostgreSQL for durability."""

    def __init__(self, redis: Redis, pg_pool: asyncpg.Pool):
        self._redis = redis
        self._pg = pg_pool

    # Events go to PostgreSQL (durability)
    async def save_event(self, event: StoredEvent) -> None:
        await self._pg.execute(...)

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        return await self._pg.fetch(...)

    # Pause/resume uses Redis (speed)
    async def save_planner_state(self, token: str, payload: dict) -> None:
        await self._redis.setex(f"pause:{token}", 3600, json.dumps(payload))

    async def load_planner_state(self, token: str) -> dict | None:
        data = await self._redis.getdel(f"pause:{token}")
        return json.loads(data) if data else None

    # Memory can use either (preference: Redis for speed)
    async def save_memory_state(self, key: str, state: dict) -> None:
        await self._redis.set(f"memory:{key}", json.dumps(state))

    async def load_memory_state(self, key: str) -> dict | None:
        data = await self._redis.get(f"memory:{key}")
        return json.loads(data) if data else None
```

---

## Compatibility Adapters

PenguiFlow provides compatibility adapters to bridge legacy method names during migration.

### Legacy Method Mappings

| New Method | Legacy Alternative | Used By |
|------------|-------------------|---------|
| `save_update()` | `save_task_update()` | Task service |
| `list_updates()` | `list_task_updates()` | Task service |
| `save_planner_event()` | `save_event(trace_id, event)` | Playground |
| `list_planner_events()` | `get_events()` | Playground |

### Using Compatibility Functions

```python
from penguiflow.state.adapters import (
    save_update_compat,
    list_updates_compat,
    save_planner_event_compat,
    list_planner_events_compat,
    maybe_save_remote_binding,
)

# These try new method name first, fall back to legacy
await save_update_compat(store, update)
updates = await list_updates_compat(store, session_id, since_id=cursor)

# Safe None-guard for optional stores
await maybe_save_remote_binding(store, binding)  # No-op if store is None
```

### Implementing Both Interfaces

For gradual migration, implement both method names:

```python
class MyStateStore:
    # New interface
    async def save_update(self, update: StateUpdate) -> None:
        await self._persist_update(update)

    # Legacy interface (deprecated)
    async def save_task_update(self, update: StateUpdate) -> None:
        await self.save_update(update)  # Delegate to new method
```

---

## Telemetry Integration

### TaskTelemetrySink Protocol

```python
class TaskTelemetrySink(Protocol):
    async def emit(self, event: TaskTelemetryEvent) -> None: ...
```

### TaskTelemetryEvent

```python
class TaskTelemetryEvent(BaseModel):
    event_type: Literal[
        "task_spawned",
        "task_completed",
        "task_failed",
        "task_cancelled",
        "task_group_completed",
        "task_group_failed",
    ]
    outcome: Literal["spawned", "completed", "failed", "cancelled"]
    session_id: str
    task_id: str
    parent_task_id: str | None = None
    trace_id: str | None = None
    task_type: TaskType
    status: TaskStatus
    mode: Literal["foreground", "subagent", "job"] | None = None
    spawn_reason: str | None = None
    duration_ms: float | None = None
    created_at_s: float = Field(default_factory=time.time)
    extra: dict[str, Any] = Field(default_factory=dict)
```

### Built-in Implementations

```python
from penguiflow.sessions.telemetry import (
    NoOpTaskTelemetrySink,    # Silent (default)
    LoggingTaskTelemetrySink, # Routes to Python logger
)
```

### Custom Telemetry Sink

```python
class PrometheusTaskSink:
    def __init__(self, registry: CollectorRegistry):
        self._counter = Counter(
            "penguiflow_tasks_total",
            "Total tasks by outcome",
            ["outcome", "task_type"],
            registry=registry,
        )

    async def emit(self, event: TaskTelemetryEvent) -> None:
        self._counter.labels(
            outcome=event.outcome,
            task_type=event.task_type.value,
        ).inc()
```

### Integration with Session

```python
session = StreamingSession(
    session_id="...",
    state_store=your_store,
    telemetry_sink=PrometheusTaskSink(registry),
)
```

### Telemetry vs. StateStore

| Aspect | TaskTelemetrySink | StateStore |
|--------|-------------------|------------|
| Purpose | Observability/monitoring | Durability/auditability |
| Emission | Async fire-and-forget | Persistent storage |
| Data | High-level event semantics | Full task/update payloads |
| Scope | Task lifecycle only | Complete execution history |
| Use Case | Metrics, alerts, dashboards | Recovery, replay, audit |

---

## Database Schema Recommendations

### PostgreSQL Schema

```sql
-- Events table (required)
CREATE TABLE flow_events (
    id BIGSERIAL PRIMARY KEY,
    -- Recommended: normalise trace_id=None ("global events") to a sentinel like '__global__'
    -- so this column can be NOT NULL and participate in uniqueness constraints.
    trace_id VARCHAR(128) NOT NULL,
    ts DOUBLE PRECISION NOT NULL,
    kind VARCHAR(64) NOT NULL,
    node_name VARCHAR(128),
    node_id VARCHAR(128),
    -- sha256 of canonical JSON of {trace_id, ts, kind, node_name, node_id, payload}
    -- Used to make save_event() idempotent without requiring a first-class event_id.
    event_fp VARCHAR(64) NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (trace_id, event_fp)
);

CREATE INDEX idx_events_trace_ts ON flow_events(trace_id, ts, id);
CREATE INDEX idx_events_kind ON flow_events(kind);

-- Remote bindings
CREATE TABLE remote_bindings (
    trace_id VARCHAR(64) NOT NULL,
    context_id VARCHAR(64),
    task_id VARCHAR(64) NOT NULL,
    agent_url TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (trace_id, task_id)
);

-- Planner pause state
CREATE TABLE planner_pauses (
    token VARCHAR(128) PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_pauses_expires ON planner_pauses(expires_at);

-- Memory state
CREATE TABLE memory_states (
    key VARCHAR(256) PRIMARY KEY,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Task state (new)
CREATE TABLE task_states (
    task_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    context_snapshot JSONB NOT NULL,
    trace_id VARCHAR(64),
    result JSONB,
    error TEXT,
    description TEXT,
    progress JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tasks_session ON task_states(session_id);
CREATE INDEX idx_tasks_status ON task_states(session_id, status);

-- State updates (new)
CREATE TABLE state_updates (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64),
    update_id VARCHAR(64) NOT NULL UNIQUE,
    update_type VARCHAR(32) NOT NULL,
    content JSONB,
    step_index INTEGER,
    total_steps INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_updates_session ON state_updates(session_id);
CREATE INDEX idx_updates_task ON state_updates(session_id, task_id);
CREATE INDEX idx_updates_cursor ON state_updates(session_id, id);

-- Steering events (new)
CREATE TABLE steering_events (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    event_id VARCHAR(64) NOT NULL UNIQUE,
    event_type VARCHAR(32) NOT NULL,
    payload JSONB,
    trace_id VARCHAR(64),
    source VARCHAR(32) DEFAULT 'user',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_steering_session ON steering_events(session_id);
CREATE INDEX idx_steering_task ON steering_events(session_id, task_id);

-- Trajectories (new)
CREATE TABLE trajectories (
    trace_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    trajectory JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (trace_id, session_id)
);

CREATE INDEX idx_trajectories_session ON trajectories(session_id, created_at DESC);

-- Planner events (new)
CREATE TABLE planner_events (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    ts DOUBLE PRECISION NOT NULL,
    trajectory_step INTEGER,
    thought TEXT,
    node_name VARCHAR(128),
    latency_ms DOUBLE PRECISION,
    token_estimate INTEGER,
    error TEXT,
    extra JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_planner_events_trace ON planner_events(trace_id, id);

-- Artifacts (new)
CREATE TABLE artifacts (
    artifact_id VARCHAR(128) PRIMARY KEY,
    session_id VARCHAR(64),
    trace_id VARCHAR(64),
    mime_type VARCHAR(128) NOT NULL,
    size_bytes BIGINT NOT NULL,
    filename VARCHAR(256),
    sha256 VARCHAR(64) NOT NULL,
    scope JSONB,
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_artifacts_session ON artifacts(session_id);
CREATE INDEX idx_artifacts_expires ON artifacts(expires_at);

-- Cleanup jobs (run periodically)
-- DELETE FROM planner_pauses WHERE expires_at < NOW();
-- DELETE FROM artifacts WHERE expires_at < NOW();
```

### Redis Key Patterns

```
# Planner pause state
pause:{token}           -> JSON payload (TTL: 1 hour)

# Memory state
memory:{tenant}:{user}:{session}  -> JSON state (no TTL or long TTL)

# Event streams (optional, for real-time)
events:{trace_id}       -> Redis Stream

# Task state (optional, for fast lookups)
task:{session_id}:{task_id}  -> JSON TaskState

# Steering queue (optional, for pub/sub)
steering:{session_id}   -> Redis Stream
```

---

## Testing Your Implementation

### Required Test Cases

```python
import pytest
from your_module import YourStateStore
from penguiflow.state import StoredEvent, RemoteBinding, TaskState, StateUpdate, SteeringEvent

@pytest.fixture
async def store():
    store = YourStateStore()
    yield store
    await store.cleanup()  # If applicable

# 1. Event persistence
async def test_save_and_load_events(store):
    event = StoredEvent(
        trace_id="test-trace",
        ts=1702857600.0,
        kind="node_success",
        node_name="test_node",
        node_id="test_node_123",
        payload={"latency_ms": 100},
    )
    await store.save_event(event)
    history = await store.load_history("test-trace")

    assert len(history) == 1
    assert history[0].trace_id == "test-trace"
    assert history[0].kind == "node_success"

# 2. Empty history for unknown trace
async def test_load_history_unknown_trace(store):
    history = await store.load_history("nonexistent")
    assert history == []

# 3. Event ordering
async def test_events_ordered_by_timestamp(store):
    for i, ts in enumerate([3.0, 1.0, 2.0]):
        await store.save_event(StoredEvent(
            trace_id="order-test",
            ts=ts,
            kind="test",
            node_name=f"node_{i}",
            node_id=None,
            payload={},
        ))

    history = await store.load_history("order-test")
    timestamps = [e.ts for e in history]
    assert timestamps == [1.0, 2.0, 3.0]

# 4. Idempotent save
async def test_save_event_idempotent(store):
    event = StoredEvent(
        trace_id="idem-test",
        ts=1.0,
        kind="test",
        node_name="node",
        node_id="id",
        payload={},
    )
    await store.save_event(event)
    await store.save_event(event)  # Should not fail

    history = await store.load_history("idem-test")
    assert len(history) >= 1  # May be 1 or 2 depending on idempotency impl

# 5. Remote binding
async def test_save_remote_binding(store):
    binding = RemoteBinding(
        trace_id="binding-test",
        context_id="ctx",
        task_id="task-123",
        agent_url="http://worker:8080",
    )
    await store.save_remote_binding(binding)
    # Verify via direct query or separate method

# 6. Planner state (if implemented)
async def test_planner_state_save_load(store):
    if not hasattr(store, "save_planner_state"):
        pytest.skip("Planner state not implemented")

    token = "test-token-123"
    payload = {
        "trajectory": {"version": 1, "steps": []},
        "reason": "await_input",
        "payload": {"example": True},
        "constraints": None,
        "tool_context": {"tenant_id": "test", "user_id": "test"},
    }

    await store.save_planner_state(token, payload)
    loaded = await store.load_planner_state(token)

    assert loaded == payload

# 7. Planner state consumed on load
async def test_planner_state_consumed(store):
    if not hasattr(store, "save_planner_state"):
        pytest.skip("Planner state not implemented")

    token = "consume-test"
    await store.save_planner_state(token, {"data": "value"})

    first_load = await store.load_planner_state(token)
    second_load = await store.load_planner_state(token)

    assert first_load == {"data": "value"}
    assert second_load is None  # Consumed / missing

# 8. Memory state (if implemented)
async def test_memory_state_roundtrip(store):
    if not hasattr(store, "save_memory_state"):
        pytest.skip("Memory state not implemented")

    key = "tenant:user:session"
    state = {"turn_history": [{"role": "user", "content": "hello"}]}

    await store.save_memory_state(key, state)
    loaded = await store.load_memory_state(key)

    assert loaded == state

# 9. Memory state returns None for unknown key
async def test_memory_state_unknown_key(store):
    if not hasattr(store, "load_memory_state"):
        pytest.skip("Memory state not implemented")

    result = await store.load_memory_state("nonexistent:key")
    assert result is None

# 10. Task state (if implemented)
async def test_task_state_roundtrip(store):
    if not hasattr(store, "save_task"):
        pytest.skip("Task persistence not implemented")

    from penguiflow.state import TaskState, TaskStatus, TaskType, TaskContextSnapshot

    snapshot = TaskContextSnapshot(
        session_id="test-session",
        task_id="test-task",
        context_version=1,
        context_hash="abc123",
    )
    task = TaskState(
        task_id="test-task",
        session_id="test-session",
        status=TaskStatus.PENDING,
        task_type=TaskType.BACKGROUND,
        priority=5,
        context_snapshot=snapshot,
        description="Test task",
    )

    await store.save_task(task)
    tasks = await store.list_tasks("test-session")

    assert len(tasks) == 1
    assert tasks[0].task_id == "test-task"
    assert tasks[0].priority == 5

# 11. State update with cursor (if implemented)
async def test_state_update_cursor(store):
    if not hasattr(store, "save_update"):
        pytest.skip("Update persistence not implemented")

    from penguiflow.state import StateUpdate, UpdateType

    updates = [
        StateUpdate(
            session_id="test-session",
            task_id="test-task",
            update_id=f"update-{i}",
            update_type=UpdateType.PROGRESS,
            content={"step": i},
        )
        for i in range(5)
    ]

    for update in updates:
        await store.save_update(update)

    # Get all updates
    all_updates = await store.list_updates("test-session")
    assert len(all_updates) == 5

    # Get updates after cursor
    partial = await store.list_updates("test-session", since_id="update-2")
    assert len(partial) == 2  # update-3, update-4

# 12. Steering event sanitization (if implemented)
async def test_steering_sanitized(store):
    if not hasattr(store, "save_steering"):
        pytest.skip("Steering persistence not implemented")

    from penguiflow.state import SteeringEvent, SteeringEventType

    event = SteeringEvent(
        session_id="test-session",
        task_id="test-task",
        event_type=SteeringEventType.USER_MESSAGE,
        payload={"text": "Hello"},
        source="user",
    )

    await store.save_steering(event)
    events = await store.list_steering("test-session")

    assert len(events) == 1
    assert events[0].source == "user"
```

### Integration Test with PenguiFlow

```python
from penguiflow import PenguiFlow, Node, Context

async def test_statestore_integration():
    store = YourStateStore()

    @Node()
    async def echo(ctx: Context, msg: str) -> str:
        return f"echo: {msg}"

    flow = PenguiFlow(
        (echo, []),
        state_store=store,
    )

    async with flow.run() as (send, recv):
        await send("hello", trace_id="int-test")
        result = await recv()

    history = await store.load_history("int-test")

    # Should have at least node_start and node_success events
    kinds = [e.kind for e in history]
    assert "node_start" in kinds
    assert "node_success" in kinds
```

---

## Error Handling

### What Is Best-Effort vs. What Can Propagate

PenguiFlow does **not** treat every StateStore call uniformly. In v2.11.x:

- **Core runtime audit-log calls** are best-effort:
  - `PenguiFlow` wraps `save_event()` and `save_remote_binding()` in `try/except` and logs failures.
- **Explicit read APIs** (like `flow.load_history(trace_id)`) may propagate storage errors to the caller.
- **Session persistence calls** (tasks/updates/steering) may be awaited without a defensive wrapper (some are fire-and-forget via `asyncio.create_task`, others are awaited inline). A throwing StateStore can therefore break interactive features.
- **Planner pause/resume** has an in-memory fallback for saving pause records, but resume still depends on `load_planner_state()` being correct.

> **Gold standard rule:** For production-grade deployments, treat every StateStore method as **must not throw**.
> Degrade with safe defaults (`[]`/`None`), apply timeouts, and log/telemetry rather than raising.

### Example: Core Runtime Best-Effort Wrapper

The core runtime wraps audit-log persistence:

```python
# From penguiflow/core.py
try:
    await self._state_store.save_event(stored_event)
except Exception as exc:
    logger.exception(
        "state_store_save_failed",
        extra={
            "event": "state_store_save_failed",
            "trace_id": stored_event.trace_id,
            "kind": stored_event.kind,
            "exception": repr(exc),
        },
    )
    # Flow continues - event loss is acceptable
```

### Recommended Error Handling in Your Implementation

```python
async def save_event(self, event: StoredEvent) -> None:
    try:
        await self._pool.execute(...)
    except asyncpg.PostgresConnectionError:
        # Gold standard: degrade gracefully.
        # - log/telemetry
        # - optionally enqueue to a dead-letter queue / local buffer
        # - return without raising (avoid breaking sessions/planner callers)
        logger.warning("statestore_unavailable", extra={"method": "save_event", "trace_id": event.trace_id})
        return
    except asyncpg.UniqueViolationError:
        # Idempotent - this is expected for duplicates
        pass
    except Exception:
        logger.exception("statestore_save_failed", extra={"method": "save_event", "trace_id": event.trace_id})
        return
```

### Logging Recommendations

Use structured logging with these fields:
- `event`: `"statestore_save_failed"`, `"statestore_load_failed"`
- `trace_id`: The affected trace
- `method`: `"save_event"`, `"load_history"`, etc.
- `exception`: The exception repr

---

## Performance Considerations

### Connection Pooling

Always use connection pools for database backends:

```python
# asyncpg
pool = await asyncpg.create_pool(
    dsn,
    min_size=5,
    max_size=20,
    command_timeout=5.0,
)

# aioredis
pool = redis.ConnectionPool.from_url(url, max_connections=20)
```

### Batching (High-Volume Scenarios)

For high-throughput flows, consider batching event writes:

```python
class BatchingStateStore:
    def __init__(self, delegate: StateStore, batch_size: int = 100):
        self._delegate = delegate
        self._batch: list[StoredEvent] = []
        self._batch_size = batch_size
        self._lock = asyncio.Lock()

    async def save_event(self, event: StoredEvent) -> None:
        async with self._lock:
            self._batch.append(event)
            if len(self._batch) >= self._batch_size:
                await self._flush()

    async def _flush(self) -> None:
        if not self._batch:
            return
        batch, self._batch = self._batch, []
        # Bulk insert (out-of-protocol extension).
        # If you add a bulk method, feature-detect it via hasattr() and fall back to per-event writes.
        bulk = getattr(self._delegate, "save_events_bulk", None)
        if bulk is not None:
            await bulk(batch)
        else:
            for item in batch:
                await self._delegate.save_event(item)
```

### Indexing Strategy

Essential indexes:
- `(trace_id, ts)` - for `load_history()`
- `(session_id)` - for `list_tasks()`, `list_updates()`
- `(session_id, id)` - for cursor-based pagination
- `(expires_at)` - for pause/artifact cleanup

Avoid:
- Indexes on `payload` JSONB (expensive to maintain)
- Too many indexes on write-heavy tables

### Timeout Configuration

Set reasonable timeouts to prevent blocking:

```python
async def save_event(self, event: StoredEvent) -> None:
    async with asyncio.timeout(5.0):  # 5 second timeout
        await self._pool.execute(...)
```

---

## Implementation Checklist

Use this checklist to verify your implementation is complete:

### Required Methods

- [ ] `save_event(event: StoredEvent) -> None` implemented
- [ ] `load_history(trace_id: str) -> Sequence[StoredEvent]` implemented
- [ ] `save_remote_binding(binding: RemoteBinding) -> None` implemented

### Method Contracts

- [ ] `save_event` is idempotent
- [ ] `load_history` returns events ordered by timestamp
- [ ] `load_history` returns empty list for unknown trace (not exception)
- [ ] `save_remote_binding` supports upsert semantics

### Optional Methods (if applicable)

- [ ] `save_planner_state(token, payload)` implemented
- [ ] `load_planner_state(token)` implemented
- [ ] `load_planner_state` returns `None` for missing/expired tokens
- [ ] `load_planner_state` consumes token (one-time use)
- [ ] `save_memory_state(key, state)` implemented
- [ ] `load_memory_state(key)` implemented
- [ ] `load_memory_state` returns `None` for missing keys

### Task & Session Methods (if applicable)

- [ ] `save_task(state: TaskState)` implemented
- [ ] `list_tasks(session_id)` implemented
- [ ] `save_update(update: StateUpdate)` implemented
- [ ] `list_updates(session_id, task_id, since_id, limit)` implemented
- [ ] `save_steering(event: SteeringEvent)` implemented
- [ ] `list_steering(session_id, task_id, since_id, limit)` implemented

### Trajectory & Planner Events (if applicable)

- [ ] `save_trajectory(trace_id, session_id, trajectory)` implemented
- [ ] `get_trajectory(trace_id, session_id)` implemented
- [ ] `list_traces(session_id, limit)` implemented
- [ ] `save_planner_event(trace_id, event)` implemented
- [ ] `list_planner_events(trace_id)` implemented

### Artifact Store (if applicable)

- [ ] `artifact_store` property returns `ArtifactStore` or `None`
- [ ] Retention config honored (TTL, max sizes)
- [ ] Session-scoped access patterns supported

### Production Readiness

- [ ] Connection pooling configured
- [ ] Timeouts set on all database operations
- [ ] Proper error handling (log, don't crash)
- [ ] TTL/expiration on pause records
- [ ] Cleanup job for expired data
- [ ] Indexes on frequently queried columns
- [ ] Steering payloads sanitized before storage
- [ ] Unit tests passing
- [ ] Integration test with PenguiFlow passing

### Distributed Deployment

- [ ] Idempotency verified for all write methods
- [ ] Cursor semantics work correctly for list methods
- [ ] Context versioning preserved in TaskContextSnapshot
- [ ] Session hydration tested

### Documentation

- [ ] Factory function documented for admin CLI
- [ ] Environment variables documented
- [ ] Schema migrations documented

---

## See Also

- [StateStore Production Guide](../tools/statestore-guide.md) - PostgreSQL/Redis patterns and pause/resume
- [StateStore Standard Follow-Ups RFC](../RFC/ToDo/RFC_STATESTORE_STANDARD_FOLLOWUPS.md) - Known gaps + roadmap
- [A2A Compliance Gap Analysis](../A2A_COMPLIANCE_GAP_ANALYSIS.md) - A2A status and gaps
- [Observability & Monitoring](../architecture/infrastructure/observability_monitoring.md) - Logging/metrics patterns
- `penguiflow/planner/pause_management.py` - Canonical pause/resume wire format and loader semantics
