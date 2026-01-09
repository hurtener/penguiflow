# RFC: Unified StateStore Protocol

> **RFC Version:** 1.0
> **Target Release:** v2.10
> **Status:** Draft
> **Author:** Santiago Benvenuto
> **Created:** January 2025

---

## Summary

Expand the existing `StateStore` protocol to become the single source of truth for all persistence in PenguiFlow. This consolidates four separate store protocols (`StateStore`, `SessionStateStore`, `PlaygroundStateStore`, `ArtifactStore`) into one unified interface with optional duck-typed methods.

---

## Motivation

### Current State (Fragmented)

PenguiFlow currently has **four separate persistence protocols**:

| Protocol | Location | Purpose | Documented? |
|----------|----------|---------|-------------|
| `StateStore` | `penguiflow/state/` | Events, bindings, planner state, memory | Yes |
| `SessionStateStore` | `penguiflow/sessions/persistence.py` | Tasks, updates, steering | No |
| `PlaygroundStateStore` | `penguiflow/cli/playground_state.py` | Trajectories, planner events, artifacts | No |
| `ArtifactStore` | `penguiflow/artifacts.py` | Binary blob storage | Partial |

### Problems

1. **Downstream confusion**: Teams must implement multiple protocols for full functionality
2. **No unified documentation**: Only `StateStore` has implementation spec
3. **Playground creates stores separately**: No coordination between task state and conversation state
4. **Production deployment unclear**: Which stores are required? How do they connect?
5. **Testing complexity**: Multiple stores to mock and wire together

### Goals

1. **One protocol to rule them all**: `StateStore` becomes the unified interface
2. **Backward compatible**: Existing implementations continue to work
3. **Clear documentation**: Single spec covers all persistence
4. **Reference implementation**: `InMemoryStateStore` as template for downstream teams
5. **Clean file organization**: No "god file" - modular package structure

---

## Detailed Design

### Protocol Structure

```python
class StateStore(Protocol):
    """Unified persistence protocol for PenguiFlow.

    Required methods must be implemented. Optional methods are detected
    via duck-typing (hasattr) and enable additional features when present.
    """

    # ═══════════════════════════════════════════════════════════════════
    # REQUIRED - Core Event Storage
    # ═══════════════════════════════════════════════════════════════════

    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime FlowEvent for audit/replay."""
        ...

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Load all events for a trace, ordered by timestamp."""
        ...

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Persist A2A remote worker association."""
        ...
```

### Optional Methods (Duck-Typed)

All optional methods are detected at runtime via `hasattr()` / `getattr()`.

**Hardening note:** “silently skipped” is fine for dev defaults, but in production it can hide misconfiguration. The recommended approach is:

- **Feature-gated validation at component startup**: if `SessionManager` / Playground / planner is configured to persist a feature, validate the required optional methods exist and fail fast with a clear error.
- **Warn-on-use when optional capability is missing**: if a feature *tries* to persist and the store lacks methods, log a single warning (rate-limited) including the missing method names and the impacted feature.

#### Planner State (OAuth/HITL Resume)

```python
# Optional - enables distributed pause/resume
async def save_planner_state(self, token: str, payload: dict) -> None:
    """Persist planner pause state for resume."""
    ...

async def load_planner_state(self, token: str) -> dict:
    """Load and consume planner pause state. Returns {} if not found."""
    ...
```

#### Memory State (Short-Term Memory Persistence)

```python
# Optional - enables session memory persistence
async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
    """Persist short-term memory state. Key format: tenant:user:session"""
    ...

async def load_memory_state(self, key: str) -> dict[str, Any] | None:
    """Load memory state. Returns None if not found."""
    ...
```

#### Task State (Background Tasks)

```python
# Optional - enables background task orchestration
async def save_task(self, state: TaskState) -> None:
    """Persist task lifecycle state."""
    ...

async def list_tasks(self, session_id: str) -> Sequence[TaskState]:
    """List all tasks for a session."""
    ...

async def save_update(self, update: StateUpdate) -> None:
    """Persist task progress/status update (append-only)."""
    ...

async def list_updates(
    self,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> Sequence[StateUpdate]:
    """List task updates with optional filtering.

    Ordering and cursor semantics:
    - Returned in ascending order (oldest -> newest).
    - `since_id` is an *exclusive* cursor in that same ordering.
    - If `since_id` is unknown, return results as if no cursor was provided.
    """
    ...
```

