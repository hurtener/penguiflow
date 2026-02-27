# Phase 002: Complete `artifacts` to `_artifacts` Rename in Remaining Test Fixtures + Checkpoint

## Objective
Complete the atomic Enhancement 2 rename by updating the remaining test fixture context classes that implement the `ToolContext` protocol. Then run the checkpoint verification (pytest, ruff, mypy) to confirm nothing is broken. This phase MUST be applied immediately after Phase 001 -- the two phases together form the atomic rename.

## Tasks
1. Rename in `DummyCtx` in `tests/test_toolnode_phase1.py`
2. Rename in `DummyCtx` in `tests/test_toolnode_phase2.py`
3. Rename in `_FakeCtx` in `tests/a2a/test_a2a_planner_tools.py`
4. Run codebase-wide grep to verify completeness
5. Run checkpoint verification

## Detailed Steps

### Step 1: Rename in `DummyCtx` -- `tests/test_toolnode_phase1.py`
- Rename `self._artifacts` attribute (line 43) to `self._artifacts_store`:
  - Before: `self._artifacts = artifact_store or InMemoryArtifactStore()`
  - After: `self._artifacts_store = artifact_store or InMemoryArtifactStore()`
- Rename `artifacts` property (line 58) to `_artifacts`, returning `self._artifacts_store`:
  - Before: `def artifacts(self): return self._artifacts`
  - After: `def _artifacts(self): return self._artifacts_store`

### Step 2: Rename in `DummyCtx` -- `tests/test_toolnode_phase2.py`
- Rename `self._artifacts` attribute (line 44) to `self._artifacts_store`:
  - Before: `self._artifacts = artifact_store or InMemoryArtifactStore()`
  - After: `self._artifacts_store = artifact_store or InMemoryArtifactStore()`
- Rename `artifacts` property (line 59) to `_artifacts`, returning `self._artifacts_store`:
  - Before: `def artifacts(self): return self._artifacts`
  - After: `def _artifacts(self): return self._artifacts_store`

### Step 3: Rename in `_FakeCtx` -- `tests/a2a/test_a2a_planner_tools.py`
- Rename `artifacts` property (line 185) to `_artifacts`.
- Change the return value from `None` to `NoOpArtifactStore()` for protocol correctness (the `_artifacts` property must return `ArtifactStore`, not `None`).
- Add import: `from penguiflow.artifacts import NoOpArtifactStore` at the top of the file.

Before:
```python
    @property
    def artifacts(self) -> Any:  # pragma: no cover - not used
        return None
```

After:
```python
    @property
    def _artifacts(self) -> Any:  # pragma: no cover - not used
        return NoOpArtifactStore()
```

### Step 4: Verify completeness with codebase-wide grep
Run these grep commands to ensure no `ctx.artifacts` references remain in production or test code:
```bash
grep -rn 'ctx\.artifacts' penguiflow/ tests/
grep -rn '\.artifacts' penguiflow/planner/ penguiflow/sessions/ penguiflow/tools/ penguiflow/cli/ | grep -v '_artifacts' | grep -v '__pycache__'
```

**Expected result:** No hits from production code. The only acceptable remaining hit is in `penguiflow/planner/context.py` inside a docstring (which was removed/simplified in Phase 001). If any new call sites are found, migrate them before proceeding.

### Step 5: Run checkpoint verification
Run `uv run pytest tests/`, `uv run ruff check .`, and `uv run mypy`. All must pass with no new failures.

## Required Code

```python
# Target file: tests/test_toolnode_phase1.py
# In DummyCtx.__init__ (line 43):
#   self._artifacts = artifact_store or InMemoryArtifactStore()
# to:
#   self._artifacts_store = artifact_store or InMemoryArtifactStore()

# Replace the artifacts property (lines 57-59) with:
    @property
    def _artifacts(self):
        return self._artifacts_store
```

```python
# Target file: tests/test_toolnode_phase2.py
# In DummyCtx.__init__ (line 44):
#   self._artifacts = artifact_store or InMemoryArtifactStore()
# to:
#   self._artifacts_store = artifact_store or InMemoryArtifactStore()

# Replace the artifacts property (lines 58-60) with:
    @property
    def _artifacts(self):
        return self._artifacts_store
```

```python
# Target file: tests/a2a/test_a2a_planner_tools.py
# Add to imports at the top of the file:
from penguiflow.artifacts import NoOpArtifactStore

# Replace the artifacts property (lines 184-186) with:
    @property
    def _artifacts(self) -> Any:  # pragma: no cover - not used
        return NoOpArtifactStore()
```

## Exit Criteria (Success)
- [ ] `DummyCtx` in `tests/test_toolnode_phase1.py` uses `self._artifacts_store` and has `_artifacts` property
- [ ] `DummyCtx` in `tests/test_toolnode_phase2.py` uses `self._artifacts_store` and has `_artifacts` property
- [ ] `_FakeCtx` in `tests/a2a/test_a2a_planner_tools.py` has `_artifacts` property returning `NoOpArtifactStore()`
- [ ] Codebase-wide grep for `ctx.artifacts` returns no production/test code hits (only doc references if any)
- [ ] `uv run ruff check .` passes with zero errors
- [ ] `uv run mypy` passes with zero new errors
- [ ] `uv run pytest tests/` passes with no new failures (pre-existing 21 failures allowed)

