# StateStore Implementation Specification

> **Version:** 2.7
> **Audience:** Downstream teams implementing custom StateStore backends
> **Last Updated:** December 2025

This document provides a complete specification for implementing a StateStore backend compatible with PenguiFlow. It covers the protocol definition, data types, method contracts, integration points, and production best practices.

---

## Table of Contents

1. [Overview](#overview)
2. [Protocol Definition](#protocol-definition)
3. [Data Types](#data-types)
4. [Required Methods](#required-methods)
5. [Optional Duck-Typed Methods](#optional-duck-typed-methods)
6. [Integration Points](#integration-points)
7. [Method Contracts & Semantics](#method-contracts--semantics)
8. [Reference Implementations](#reference-implementations)
9. [Database Schema Recommendations](#database-schema-recommendations)
10. [Testing Your Implementation](#testing-your-implementation)
11. [Error Handling](#error-handling)
12. [Performance Considerations](#performance-considerations)
13. [Checklist](#implementation-checklist)

---

## Overview

### What is StateStore?

`StateStore` is a **Protocol** (duck-typed interface) that enables PenguiFlow to persist:

- **Runtime events** (node execution, errors, retries) for audit/replay
- **Remote bindings** (A2A worker associations) for distributed flows
- **Planner pause state** (OAuth/HITL flows) for distributed resume
- **Memory state** (conversation history) for session persistence

### Why Implement a StateStore?

| Feature | Without StateStore | With StateStore |
|---------|-------------------|-----------------|
| Event audit trail | In-memory only, lost on restart | Persisted, queryable |
| Trace replay | Not available | Full replay capability |
| Distributed resume (OAuth/HITL) | Single-worker only | Multi-worker support |
| Session memory persistence | In-memory, per-instance | Shared across instances |
| Remote binding tracking | Not tracked | Persisted for debugging |

### Design Philosophy

1. **Optional but recommended** - Flows work without a StateStore, but persistence features require one
2. **Protocol-based (duck-typing)** - No inheritance required; just implement the methods
3. **Fail-safe** - StateStore errors never crash the runtime; errors are logged and execution continues
4. **Async-only** - All methods must be async/awaitable
5. **Idempotent writes** - `save_event` may be called multiple times for the same event (retries)

---

## Protocol Definition

**Location:** `penguiflow/state/protocol.py` (import path remains `penguiflow.state`)

```python
from typing import Protocol, Sequence, Any, Mapping
from dataclasses import dataclass

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

### Protocol Checking

PenguiFlow validates StateStore implementations at runtime. The admin CLI (`penguiflow-admin`) uses this check:

```python
required = ("save_event", "load_history", "save_remote_binding")
if not all(hasattr(instance, attr) for attr in required):
    raise TypeError("StateStore must implement required methods")
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

| Event Kind | Description | When Emitted |
|------------|-------------|--------------|
| `node_start` | Node began processing | Before node handler runs |
| `node_end` | Node completed successfully | After node handler returns |
| `node_error` | Node raised an exception | On unhandled exception |
| `retry` | Retry attempt initiated | Before retry sleep |
| `emit` | Message emitted to downstream | When node emits output |
| `fetch` | Message fetched from queue | When node pulls from queue |
| `stream_chunk` | Streaming token emitted | During LLM streaming |
| `cancel_begin` | Trace cancellation started | When `cancel()` called |
| `cancel_end` | Trace cancellation completed | After cancel cleanup |
| `timeout` | Deadline exceeded | When deadline/budget exhausted |

#### Payload Structure

The `payload` dict contains event-specific fields. Common fields:

```python
{
    "ts": 1702857600.123,          # Timestamp
    "event": "node_end",           # Event type (same as kind)
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
        "original_exc": "HTTPError(...)"
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

### FlowEvent (Source Type)

The runtime emits `FlowEvent` objects which are converted to `StoredEvent` via the factory method. You don't directly interact with `FlowEvent` in your StateStore, but understanding it helps with schema design:

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
- SHOULD handle `trace_id=None` for global events
- MAY store events asynchronously (eventual consistency acceptable)

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `StoredEvent` | The event to persist |

**Example Implementation:**
```python
async def save_event(self, event: StoredEvent) -> None:
    await self._pool.execute(
        """
        INSERT INTO flow_events (trace_id, ts, kind, node_name, node_id, payload)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING  -- Idempotent
        """,
        event.trace_id,
        event.ts,
        event.kind,
        event.node_name,
        event.node_id,
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
    "trajectory": [...],           # List of planner steps
    "payload": {...},              # Original request payload
    "constraints": {...},          # Planning constraints
    "tool_context": {...},         # Tool execution context
    "llm_context": {...},          # LLM context (optional)
    "short_term_memory": {...},    # Memory snapshot (optional)
}
```

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

### 5. `load_planner_state(token: str) -> dict`

**Purpose:** Load planner pause state for resume.

**When Called:** When `ReactPlanner.resume(token)` is called after OAuth/HITL completion.

**Contract:**
- MUST return empty dict `{}` if token not found or expired
- SHOULD NOT raise exceptions for missing tokens
- SHOULD delete/expire token after successful load (one-time use)

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `token` | `str` | The pause token to load |

**Returns:** `dict` - The stored payload, or `{}` if not found

**Example Implementation:**
```python
async def load_planner_state(self, token: str) -> dict:
    row = await self._pool.fetchrow(
        """
        DELETE FROM planner_pauses
        WHERE token = $1 AND expires_at > NOW()
        RETURNING payload
        """,
        token,
    )
    if not row:
        return {}
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
    "turn_history": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
    ],
    "summary": "Previous conversation summary...",
    "metadata": {...},
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
- Reads MUST return an empty sequence on “not found” (do not raise).

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
- If `since_id` is unknown, treat it as “no cursor”.
- To preserve cursor semantics, avoid filtering after applying limit.

### 9. Steering Persistence (`save_steering`, `list_steering`)

**Purpose:** Persist bidirectional steering/intervention events (USER_MESSAGE, CANCEL, APPROVE, etc.).

**Location (integration):** `penguiflow/sessions/session.py`

**Contracts:**
- Steering payloads are untrusted; persist the sanitized form (see `penguiflow/steering.py`).
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

### 12. Artifact Storage (`artifact_store` property)

**Purpose:** Provide binary/large-text storage for rich tool outputs without polluting LLM context.

**Contract:**
- Return an `ArtifactStore` instance, or `None` if artifacts are not supported.
- Prefer session-scoped access patterns for HTTP retrieval (see `ArtifactScope.session_id`).

**Signature:**
```python
@property
def artifact_store(self) -> ArtifactStore | None: ...
```

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
- Errors in StateStore methods are logged but **never crash the flow**

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
- Methods detected via `getattr()` - missing methods are silently skipped

### 3. StreamingSession / SessionManager

**Location:** `penguiflow/sessions/session.py`

**Integration Behavior:**
- `save_task()` called on task lifecycle transitions
- `save_update()` called for streaming updates emitted by tasks
- `save_steering()` called for inbound steering events
- `list_tasks()` used to hydrate a session on startup
- `list_updates()` / `list_steering()` used for polling and UI backfills

### 3. Admin CLI

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

    async def load_planner_state(self, token: str) -> dict:
        return self._planner_state.pop(token, {})

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

    async def load_planner_state(self, token: str) -> dict:
        data = await self._redis.getdel(f"pause:{token}")
        return json.loads(data) if data else {}

    # Memory can use either (preference: Redis for speed)
    async def save_memory_state(self, key: str, state: dict) -> None:
        await self._redis.set(f"memory:{key}", json.dumps(state))

    async def load_memory_state(self, key: str) -> dict | None:
        data = await self._redis.get(f"memory:{key}")
        return json.loads(data) if data else None
```

---

## Database Schema Recommendations

### PostgreSQL Schema

```sql
-- Events table (required)
CREATE TABLE flow_events (
    id BIGSERIAL PRIMARY KEY,
    trace_id VARCHAR(64),
    ts DOUBLE PRECISION NOT NULL,
    kind VARCHAR(32) NOT NULL,
    node_name VARCHAR(128),
    node_id VARCHAR(128),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_events_trace_id ON flow_events(trace_id);
CREATE INDEX idx_events_trace_ts ON flow_events(trace_id, ts);
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

-- Cleanup job for expired pauses (run periodically)
-- DELETE FROM planner_pauses WHERE expires_at < NOW();
```

### Redis Key Patterns

```
# Planner pause state
pause:{token}           -> JSON payload (TTL: 1 hour)

# Memory state
memory:{tenant}:{user}:{session}  -> JSON state (no TTL or long TTL)

# Event streams (optional, for real-time)
events:{trace_id}       -> Redis Stream
```

---

## Testing Your Implementation

### Required Test Cases

```python
import pytest
from your_module import YourStateStore
from penguiflow.state import StoredEvent, RemoteBinding

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
        kind="node_end",
        node_name="test_node",
        node_id="test_node_123",
        payload={"latency_ms": 100},
    )
    await store.save_event(event)
    history = await store.load_history("test-trace")

    assert len(history) == 1
    assert history[0].trace_id == "test-trace"
    assert history[0].kind == "node_end"

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
    payload = {"trajectory": [], "constraints": {}}

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
    assert second_load == {}  # Consumed

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

    # Should have at least node_start and node_end events
    kinds = [e.kind for e in history]
    assert "node_start" in kinds
    assert "node_end" in kinds
```

---

## Error Handling

### StateStore Errors Are Non-Fatal

PenguiFlow wraps all StateStore calls in try/except. Errors are logged but never propagate:

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
        # Log and potentially retry with backoff
        raise  # Let PenguiFlow handle it
    except asyncpg.UniqueViolationError:
        # Idempotent - this is expected for duplicates
        pass
    except Exception:
        # Log unexpected errors
        raise
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
        # Bulk insert
        await self._delegate.save_events_bulk(batch)
```

### Indexing Strategy

Essential indexes:
- `(trace_id, ts)` - for `load_history()`
- `(expires_at)` - for pause cleanup
- `(kind)` - for event type filtering (optional)

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
- [ ] `load_planner_state` returns `{}` for missing/expired tokens
- [ ] `load_planner_state` consumes token (one-time use)
- [ ] `save_memory_state(key, state)` implemented
- [ ] `load_memory_state(key)` implemented
- [ ] `load_memory_state` returns `None` for missing keys

### Production Readiness

- [ ] Connection pooling configured
- [ ] Timeouts set on all database operations
- [ ] Proper error handling (log, don't crash)
- [ ] TTL/expiration on pause records
- [ ] Cleanup job for expired data
- [ ] Indexes on frequently queried columns
- [ ] Unit tests passing
- [ ] Integration test with PenguiFlow passing

### Documentation

- [ ] Factory function documented for admin CLI
- [ ] Environment variables documented
- [ ] Schema migrations documented

---

## See Also

- [StateStore Production Guide](./tools/statestore-guide.md) - PostgreSQL/Redis examples
- [ReactPlanner Integration](./REACT_PLANNER_INTEGRATION_GUIDE.md) - Pause/resume flows
- [A2A Protocol](./A2A_PROTOCOL.md) - Remote binding details
- [Observability](./OBSERVABILITY.md) - Event types and metrics
