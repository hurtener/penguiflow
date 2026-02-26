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
