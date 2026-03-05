# Phase 001: Rename `artifacts` to `_artifacts` -- Protocol, Production Contexts, and Call Sites

## Objective
Begin the atomic rename of the `artifacts` property to `_artifacts` across the protocol definition, all production context implementations, and all `ctx.artifacts` call sites in production code. This is the first half of Enhancement 2. The rename MUST NOT be verified in isolation -- Phase 002 completes the rename for test fixtures and runs the checkpoint. Do NOT run pytest/ruff/mypy until Phase 002 is also complete.

## Tasks
1. Rename `artifacts` property to `_artifacts` in `ToolContext` protocol
2. Rename `artifacts` property to `_artifacts` in `_PlannerContext` and fix `kv` call site
3. Rename `artifacts` property to `_artifacts` in `ToolJobContext` and rename internal attribute
4. Migrate all 9 `ctx.artifacts` call sites in `penguiflow/tools/node.py`
5. Migrate 1 `ctx.artifacts` call site in `penguiflow/sessions/tool_jobs.py`
6. Rename in `MinimalCtx` in `penguiflow/cli/playground.py`
7. Rename in `DummyContext` in `tests/test_rich_output_nodes.py` and migrate call site

## Detailed Steps

### Step 1: Rename in `ToolContext` protocol -- `penguiflow/planner/context.py`
- Rename the `artifacts` property (line 86) to `_artifacts`.
- Change the docstring to indicate this is for framework-internal use.
- Keep return type as `ArtifactStore`.
- Do NOT add `ScopedArtifacts` import yet -- that comes in Phase 004.

Before:
```python
    @property
    def artifacts(self) -> ArtifactStore:
        """Binary/large-text artifact storage.

        Use this to store binary content (PDFs, images) or large text
        out-of-band, keeping only compact ArtifactRef in LLM context.

        Example:
            ref = await ctx.artifacts.put_bytes(
                pdf_bytes,
                mime_type="application/pdf",
                filename="report.pdf",
            )
            return {"artifact": ref, "summary": "Downloaded PDF"}
        """
```

After:
```python
    @property
    def _artifacts(self) -> ArtifactStore:
        """Raw artifact store for framework-internal use."""
```

### Step 2: Rename in `_PlannerContext` -- `penguiflow/planner/planner_context.py`
- Rename the `artifacts` property (line 74) to `_artifacts`.
- **CRITICAL -- `kv` property (line 102):** Change `artifacts=self.artifacts` to `artifacts=self._artifact_proxy`. Using the raw proxy attribute directly is more explicit and avoids going through the property layer.

Before (property):
```python
    @property
    def artifacts(self) -> ArtifactStore:
        ...
        return self._artifact_proxy
```

After (property):
```python
    @property
    def _artifacts(self) -> ArtifactStore:
        """Raw artifact store for framework-internal use."""
        return self._artifact_proxy
```

Before (kv, line 102):
```python
                artifacts=self.artifacts,
```

After (kv):
```python
                artifacts=self._artifact_proxy,
```

### Step 3: Rename in `ToolJobContext` -- `penguiflow/sessions/tool_jobs.py`
- Rename internal attribute `self._artifacts` to `self._artifacts_store` in `__init__` (line 42):
  - Before: `self._artifacts = artifacts or NoOpArtifactStore()`
  - After: `self._artifacts_store = artifacts or NoOpArtifactStore()`
- Rename the `artifacts` property (line 61) to `_artifacts`:
  - Before: `return self._artifacts` (referred to the old instance attribute)
  - After: `return self._artifacts_store`
- The `kv` property (line 71) currently has `artifacts=self._artifacts`. After the rename, `self._artifacts` resolves to the new **property** `_artifacts` (which returns `self._artifacts_store`). This still produces the correct value. **No change needed on line 71.**

### Step 4: Migrate call sites in `penguiflow/tools/node.py`
- Use search-and-replace to change all 9 occurrences of `ctx.artifacts.` and `ctx.artifacts,` to `ctx._artifacts.` and `ctx._artifacts,`:
  - Lines 984, 1093, 1119, 1193: `ctx.artifacts.put_bytes(` -> `ctx._artifacts.put_bytes(`
  - Lines 1308, 1343: `ctx.artifacts.put_text(` -> `ctx._artifacts.put_text(`
  - Lines 1671, 1685: `ctx.artifacts.put_bytes(` / `ctx.artifacts.put_text(` -> `ctx._artifacts.*`
  - Line 1615: `artifact_store=ctx.artifacts,` -> `artifact_store=ctx._artifacts,`

### Step 5: Migrate call site in `penguiflow/sessions/tool_jobs.py`
- Line 275: `artifact_store=ctx.artifacts,` -> `artifact_store=ctx._artifacts,`

### Step 6: Rename in `MinimalCtx` -- `penguiflow/cli/playground.py`
- Rename `self._artifacts` attribute (line 2124) to `self._artifacts_store`:
  - Before: `self._artifacts = artifacts`
  - After: `self._artifacts_store = artifacts`
