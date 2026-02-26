# Phase 005: Wire `ScopedArtifacts` into Test Fixtures and Jinja Templates

## Objective
Add the `artifacts` property (returning `ScopedArtifacts`) to all test fixture context classes so they satisfy the updated `ToolContext` protocol. Also update the Jinja templates for `penguiflow new` with stub properties. After this phase, all `ToolContext` implementations (production and test) have both `_artifacts` and `artifacts`. This is Enhancement 3, part 3.

## Tasks
1. Update `DummyContext` in `tests/test_rich_output_nodes.py`
2. Update `DummyCtx` in `tests/test_toolnode_phase1.py`
3. Update `DummyCtx` in `tests/test_toolnode_phase2.py`
4. Update `DummyContext` in `tests/test_task_tools.py`
5. Update `_FakeCtx` in `tests/a2a/test_a2a_planner_tools.py`
6. Update `penguiflow/cli/templates/conftest.py.jinja`
7. Update `penguiflow/templates/new/react/tests/conftest.py.jinja`

## Detailed Steps

### Step 1: Update `DummyContext` -- `tests/test_rich_output_nodes.py`
- Add `ScopedArtifacts` import: `from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts`
- In `__init__`, after `self._artifacts_store = InMemoryArtifactStore()` (renamed in Phase 001), add:
```python
        self._scoped_artifacts = ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```
- Add an `artifacts` property:
```python
    @property
    def artifacts(self):  # type: ignore[no-untyped-def]
        return self._scoped_artifacts
```

### Step 2: Update `DummyCtx` -- `tests/test_toolnode_phase1.py`
- Add import: `from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts`
  (update the existing `from penguiflow.artifacts import InMemoryArtifactStore` line)
- Add `artifacts` property that returns a `ScopedArtifacts` facade wrapping `self._artifacts_store`:
```python
    @property
    def artifacts(self):
        return ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

### Step 3: Update `DummyCtx` -- `tests/test_toolnode_phase2.py`
- Add import: `from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts`
  (update the existing `from penguiflow.artifacts import InMemoryArtifactStore` line)
- Add `artifacts` property (same pattern as Step 2):
```python
    @property
    def artifacts(self):
        return ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

