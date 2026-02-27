# Phase 004: Wire `ScopedArtifacts` into Production Contexts

## Objective
Add the new `artifacts` property (returning `ScopedArtifacts`) to the `ToolContext` protocol definition, `_PlannerContext`, and `ToolJobContext`. After this phase, production code has both `_artifacts` (raw store, for internal framework use) and `artifacts` (scoped facade, for tool developers). This is Enhancement 3, part 2.

## Tasks
1. Add `artifacts` property to `ToolContext` protocol in `penguiflow/planner/context.py`
2. Wire `ScopedArtifacts` into `_PlannerContext` in `penguiflow/planner/planner_context.py`
3. Wire `ScopedArtifacts` into `ToolJobContext` in `penguiflow/sessions/tool_jobs.py`

## Detailed Steps

### Step 1: Add `artifacts` to `ToolContext` protocol -- `penguiflow/planner/context.py`
- Add `ScopedArtifacts` to the `TYPE_CHECKING` import block (line 9):
  - Before: `from penguiflow.artifacts import ArtifactStore`
  - After: `from penguiflow.artifacts import ArtifactStore, ScopedArtifacts`
- Add a new `artifacts` property to the `ToolContext` protocol class, alongside the existing `_artifacts` property. Place it right after `_artifacts`:

```python
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers.

        Example:
            ref = await ctx.artifacts.upload(
                pdf_bytes,
                mime_type="application/pdf",
                filename="report.pdf",
            )
            return {"artifact": ref, "summary": "Downloaded PDF"}
        """
```

### Step 2: Wire into `_PlannerContext` -- `penguiflow/planner/planner_context.py`
- Add `ScopedArtifacts` to the import from `..artifacts` (line 11):
  - Before: `from ..artifacts import ArtifactStore`
  - After: `from ..artifacts import ArtifactStore, ScopedArtifacts`
- Add `"_scoped_artifacts"` to `__slots__` (line 23).
- In `__init__`, after constructing `_artifact_proxy` (after line 50), build the facade:

```python
        self._scoped_artifacts = ScopedArtifacts(
            self._artifact_proxy,
            tenant_id=str(self._tool_context["tenant_id"]) if self._tool_context.get("tenant_id") is not None else None,
            user_id=str(self._tool_context["user_id"]) if self._tool_context.get("user_id") is not None else None,
            session_id=str(self._tool_context["session_id"]) if self._tool_context.get("session_id") is not None else None,
            trace_id=str(self._tool_context["trace_id"]) if self._tool_context.get("trace_id") is not None else None,
        )
```

- Add a new `artifacts` property returning `self._scoped_artifacts`. Place it right after the existing `_artifacts` property:

```python
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers."""
        return self._scoped_artifacts
```

### Step 3: Wire into `ToolJobContext` -- `penguiflow/sessions/tool_jobs.py`
- Add `ScopedArtifacts` to the import from `penguiflow.artifacts` (line 22):
  - Before: `from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore`
  - After: `from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore, ScopedArtifacts`
- In `__init__`, after the existing attribute assignments (after `self._kv = None`, line 45), build the facade:

```python
        self._scoped_artifacts = ScopedArtifacts(
            self._artifacts_store,
            tenant_id=str(tool_context["tenant_id"]) if tool_context.get("tenant_id") is not None else None,
            user_id=str(tool_context["user_id"]) if tool_context.get("user_id") is not None else None,
            session_id=str(tool_context["session_id"]) if tool_context.get("session_id") is not None else None,
            trace_id=str(tool_context["trace_id"]) if tool_context.get("trace_id") is not None else None,
        )
```

- Add a new `artifacts` property. Place it right after the existing `_artifacts` property:

```python
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers."""
        return self._scoped_artifacts
```

## Required Code

```python
# Target file: penguiflow/planner/context.py
# Update TYPE_CHECKING import (line 9):
    from penguiflow.artifacts import ArtifactStore, ScopedArtifacts

# Add after the _artifacts property in the ToolContext protocol:
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers.

        Example:
            ref = await ctx.artifacts.upload(
                pdf_bytes,
                mime_type="application/pdf",
                filename="report.pdf",
            )
            return {"artifact": ref, "summary": "Downloaded PDF"}
        """
```

```python
# Target file: penguiflow/planner/planner_context.py
# Update import (line 11):
from ..artifacts import ArtifactStore, ScopedArtifacts

# Add "_scoped_artifacts" to __slots__

# In __init__, after _artifact_proxy construction and before self._meta_warned:
        self._scoped_artifacts = ScopedArtifacts(
            self._artifact_proxy,
            tenant_id=str(self._tool_context["tenant_id"]) if self._tool_context.get("tenant_id") is not None else None,
            user_id=str(self._tool_context["user_id"]) if self._tool_context.get("user_id") is not None else None,
            session_id=str(self._tool_context["session_id"]) if self._tool_context.get("session_id") is not None else None,
            trace_id=str(self._tool_context["trace_id"]) if self._tool_context.get("trace_id") is not None else None,
        )

# Add after _artifacts property:
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers."""
        return self._scoped_artifacts
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Update import (line 22):
from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore, ScopedArtifacts

# In __init__, after self._kv = None:
        self._scoped_artifacts = ScopedArtifacts(
            self._artifacts_store,
            tenant_id=str(tool_context["tenant_id"]) if tool_context.get("tenant_id") is not None else None,
            user_id=str(tool_context["user_id"]) if tool_context.get("user_id") is not None else None,
            session_id=str(tool_context["session_id"]) if tool_context.get("session_id") is not None else None,
            trace_id=str(tool_context["trace_id"]) if tool_context.get("trace_id") is not None else None,
        )

# Add after _artifacts property:
    @property
    def artifacts(self) -> ScopedArtifacts:
        """Scoped artifact facade for tool developers."""
        return self._scoped_artifacts
```