**Compatibility aliases (recommended during migration):**

- Accept `save_task_update` as an alias of `save_update`.
- Accept `list_task_updates` as an alias of `list_updates`.

#### Steering Events (Chat-Based Steering)

```python
# Optional - enables steering/intervention
async def save_steering(self, event: SteeringEvent) -> None:
    """Persist steering event (USER_MESSAGE, PRIORITY_CHANGE, etc.)."""
    ...

async def list_steering(
    self,
    session_id: str,
    *,
    task_id: str | None = None,
    since_id: str | None = None,
    limit: int = 500,
) -> Sequence[SteeringEvent]:
    """List steering events with optional filtering."""
    ...
```

#### Trajectories (Conversation History)

```python
# Optional - enables conversation replay and debugging
async def save_trajectory(
    self,
    trace_id: str,
    session_id: str,
    trajectory: Trajectory,
) -> None:
    """Persist planner trajectory (tool calls, reasoning, results)."""
    ...

async def get_trajectory(
    self,
    trace_id: str,
    session_id: str,
) -> Trajectory | None:
    """Load trajectory. Returns None if not found."""
    ...

async def list_traces(
    self,
    session_id: str,
    limit: int = 50,
) -> list[str]:
    """List trace IDs for a session, most recent first.

    “Most recent” is defined as the last time a trajectory was saved for that trace.
    """
    ...
```

#### Planner Events (Tool Execution Events)

```python
# Optional - enables detailed execution tracking
async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None:
    """Persist planner-specific event (tool call, LLM response, etc.)."""
    ...

async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]:
    """List all planner events for a trace."""
    ...
```

**Naming collision note:** the unified protocol already uses `save_event()` for runtime `StoredEvent` (core audit log). This is why planner/tool events use `save_planner_event()` / `list_planner_events()` instead of reusing `save_event()` (the Playground protocol currently uses `save_event()` for planner events; it will need an adapter during migration).

**Compatibility aliases (recommended during migration):**

- Accept `save_event(trace_id, event)` (Playground-style) as an alias of `save_planner_event(trace_id, event)`.
- Accept `get_events(trace_id)` as an alias of `list_planner_events(trace_id)`.

#### Artifacts (Binary Storage)

```python
# Optional - enables binary content storage (images, PDFs, charts)
@property
def artifact_store(self) -> ArtifactStore | None:
    """Return artifact store for binary content, or None if not supported."""
    ...
```

### Production Semantics (Best Practices)

These semantics are what make implementations “production proof” (observable, safe under concurrency, and predictable across backends).

#### 1) Timeouts, cancellation, and backpressure

- All methods **must be cancellation-friendly** (don’t swallow `CancelledError`).
- All I/O should have a **bounded timeout** (per-call or via client config).
- Consider buffering/batching writes for high-volume event streams, but preserve ordering guarantees for readers.

#### 2) Idempotency and stable ordering

- **Idempotency**: `save_*` methods may be called multiple times for the same logical item (retries, at-least-once delivery). Implementations should dedupe via:
  - A natural unique key (`token`, `update_id`, `event_id`), or
  - A deterministic fingerprint of the payload when no ID exists.
- **Stable ordering**: any `list_*` method that supports `since_id` must return results in a stable order and interpret `since_id` as an exclusive cursor in that order.
- For `load_history()`, use a secondary sort key (e.g., auto-increment ID or deterministic event fingerprint) to break ties when timestamps are equal.

#### 3) Session isolation and tenancy

- If your deployment is multi-tenant, stores should treat `session_id` as scoped by `(tenant_id, user_id)` and enforce it at the storage/query layer.
- For memory persistence, prefer an internal composite key over a raw string. If a string key is used, standardize it (and escape delimiters) to avoid collisions.

#### 4) Payload hygiene (size, PII, untrusted input)

- Bound payload sizes for steering and updates; sanitize to JSON-serializable shapes.
- Treat steering payloads as untrusted input; persist the sanitized form (see `penguiflow/steering.py` sanitizers).

#### 5) Failure handling and observability

- StateStore failures should be **observable** (logs + metrics) but not crash flows by default.
- Separate “optional feature persistence failed” from “core audit log failed” in telemetry so operators know what degraded.

### Recommended Capability Validation Helper