- Rename `artifacts` property (line 2127) to `_artifacts`, returning `self._artifacts_store`:
  - Before: `def artifacts(self) -> Any: return self._artifacts`
  - After: `def _artifacts(self) -> Any: return self._artifacts_store`
- **CRITICAL:** Without this change, playground resource reads will break at runtime because `read_resource` (node.py line 1615) accesses `ctx._artifacts`.

### Step 7: Rename in `DummyContext` -- `tests/test_rich_output_nodes.py`
- Rename `self._artifacts` attribute (line 26) to `self._artifacts_store`:
  - Before: `self._artifacts = InMemoryArtifactStore()`
  - After: `self._artifacts_store = InMemoryArtifactStore()`
- Rename `artifacts` property (line 35) to `_artifacts`, returning `self._artifacts_store`:
  - Before: `def artifacts(self): return self._artifacts`
  - After: `def _artifacts(self): return self._artifacts_store`
- Rename call site on line 193: `ctx.artifacts.put_text(` -> `ctx._artifacts.put_text(`

### Step 8: Rename in `DummyContext` -- `tests/test_task_tools.py`
- Rename `artifacts` property (line 27) to `_artifacts`:
  - Before: `def artifacts(self): raise RuntimeError("not_used")`
  - After: `def _artifacts(self): raise RuntimeError("not_used")`

## Required Code

```python
# Target file: penguiflow/planner/context.py
# Replace the artifacts property (lines 85-99) with:

    @property
    def _artifacts(self) -> ArtifactStore:
        """Raw artifact store for framework-internal use."""
```

```python
# Target file: penguiflow/planner/planner_context.py
# Replace the artifacts property (lines 73-83) with:

    @property
    def _artifacts(self) -> ArtifactStore:
        """Raw artifact store for framework-internal use."""
        return self._artifact_proxy

# Replace line 102 in the kv property:
#   artifacts=self.artifacts,
# with:
#   artifacts=self._artifact_proxy,
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# In __init__ (line 42), rename attribute:
#   self._artifacts = artifacts or NoOpArtifactStore()
# to:
#   self._artifacts_store = artifacts or NoOpArtifactStore()

# Replace the artifacts property (lines 60-62) with:
    @property
    def _artifacts(self) -> ArtifactStore:
        return self._artifacts_store

# Line 275: change ctx.artifacts to ctx._artifacts:
#   artifact_store=ctx.artifacts,
# to:
#   artifact_store=ctx._artifacts,
```

```python
# Target file: penguiflow/tools/node.py
# Replace ALL 9 occurrences (use replace-all):
#   ctx.artifacts.put_bytes  ->  ctx._artifacts.put_bytes
#   ctx.artifacts.put_text   ->  ctx._artifacts.put_text
#   ctx.artifacts,           ->  ctx._artifacts,
# Specifically: ctx.artifacts. -> ctx._artifacts.  and  ctx.artifacts, -> ctx._artifacts,
```

```python
# Target file: penguiflow/cli/playground.py
# In MinimalCtx.__init__ (line 2124):
#   self._artifacts = artifacts
# to:
#   self._artifacts_store = artifacts

# Replace the artifacts property (lines 2126-2128) with:
            @property
            def _artifacts(self) -> Any:
                return self._artifacts_store
```

```python
# Target file: tests/test_rich_output_nodes.py
# In DummyContext.__init__ (line 26):
#   self._artifacts = InMemoryArtifactStore()
# to:
#   self._artifacts_store = InMemoryArtifactStore()

# Replace the artifacts property (lines 34-36) with:
    @property
    def _artifacts(self):  # type: ignore[no-untyped-def]
        return self._artifacts_store

# Line 193:
#   ref = await ctx.artifacts.put_text(
# to:
#   ref = await ctx._artifacts.put_text(
```

```python
# Target file: tests/test_task_tools.py
# Replace the artifacts property (lines 26-28) with:
    @property
    def _artifacts(self):
        raise RuntimeError("not_used")
```

## Exit Criteria (Success)
- [ ] `ToolContext` protocol has `_artifacts` property (not `artifacts`)
- [ ] `_PlannerContext` has `_artifacts` property, `kv` uses `self._artifact_proxy`
- [ ] `ToolJobContext` uses `self._artifacts_store` internally, exposes `_artifacts` property
- [ ] All 9 call sites in `penguiflow/tools/node.py` use `ctx._artifacts`
- [ ] Line 275 in `penguiflow/sessions/tool_jobs.py` uses `ctx._artifacts`
- [ ] `MinimalCtx` in `penguiflow/cli/playground.py` uses `self._artifacts_store` and `_artifacts` property
- [ ] `DummyContext` in `tests/test_rich_output_nodes.py` uses `self._artifacts_store` and `_artifacts` property; call site on line 193 uses `ctx._artifacts`
- [ ] `DummyContext` in `tests/test_task_tools.py` has `_artifacts` property
- [ ] **Do NOT run verification yet** -- Phase 002 must be completed first (remaining test fixtures)