## Exit Criteria (Success)
- [ ] `ToolContext` protocol has both `_artifacts` (returns `ArtifactStore`) and `artifacts` (returns `ScopedArtifacts`) properties
- [ ] `ScopedArtifacts` is imported under `TYPE_CHECKING` in `context.py`
- [ ] `_PlannerContext` has `"_scoped_artifacts"` in `__slots__`, constructs facade in `__init__`, and exposes `artifacts` property
- [ ] `_PlannerContext` extracts scope from `self._tool_context` dict (tenant_id, user_id, session_id, trace_id)
- [ ] `ToolJobContext` constructs facade in `__init__` and exposes `artifacts` property
- [ ] `ToolJobContext` extracts scope from `tool_context` dict
- [ ] `uv run ruff check penguiflow/planner/context.py penguiflow/planner/planner_context.py penguiflow/sessions/tool_jobs.py` passes
- [ ] `uv run mypy` passes with zero new errors

## Implementation Notes
- The `ScopedArtifacts` class was created in Phase 003 and must exist before this phase runs.
- The scope values are extracted from `tool_context` dict using `.get()` with `is not None` checks. Values are cast to `str()` because `tool_context` values may be non-string types. If a key is missing or `None`, the facade gets `None` for that dimension.
- `_PlannerContext` wraps `self._artifact_proxy` (the event-emitting proxy), so artifact events still fire for both `ctx._artifacts` and `ctx.artifacts.upload()` calls.
- `ToolJobContext` wraps `self._artifacts_store` (the raw store or `NoOpArtifactStore`).
- The `kv` property in both contexts continues to use the raw store (not the facade). `SessionKVFacade` expects `ArtifactStore`, not `ScopedArtifacts`.
- `MinimalCtx` in `playground.py` does NOT need an `artifacts` property -- see Phase 005 implementation notes.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv run ruff check penguiflow/planner/context.py penguiflow/planner/planner_context.py penguiflow/sessions/tool_jobs.py
uv run mypy
# Note: pytest may fail until test fixtures are updated in Phase 005
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-26

### Summary of Changes
- **`penguiflow/planner/context.py`**: Added `ScopedArtifacts` to the `TYPE_CHECKING` import block. Added `artifacts` property (returning `ScopedArtifacts`) to the `ToolContext` protocol, placed directly after the existing `_artifacts` property, with a docstring and usage example.
- **`penguiflow/planner/planner_context.py`**: Added `ScopedArtifacts` to the runtime import from `..artifacts`. Added `"_scoped_artifacts"` to `__slots__`. Constructed `ScopedArtifacts` facade in `__init__` using scope values extracted from `self._tool_context`. Added `artifacts` property returning `self._scoped_artifacts`.
- **`penguiflow/sessions/tool_jobs.py`**: Added `ScopedArtifacts` to the runtime import from `penguiflow.artifacts`. Constructed `ScopedArtifacts` facade in `__init__` using scope values extracted from the `tool_context` parameter. Added `artifacts` property returning `self._scoped_artifacts`.

### Key Considerations
- In `_PlannerContext`, the plan's code used `self._tool_context["key"]` directly in long ternary expressions, which caused the `session_id` line to exceed the 120-character ruff line-length limit. I introduced a local variable `tc = self._tool_context` to shorten the lines while preserving identical semantics. This is a style-only change to satisfy ruff E501.
- The `ScopedArtifacts` import in `context.py` is under `TYPE_CHECKING` (matching the existing pattern for `ArtifactStore`), while in `planner_context.py` and `tool_jobs.py` it is a runtime import because the classes actually construct `ScopedArtifacts` instances.
- The `artifacts` property in the protocol has no `...` body -- it follows the same pattern as the existing `_artifacts` property in the protocol.

### Assumptions
- `ScopedArtifacts` was already implemented in Phase 003 and is exported from `penguiflow/artifacts.py`. Verified this was the case.
- The `tool_context` dicts may or may not contain `tenant_id`, `user_id`, `session_id`, `trace_id` keys. The `.get()` with `is not None` pattern handles both missing keys and explicit `None` values gracefully.
- Existing tests may need updates in Phase 005 to account for the new `artifacts` property on the protocol, but that is out of scope for this phase.

### Deviations from Plan
- **Line-length fix in `planner_context.py`**: The plan's verbatim code for `_PlannerContext.__init__` used `self._tool_context["session_id"]` and `self._tool_context.get("session_id")` in a single line that was 123 characters, exceeding the 120-character ruff limit. I extracted `self._tool_context` into a local `tc` variable for the `ScopedArtifacts` constructor call. All four scope keyword arguments use `tc` consistently for readability.

### Potential Risks & Reviewer Attention Points
- **Test fixtures**: As noted in the phase file, pytest may fail until test fixtures are updated in Phase 005, since any test constructing `ToolJobContext` or `_PlannerContext` now requires `tool_context` dicts to be compatible with `ScopedArtifacts` construction (the `.get()` calls are safe, but the `ScopedArtifacts` constructor itself is called unconditionally).
- **`ToolJobContext.__init__` parameter shadowing**: The `__init__` parameter is named `artifacts` (type `ArtifactStore | None`) while the new property is also named `artifacts` (type `ScopedArtifacts`). This is not a conflict because the parameter is only used during `__init__` and the property reads from `self._scoped_artifacts`, but reviewers should note the naming overlap.

### Files Modified
- `penguiflow/planner/context.py`
- `penguiflow/planner/planner_context.py`
- `penguiflow/sessions/tool_jobs.py`