At startup (or when enabling a feature), validate capabilities explicitly rather than relying on “silent skip” behavior:

```python
def require_capabilities(store: object, *, feature: str, methods: tuple[str, ...]) -> None:
    missing = [m for m in methods if not hasattr(store, m)]
    if missing:
        raise TypeError(f"StateStore missing {missing} required for feature={feature}")
```

---

## File Organization

### New Package Structure

```
penguiflow/
├── state/                              # Unified state package (replaces state.py module)
│   ├── __init__.py                     # Exports protocol + all models
│   ├── protocol.py                     # StateStore protocol (slim, ~100 lines)
│   ├── models.py                       # All data classes:
│   │                                   #   StoredEvent, RemoteBinding,
│   │                                   #   TaskState, StateUpdate,
│   │                                   #   SteeringEvent, Trajectory
│   ├── in_memory.py                    # InMemoryStateStore reference impl
│   └── adapters.py                     # Migration adapters (if needed)
│
├── artifacts.py                        # ArtifactStore protocol (unchanged)
│                                       # InMemoryArtifactStore (unchanged)
│
├── planner/
│   ├── memory.py                       # Short-term memory (unchanged)
│   │                                   # Already uses save_memory_state duck-typing
│   └── ...
│
├── sessions/
│   ├── persistence.py                  # DEPRECATED - points to state/
│   ├── session.py                      # SessionManager (updated to use StateStore)
│   └── ...
│
├── cli/
│   ├── playground_state.py             # DEPRECATED - points to state/
│   ├── playground.py                   # Updated to use single StateStore
│   └── ...
```

### Export Structure

```python
# penguiflow/state/__init__.py
from .protocol import StateStore
from .models import (
    StoredEvent,
    RemoteBinding,
    TaskState,
    StateUpdate,
    SteeringEvent,
)
from .in_memory import InMemoryStateStore

__all__ = [
    "StateStore",
    "StoredEvent",
    "RemoteBinding",
    "TaskState",
    "StateUpdate",
    "SteeringEvent",
    "InMemoryStateStore",
]
```

**Note:** `penguiflow/state.py` (module) and `penguiflow/state/` (package) cannot coexist. The migration replaces the module with a package while keeping the import path `penguiflow.state` stable.

---

## Reference Implementation

`InMemoryStateStore` serves as the complete reference for downstream teams:

```python
# penguiflow/state/in_memory.py
"""Reference StateStore implementation for development and testing.

Downstream teams can use this as a template to implement production stores:
- PostgresStateStore for event/text data
- RedisStateStore for high-speed pause/resume
- S3ArtifactStore for binary blobs

See STATESTORE_IMPLEMENTATION_SPEC.md for full documentation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from penguiflow.artifacts import ArtifactStore, InMemoryArtifactStore
from penguiflow.planner import PlannerEvent, Trajectory

from .models import (
    RemoteBinding,
    StateUpdate,
    SteeringEvent,
    StoredEvent,
    TaskState,
)


class InMemoryStateStore:
    """Complete in-memory StateStore for development and testing.

    Implements all required and optional methods. Production implementations
    may choose which optional methods to support based on features needed.

    Storage backends suggestion for production:
    - Events, tasks, steering, trajectories → PostgreSQL (durable, queryable)
    - Planner state (pause/resume) → Redis (fast, TTL support)
    - Memory state → Redis or PostgreSQL (depends on scale)
    - Artifacts → S3/GCS (binary blobs, large files)
    """

    def __init__(self) -> None:
        # Required - Core events
        self._events: dict[str, list[StoredEvent]] = defaultdict(list)
        self._event_fingerprints: set[str] = set()
        self._bindings: dict[tuple[str, str | None, str], RemoteBinding] = {}

        # Optional - Planner pause/resume
        self._planner_state: dict[str, dict[str, Any]] = {}

        # Optional - Memory persistence
        self._memory_state: dict[str, dict[str, Any]] = {}

        # Optional - Task lifecycle
        self._tasks: dict[str, dict[str, TaskState]] = {}  # session_id -> task_id -> state
        self._task_updates: dict[str, list[StateUpdate]] = defaultdict(list)

        # Optional - Steering
        self._steering: dict[str, list[SteeringEvent]] = defaultdict(list)

        # Optional - Trajectories
        self._trajectories: dict[str, tuple[str, Trajectory]] = {}  # trace_id -> (session_id, trajectory)
        self._session_traces: dict[str, list[str]] = defaultdict(list)  # session_id -> [trace_ids]
        self._planner_events: dict[str, list[PlannerEvent]] = defaultdict(list)

        # Optional - Artifacts (composition)
        self._artifact_store = InMemoryArtifactStore()

        self._lock = asyncio.Lock()

    def _fingerprint_event(self, event: StoredEvent) -> str:
        """Return a deterministic fingerprint for idempotent event writes.

        Production stores should prefer a real event ID (or a DB-level unique
        constraint). This is a best-effort fallback when no explicit ID exists.
        """
        payload = json.dumps(dict(event.payload), sort_keys=True, default=str)
        raw = f"{event.trace_id}|{event.ts}|{event.kind}|{event.node_id}|{event.node_name}|{payload}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ═══════════════════════════════════════════════════════════════════
    # REQUIRED METHODS
    # ═══════════════════════════════════════════════════════════════════

    async def save_event(self, event: StoredEvent) -> None:
        """Persist a runtime event. Must be idempotent."""
        key = event.trace_id or "__global__"
        fp = self._fingerprint_event(event)
        async with self._lock:
            if fp in self._event_fingerprints:
                return
            self._event_fingerprints.add(fp)
            self._events[key].append(event)

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        """Load events ordered by timestamp. Returns [] if not found."""
        async with self._lock:
            events = list(self._events.get(trace_id, []))
        # Stable ordering: break ties deterministically.
        return sorted(events, key=lambda e: (e.ts, self._fingerprint_event(e)))

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        """Persist A2A binding. Must be idempotent."""
        async with self._lock:
            self._bindings[(binding.trace_id, binding.context_id, binding.task_id)] = binding

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Planner State
    # ═══════════════════════════════════════════════════════════════════

    async def save_planner_state(self, token: str, payload: dict[str, Any]) -> None:
        """Persist pause state. Production: use Redis with TTL."""
        async with self._lock:
            self._planner_state[token] = payload

    async def load_planner_state(self, token: str) -> dict[str, Any]:
        """Load and consume pause state. Returns {} if not found."""
        async with self._lock:
            return self._planner_state.pop(token, {})

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Memory State
    # ═══════════════════════════════════════════════════════════════════

    async def save_memory_state(self, key: str, state: dict[str, Any]) -> None:
        """Persist memory state. Key format: tenant:user:session"""
        async with self._lock:
            self._memory_state[key] = state

    async def load_memory_state(self, key: str) -> dict[str, Any] | None:
        """Load memory state. Returns None if not found."""
        async with self._lock:
            return self._memory_state.get(key)

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Task State
    # ═══════════════════════════════════════════════════════════════════

    async def save_task(self, state: TaskState) -> None:
        """Persist task state. Upsert semantics."""
        async with self._lock:
            if state.session_id not in self._tasks:
                self._tasks[state.session_id] = {}
            self._tasks[state.session_id][state.task_id] = state

    async def list_tasks(self, session_id: str) -> Sequence[TaskState]:
        """List all tasks for session."""
        async with self._lock:
            return list(self._tasks.get(session_id, {}).values())

    async def save_update(self, update: StateUpdate) -> None:
        """Persist task update."""
        async with self._lock:
            self._task_updates[update.session_id].append(update)

    async def list_updates(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[StateUpdate]:
        """List task updates with filtering."""
        async with self._lock:
            updates = list(self._task_updates.get(session_id, []))

        # Filter by since_id
        if since_id:
            start_idx = 0
            for idx, update in enumerate(updates):
                if update.update_id == since_id:
                    start_idx = idx + 1
                    break
            updates = updates[start_idx:]

        # Filter by task_id
        if task_id is not None:
            updates = [u for u in updates if u.task_id == task_id]

        return updates[-limit:]

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Steering
    # ═══════════════════════════════════════════════════════════════════

    async def save_steering(self, event: SteeringEvent) -> None:
        """Persist steering event."""
        async with self._lock:
            self._steering[event.session_id].append(event)

    async def list_steering(
        self,
        session_id: str,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> Sequence[SteeringEvent]:
        """List steering events with filtering."""
        async with self._lock:
            events = list(self._steering.get(session_id, []))

        if since_id:
            start_idx = 0
            for idx, event in enumerate(events):
                if event.event_id == since_id:
                    start_idx = idx + 1
                    break
            events = events[start_idx:]

        if task_id is not None:
            events = [e for e in events if e.task_id == task_id]

        return events[-limit:]

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Trajectories
    # ═══════════════════════════════════════════════════════════════════

    async def save_trajectory(
        self,
        trace_id: str,
        session_id: str,
        trajectory: Trajectory,
    ) -> None:
        """Persist conversation trajectory."""
        async with self._lock:
            self._trajectories[trace_id] = (session_id, trajectory)
            if trace_id not in self._session_traces[session_id]:
                self._session_traces[session_id].append(trace_id)

    async def get_trajectory(
        self,
        trace_id: str,
        session_id: str,
    ) -> Trajectory | None:
        """Load trajectory with session validation."""
        async with self._lock:
            entry = self._trajectories.get(trace_id)
            if entry is None:
                return None
            stored_session, trajectory = entry
            if stored_session != session_id:
                return None
            return trajectory

    async def list_traces(self, session_id: str, limit: int = 50) -> list[str]:
        """List trace IDs, most recent first."""
        async with self._lock:
            traces = list(self._session_traces.get(session_id, []))
        return list(reversed(traces))[:limit]

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Planner Events
    # ═══════════════════════════════════════════════════════════════════

    async def save_planner_event(self, trace_id: str, event: PlannerEvent) -> None:
        """Persist planner event (tool call, LLM response, etc.)."""
        async with self._lock:
            self._planner_events[trace_id].append(event)

    async def list_planner_events(self, trace_id: str) -> list[PlannerEvent]:
        """List planner events for trace."""
        async with self._lock:
            return list(self._planner_events.get(trace_id, []))

    # ═══════════════════════════════════════════════════════════════════
    # OPTIONAL - Artifacts
    # ═══════════════════════════════════════════════════════════════════

    @property
    def artifact_store(self) -> ArtifactStore:
        """Return artifact store for binary content."""
        return self._artifact_store

    # ═══════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ═══════════════════════════════════════════════════════════════════

    def clear(self) -> None:
        """Clear all stored data. Useful for testing."""
        self._events.clear()
        self._event_fingerprints.clear()
        self._bindings.clear()
        self._planner_state.clear()
        self._memory_state.clear()
        self._tasks.clear()
        self._task_updates.clear()
        self._steering.clear()
        self._trajectories.clear()
        self._session_traces.clear()
        self._planner_events.clear()
        self._artifact_store.clear()

    def clear_session(self, session_id: str) -> None:
        """Clear all data for a specific session."""
        self._tasks.pop(session_id, None)
        self._task_updates.pop(session_id, None)
        self._steering.pop(session_id, None)

        traces = self._session_traces.pop(session_id, [])
        for trace_id in traces:
            self._trajectories.pop(trace_id, None)
            self._planner_events.pop(trace_id, None)
            self._events.pop(trace_id, None)

        # Best-effort cleanup: recompute fingerprints for remaining events.
        remaining: set[str] = set()
        for bucket in self._events.values():
            for event in bucket:
                remaining.add(self._fingerprint_event(event))
        self._event_fingerprints = remaining


__all__ = ["InMemoryStateStore"]
```