## Implementation Notes
- **ATOMICITY WARNING:** Do NOT run `pytest`, `ruff`, or `mypy` after this phase alone. The rename is incomplete until Phase 002 updates the remaining test fixtures. Running tests now will fail because `DummyCtx` in `test_toolnode_phase1.py`, `test_toolnode_phase2.py`, and `_FakeCtx` in `test_a2a_planner_tools.py` still have the old `artifacts` property.
- The `kv` property in `ToolJobContext` (line 71) has `artifacts=self._artifacts`. After the rename, `self._artifacts` resolves to the new property `_artifacts` (which returns `self._artifacts_store`), so it still works correctly. Do NOT change it.
- The docstring example in `context.py` that previously showed `ctx.artifacts.put_bytes(...)` is removed in this phase (the property docstring is simplified). A new docstring with `ctx.artifacts.upload(...)` will be added in Phase 004 when the `artifacts` property is re-added for `ScopedArtifacts`.

## Verification Commands
```bash
# Do NOT run these yet -- wait until Phase 002 is complete.
# These are here for reference only.
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
- **`penguiflow/planner/context.py`**: Renamed `artifacts` property to `_artifacts` in the `ToolContext` protocol. Replaced the multi-line docstring with a single-line "Raw artifact store for framework-internal use." docstring.
- **`penguiflow/planner/planner_context.py`**: Renamed `artifacts` property to `_artifacts` in `_PlannerContext`. Replaced the multi-line docstring with the simplified internal-use docstring. Changed `kv` property to pass `self._artifact_proxy` directly instead of going through `self.artifacts`.
- **`penguiflow/sessions/tool_jobs.py`**: Renamed internal attribute `self._artifacts` to `self._artifacts_store` in `ToolJobContext.__init__`. Renamed `artifacts` property to `_artifacts`, returning `self._artifacts_store`. Updated the `ctx.artifacts` call site on line 275 to `ctx._artifacts`. Left the `kv` property's `artifacts=self._artifacts` unchanged as it now resolves to the new `_artifacts` property correctly.
- **`penguiflow/tools/node.py`**: Replaced all 9 occurrences of `ctx.artifacts.` and `ctx.artifacts,` with `ctx._artifacts.` and `ctx._artifacts,` using replace-all.
- **`penguiflow/cli/playground.py`**: In `MinimalCtx`, renamed `self._artifacts` to `self._artifacts_store` and renamed `artifacts` property to `_artifacts`.
- **`tests/test_rich_output_nodes.py`**: In `DummyContext`, renamed `self._artifacts` to `self._artifacts_store`, renamed `artifacts` property to `_artifacts`, and updated the `ctx.artifacts.put_text(` call site to `ctx._artifacts.put_text(`.
- **`tests/test_task_tools.py`**: In `DummyContext`, renamed `artifacts` property to `_artifacts`.

### Key Considerations
- The `kv` property in `ToolJobContext` (line 71) was intentionally left unchanged. After renaming the instance attribute from `self._artifacts` to `self._artifacts_store`, the reference `self._artifacts` in the `kv` property now resolves to the new `_artifacts` **property** (not the old instance attribute). This property returns `self._artifacts_store`, so the behavior is preserved.
- The `kv` property in `_PlannerContext` was changed from `self.artifacts` to `self._artifact_proxy` (the raw instance attribute) rather than `self._artifacts` (the new property). This matches the plan's guidance to be more explicit and avoid going through the property layer.
- Used `replace_all=true` for the `node.py` changes since all `ctx.artifacts.` and `ctx.artifacts,` patterns needed the same transformation, and there were no false-positive matches.

### Assumptions
- The plan explicitly states not to run verification (pytest, ruff, mypy) after this phase. This is because other test files (`test_toolnode_phase1.py`, `test_toolnode_phase2.py`, `test_a2a_planner_tools.py`) still have `DummyCtx`/`_FakeCtx` classes with the old `artifacts` property name, which will be updated in Phase 002.
- No other production files reference `ctx.artifacts` or `self.artifacts` beyond what was listed in the plan. This was verified via grep after making changes.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- **Incomplete rename**: Until Phase 002 is applied, running the test suite will fail because several test fixture classes still use the old `artifacts` property name. This is expected and documented in the plan.
- **Property shadowing in ToolJobContext**: The `kv` property references `self._artifacts` which now resolves as a property call rather than a direct attribute access. This is functionally correct but subtly different -- if someone adds `__slots__` to `ToolJobContext` in the future, they would need to be aware that `_artifacts` is a property, not a slot.
- **MinimalCtx scope**: `MinimalCtx` is a local class inside a function in `playground.py`. Its `_artifacts` property has the leading underscore convention consistent with the protocol, but since it is a tiny inline class it does not implement the full `ToolContext` protocol -- it only provides what `read_resource` needs.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/context.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/planner_context.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/sessions/tool_jobs.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/tools/node.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_rich_output_nodes.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_task_tools.py`