## Implementation Notes
- This phase completes the atomic rename started in Phase 001. Together, Phases 001 + 002 form the Enhancement 2 checkpoint.
- The `NoOpArtifactStore` import in `test_a2a_planner_tools.py` is needed because `_FakeCtx._artifacts` must return an `ArtifactStore`, not `None`.
- After this checkpoint, `ctx.artifacts` no longer exists anywhere in production/test code. The property name `artifacts` is now free to be used for the `ScopedArtifacts` facade in Phases 003-005.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow

# Grep to verify completeness (should return no hits in code, only docs/comments)
grep -rn 'ctx\.artifacts' penguiflow/ tests/ --include='*.py' | grep -v '_artifacts' | grep -v '__pycache__'

# Full checkpoint
uv run ruff check .
uv run mypy
uv run pytest tests/ -x -q
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-26

### Summary of Changes
- **`tests/test_toolnode_phase1.py`**: Renamed `self._artifacts` to `self._artifacts_store` in `DummyCtx.__init__`, renamed `artifacts` property to `_artifacts` returning `self._artifacts_store`.
- **`tests/test_toolnode_phase2.py`**: Identical rename as phase1 -- `self._artifacts` to `self._artifacts_store` in `DummyCtx.__init__`, renamed `artifacts` property to `_artifacts` returning `self._artifacts_store`.
- **`tests/a2a/test_a2a_planner_tools.py`**: Added `from penguiflow.artifacts import NoOpArtifactStore` import, renamed `artifacts` property to `_artifacts` in `_FakeCtx`, changed return from `None` to `NoOpArtifactStore()`. Import ordering was auto-fixed by `ruff --fix` (isort splits `from penguiflow import ...` lines alphabetically).
- **`penguiflow/rich_output/nodes.py`** (additional fix): Changed `getattr(ctx, "artifacts", None)` to `getattr(ctx, "_artifacts", None)` on line 106. This was a call site missed in Phase 001 that used `getattr` with a string literal (not `ctx.artifacts` dot access), so it was not caught by the Phase 001 grep patterns.

### Key Considerations
- The three test file changes were straightforward mechanical renames exactly as specified in the plan.
- The import ordering in `test_a2a_planner_tools.py` required `ruff --fix` to auto-sort, since `penguiflow` first-party imports must be grouped and sorted alphabetically per the project's isort configuration. The initial manual placement triggered an `I001` lint error.
- The `getattr(ctx, "artifacts", None)` in `rich_output/nodes.py` was discovered during checkpoint verification when `test_list_artifacts_ingests_background_results_for_artifact_refs` failed. This call site uses a string literal for attribute lookup rather than direct dot access, which is why it was missed by the `ctx\.artifacts` grep pattern used in Phase 001.

### Assumptions
- The `getattr(ctx, "artifacts", None)` fix in `rich_output/nodes.py` is a legitimate completion of the Phase 001 rename that was missed during that phase. Since Phase 002 explicitly instructs to "migrate any new call sites found before proceeding" (Step 4), this fix falls within scope.
- The 21 pre-existing test failures (all in `test_databricks_provider.py`, `test_llm_provider_databricks.py`, `test_google_provider_streaming.py`, and `test_llm_provider_google.py`) are caused by missing optional dependencies (`openai`, `google` provider packages) and are unrelated to this work.

### Deviations from Plan
- **Additional fix in `penguiflow/rich_output/nodes.py`**: The plan did not mention this file, but the Phase 002 grep verification step (Step 4) explicitly instructs to fix any remaining call sites found. The `getattr(ctx, "artifacts", None)` pattern was a call site that used string-based attribute lookup and was missed in Phase 001. Without this fix, `test_list_artifacts_ingests_background_results_for_artifact_refs` would fail (22 failures instead of the expected 21).

### Potential Risks & Reviewer Attention Points
- The `_artifacts` property name uses a single leading underscore, which is a Python convention for "internal" but does not trigger name mangling. External code (like tests) can still access `ctx._artifacts` without issue. However, this means the property is accessible from outside the class, which is intentional per the protocol design.
- The `getattr(ctx, "_artifacts", None)` pattern in `rich_output/nodes.py` still returns `None` for contexts that do not implement the `_artifacts` property. This is the same graceful fallback behavior as before the rename.
- Reviewers should verify that no other `getattr` patterns with `"artifacts"` string literals exist elsewhere in the codebase. A grep for `getattr\([^,]+, ['\"]artifacts['\"]` confirmed no remaining instances.

### Files Modified
- `tests/test_toolnode_phase1.py` -- Renamed attribute and property in `DummyCtx`
- `tests/test_toolnode_phase2.py` -- Renamed attribute and property in `DummyCtx`
- `tests/a2a/test_a2a_planner_tools.py` -- Added import, renamed property in `_FakeCtx`, changed return value
- `penguiflow/rich_output/nodes.py` -- Fixed missed `getattr(ctx, "artifacts", None)` to use `"_artifacts"`
