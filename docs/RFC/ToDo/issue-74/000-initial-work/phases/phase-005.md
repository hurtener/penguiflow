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

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-26

### Summary of Changes
- **tests/test_rich_output_nodes.py**: Added `ScopedArtifacts` to the import from `penguiflow.artifacts`. Added `self._scoped_artifacts` construction in `DummyContext.__init__` and a new `artifacts` property returning it.
- **tests/test_toolnode_phase1.py**: Added `ScopedArtifacts` to the import from `penguiflow.artifacts`. Added `artifacts` property to `DummyCtx` that creates a `ScopedArtifacts` inline with all-None scope fields.
- **tests/test_toolnode_phase2.py**: Added `ScopedArtifacts` to the import from `penguiflow.artifacts`. Added `artifacts` property to `DummyCtx` that creates a `ScopedArtifacts` inline with all-None scope fields.
- **tests/test_task_tools.py**: Added new import `from penguiflow.artifacts import NoOpArtifactStore, ScopedArtifacts`. Added `artifacts` property to `DummyContext` wrapping `NoOpArtifactStore()`.
- **tests/a2a/test_a2a_planner_tools.py**: Added `ScopedArtifacts` to the existing `NoOpArtifactStore` import. Added `artifacts` property to `_FakeCtx` with `# pragma: no cover` annotation.
- **penguiflow/cli/templates/conftest.py.jinja**: Added `_artifacts` property, `artifacts` property, and `emit_artifact` async method stubs to `DummyToolContext` dataclass.
- **penguiflow/templates/new/react/tests/conftest.py.jinja**: Same additions as the cli template above.

### Key Considerations
- The `ScopedArtifacts` constructor uses keyword-only arguments (after `*`) for `tenant_id`, `user_id`, `session_id`, `trace_id`. The phase plan code snippets correctly pass `store` positionally and the scope fields as keyword arguments.
- For `test_rich_output_nodes.py`, the `ScopedArtifacts` is cached in `__init__` (as `self._scoped_artifacts`) because this test file actually uses the artifact store in tests. For `test_toolnode_phase1.py` and `test_toolnode_phase2.py`, the `ScopedArtifacts` is created inline in the property getter, which is acceptable for test fixtures as noted in the plan.
- For `test_task_tools.py` and `test_a2a_planner_tools.py`, `NoOpArtifactStore` is used since these test files do not exercise artifacts.
- The Jinja template stubs return `None` for both `_artifacts` and `artifacts` since generated project tests do not exercise artifact functionality.

### Assumptions
- The pre-existing 21 test failures (all in `test_databricks_provider.py`, `test_llm_provider_databricks.py`, and `test_llm_provider_google.py`) are unrelated to this phase and acceptable.
- The `ScopedArtifacts` class was already implemented in a prior phase (Phase 003) and is available in `penguiflow.artifacts`.
- The `_artifacts` property was already renamed from `artifacts` to `_artifacts` in a prior phase (Phase 001) across all these test fixtures.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The `artifacts` property in `test_toolnode_phase1.py` and `test_toolnode_phase2.py` creates a new `ScopedArtifacts` instance on every access. This is fine for tests but would be wasteful in production code.
- The `artifacts` property in `test_task_tools.py` and `test_a2a_planner_tools.py` creates both a new `NoOpArtifactStore()` and a new `ScopedArtifacts` on every access. Again, acceptable for test fixtures.
- The Jinja templates' `_artifacts` and `artifacts` return `None`. If a generated project's tools begin using `ctx.artifacts`, the developer will need to replace these stubs with real implementations.

### Files Modified
- `tests/test_rich_output_nodes.py`
- `tests/test_toolnode_phase1.py`
- `tests/test_toolnode_phase2.py`
- `tests/test_task_tools.py`
- `tests/a2a/test_a2a_planner_tools.py`
- `penguiflow/cli/templates/conftest.py.jinja`
- `penguiflow/templates/new/react/tests/conftest.py.jinja`