---

## Migration Plan

### Phase 1: Create Package Structure (Non-Breaking)

1. Create `penguiflow/state/` package
2. Move `StateStore` protocol to `state/protocol.py`
3. Create `state/models.py` with all data classes
4. Create `state/in_memory.py` with `InMemoryStateStore`
5. Replace `penguiflow/state.py` module with the `penguiflow/state/` package (import path stays `penguiflow.state`)
6. All existing imports of `penguiflow.state` continue to work

### Phase 2: Consolidate Models (Non-Breaking)

1. Move `TaskState`, `StateUpdate` from `sessions/models.py` to `state/models.py`
2. Move `SteeringEvent` from `steering.py` to `state/models.py`
3. Update imports throughout codebase
4. Add re-exports for backward compatibility

### Phase 3: Update Playground (Breaking for Playground Internals)

1. Update `playground.py` to create single `InMemoryStateStore`
2. Remove separate `InMemorySessionStateStore` creation
3. Pass unified store to `SessionManager`, `AgentWrapper`
4. Update `SessionManager` to use `StateStore` duck-typed methods

### Phase 4: Deprecate Old Protocols

1. Mark `SessionStateStore` as deprecated
2. Mark `PlaygroundStateStore` as deprecated
3. Add deprecation warnings on import
4. Update documentation