### Step 4: Update `DummyContext` -- `tests/test_task_tools.py`
- Add import: `from penguiflow.artifacts import NoOpArtifactStore, ScopedArtifacts`
- Add `artifacts` property that returns a `ScopedArtifacts` wrapping `NoOpArtifactStore` with all-None scope:
```python
    @property
    def artifacts(self):
        return ScopedArtifacts(
            NoOpArtifactStore(),
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

### Step 5: Update `_FakeCtx` -- `tests/a2a/test_a2a_planner_tools.py`
- Add `ScopedArtifacts` to the existing `from penguiflow.artifacts import NoOpArtifactStore` import (added in Phase 002):
  - After: `from penguiflow.artifacts import NoOpArtifactStore, ScopedArtifacts`
- Add `artifacts` property:
```python
    @property
    def artifacts(self) -> Any:  # pragma: no cover - not used
        return ScopedArtifacts(
            NoOpArtifactStore(),
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

### Step 6: Update `penguiflow/cli/templates/conftest.py.jinja`
- Add the following stub methods/properties to the `DummyToolContext` dataclass, after the existing `record_status` method:

```python
    @property
    def _artifacts(self) -> Any:
        """Raw artifact store stub -- not used in generated project tests."""
        return None

    @property
    def artifacts(self) -> Any:
        """Scoped artifacts stub -- not used in generated project tests."""
        return None

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Artifact streaming stub -- not used in generated project tests."""
        del stream_id, chunk, done, artifact_type, meta
```

### Step 7: Update `penguiflow/templates/new/react/tests/conftest.py.jinja`
- Same changes as Step 6 -- identical `DummyToolContext` dataclass.

### Step 8: Note on `MinimalCtx` in `penguiflow/cli/playground.py`
- **No changes needed.** `MinimalCtx` is only used for `read_resource()` which accesses `ctx._artifacts` (renamed in Phase 001). It does NOT need a `ScopedArtifacts` facade. This is a pre-existing protocol violation that is acceptable.

## Required Code

```python
# Target file: tests/test_rich_output_nodes.py
# Update import:
from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts

# In DummyContext.__init__, after self._artifacts_store = InMemoryArtifactStore():
        self._scoped_artifacts = ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )

# Add property after _artifacts property:
    @property
    def artifacts(self):  # type: ignore[no-untyped-def]
        return self._scoped_artifacts
```

```python
# Target file: tests/test_toolnode_phase1.py
# Update import:
from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts

# Add property after _artifacts property in DummyCtx:
    @property
    def artifacts(self):
        return ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

```python
# Target file: tests/test_toolnode_phase2.py
# Update import:
from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts

# Add property after _artifacts property in DummyCtx:
    @property
    def artifacts(self):
        return ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

```python
# Target file: tests/test_task_tools.py
# Add import:
from penguiflow.artifacts import NoOpArtifactStore, ScopedArtifacts

# Add property in DummyContext, after _artifacts property:
    @property
    def artifacts(self):
        return ScopedArtifacts(
            NoOpArtifactStore(),
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

```python
# Target file: tests/a2a/test_a2a_planner_tools.py
# Update import:
from penguiflow.artifacts import NoOpArtifactStore, ScopedArtifacts

# Add property in _FakeCtx, after _artifacts property:
    @property
    def artifacts(self) -> Any:  # pragma: no cover - not used
        return ScopedArtifacts(
            NoOpArtifactStore(),
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
```

```python
# Target file: penguiflow/cli/templates/conftest.py.jinja
# Add to DummyToolContext dataclass, after record_status method:

    @property
    def _artifacts(self) -> Any:
        """Raw artifact store stub -- not used in generated project tests."""
        return None

    @property
    def artifacts(self) -> Any:
        """Scoped artifacts stub -- not used in generated project tests."""
        return None

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Artifact streaming stub -- not used in generated project tests."""
        del stream_id, chunk, done, artifact_type, meta
```

```python
# Target file: penguiflow/templates/new/react/tests/conftest.py.jinja
# Add identical stubs to DummyToolContext dataclass (same as above).
```

## Exit Criteria (Success)
- [ ] `DummyContext` in `tests/test_rich_output_nodes.py` has both `_artifacts` and `artifacts` properties
- [ ] `DummyCtx` in `tests/test_toolnode_phase1.py` has both `_artifacts` and `artifacts` properties
- [ ] `DummyCtx` in `tests/test_toolnode_phase2.py` has both `_artifacts` and `artifacts` properties
- [ ] `DummyContext` in `tests/test_task_tools.py` has both `_artifacts` and `artifacts` properties
- [ ] `_FakeCtx` in `tests/a2a/test_a2a_planner_tools.py` has both `_artifacts` and `artifacts` properties
- [ ] Both Jinja templates have `_artifacts`, `artifacts`, and `emit_artifact` stubs
- [ ] `uv run ruff check .` passes with zero errors
- [ ] `uv run mypy` passes with zero new errors
- [ ] `uv run pytest tests/` passes with no new failures (pre-existing 21 failures allowed)

## Implementation Notes
- Test fixtures use all-`None` scope fields since they don't have real tenant/user/session/trace context.
- `test_toolnode_phase1.py` and `test_toolnode_phase2.py` create the `ScopedArtifacts` inline in the property (not cached in `__init__`). This is acceptable for test fixtures.
- `test_task_tools.py` and `test_a2a_planner_tools.py` wrap `NoOpArtifactStore()` since they don't use artifacts.
- The `emit_artifact` stub in the Jinja templates is a pre-existing gap being fixed as a convenience since we are already touching these templates.
- `MinimalCtx` in `playground.py` is intentionally NOT updated -- it only needs `_artifacts`.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv run ruff check .
uv run mypy
uv run pytest tests/ -x -q
```