### Phase 5: Update Spec

1. Expand `STATESTORE_IMPLEMENTATION_SPEC.md` with all optional methods
2. Add task, steering, trajectory sections
3. Add artifact property section
4. Update PostgreSQL schema recommendations
5. Update test cases

---

## Backward Compatibility

| Scenario | Impact |
|----------|--------|
| Existing `StateStore` implementations | **No change** - only required methods enforced |
| Code importing from `penguiflow.state` | **No change** - import path preserved (module becomes package) |
| Code using `SessionStateStore` | **Deprecation warning** - continues to work |
| Code using `PlaygroundStateStore` | **Deprecation warning** - continues to work |
| Playground internals | **Breaking** - updated to use unified store |

---

## Testing Strategy

### Unit Tests

```python
# tests/test_state/test_in_memory.py

@pytest.fixture
def store():
    return InMemoryStateStore()

# Required methods
async def test_save_and_load_events(store): ...
async def test_load_history_unknown_trace(store): ...
async def test_events_ordered_by_timestamp(store): ...
async def test_save_remote_binding(store): ...

# Optional - Planner state
async def test_planner_state_roundtrip(store): ...
async def test_planner_state_consumed(store): ...

# Optional - Memory
async def test_memory_state_roundtrip(store): ...
async def test_memory_state_unknown_key(store): ...

# Optional - Tasks
async def test_save_and_list_tasks(store): ...
async def test_updates_filtering(store): ...

# Optional - Steering
async def test_steering_events(store): ...
async def test_steering_filtering(store): ...

# Optional - Trajectories
async def test_trajectory_roundtrip(store): ...
async def test_list_traces(store): ...

# Optional - Planner events
async def test_planner_events(store): ...

# Optional - Artifacts
async def test_artifact_store_property(store): ...
```

### Integration Tests

```python
# Test playground with unified store
async def test_playground_unified_store(): ...

# Test SessionManager with StateStore
async def test_session_manager_with_statestore(): ...

# Test ReactPlanner memory persistence
async def test_planner_memory_with_statestore(): ...
```

---

## Open Questions

1. **Should we provide PostgreSQL reference implementation?**
   - Pro: Helps downstream teams
   - Con: Maintenance burden, database-specific

2. **Should artifacts be inline methods or property?**
   - Decision: Property (composition) - allows different backends

3. **Timeline for deprecation removal?**
   - Suggestion: v2.12 (two minor versions)

---

## References

- [STATESTORE_IMPLEMENTATION_SPEC.md](./STATESTORE_IMPLEMENTATION_SPEC.md) - Current spec (to be expanded)
- [RFC_BIDIRECTIONAL_PROTOCOL.md](./RFC_BIDIRECTIONAL_PROTOCOL.md) - Steering events
- [MEMORY_GUIDE.md](./MEMORY_GUIDE.md) - Short-term memory integration
- [RFC_MCP_BINARY_CONTENT_HANDLING.md](./RFC_MCP_BINARY_CONTENT_HANDLING.md) - Artifact storage and binary payload guidance

---

## Appendix: Method Summary

| Method | Required | Category | Purpose |
|--------|----------|----------|---------|
| `save_event` | Yes | Core | Persist FlowEvent |
| `load_history` | Yes | Core | Load events by trace |
| `save_remote_binding` | Yes | Core | Persist A2A binding |
| `save_planner_state` | No | Planner | Pause state |
| `load_planner_state` | No | Planner | Resume state |
| `save_memory_state` | No | Memory | Persist conversation memory |
| `load_memory_state` | No | Memory | Load conversation memory |
| `save_task` | No | Tasks | Task lifecycle |
| `list_tasks` | No | Tasks | Query tasks |
| `save_update` | No | Tasks | Task progress |
| `list_updates` | No | Tasks | Query updates |
| `save_steering` | No | Steering | Steering events |
| `list_steering` | No | Steering | Query steering |
| `save_trajectory` | No | Trajectory | Conversation history |
| `get_trajectory` | No | Trajectory | Load conversation |
| `list_traces` | No | Trajectory | Session traces |
| `save_planner_event` | No | Events | Tool/LLM events |
| `list_planner_events` | No | Events | Query events |
| `artifact_store` | No | Artifacts | Binary storage (property) |
