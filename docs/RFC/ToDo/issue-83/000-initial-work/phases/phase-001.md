# Phase 001: Fix `_discover_artifact_store()` to fall back to the state store

## Objective
The `_discover_artifact_store()` function inside `create_playground_app` only looks at the planner for an artifact store. When the planner is not discoverable (e.g., orchestrator-based agents) or the planner has a `NoOpArtifactStore`, the function returns `None` -- even when the custom state store passed to `create_playground_app(state_store=...)` has a perfectly valid artifact store. This phase adds a fallback that checks the outer `store` closure variable (the state store) when planner-based discovery fails.

## Tasks
1. Rename the local variable `store` in `_discover_artifact_store()` to `found` to avoid shadowing the outer `store` closure variable.
2. Restructure the function to try the planner path first, then fall back to `store.artifact_store`.
3. Keep the existing `NoOpArtifactStore` and `isinstance(x, ArtifactStore)` guard checks for both paths.

## Detailed Steps

### Step 1: Understand the current code
- File: `penguiflow/cli/playground.py`, function `_discover_artifact_store()` at lines 1020-1040.
- The outer closure variable `store` is defined at line 825 of the same function (`create_playground_app`): `store = state_store`.
- The current code uses `store` as a local variable (line 1031) for the planner's artifact store. This shadows the outer `store`.

Current code (lines 1020-1040):
```python
def _discover_artifact_store() -> Any | None:
    """Discover the artifact store from the running agent (no injection).

    Returns None if the agent has no artifact store configured or is using NoOp.
    """
    from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

    planner = _discover_planner()
    if planner is None:
        return None

    store = getattr(planner, "artifact_store", None)
    if store is None:
        store = getattr(planner, "_artifact_store", None)
    if store is None:
        return None
    if isinstance(store, NoOpArtifactStore):
        return None
    if not isinstance(store, ArtifactStore):
        return None
    return store
```

### Step 2: Replace the entire function body
- Replace the function with the new version below.
- Key changes:
  1. Rename local `store` to `found` everywhere inside the function.
  2. When `planner is None`, do NOT return immediately -- fall through to the state store fallback.
  3. When the planner path finds a valid artifact store (`found` is not None, is `ArtifactStore`, is not `NoOpArtifactStore`), return it (preserving existing behavior: planner takes priority).
  4. After the planner path, add a fallback block that checks `store` (the outer closure variable = the custom state store). Use `getattr(store, "artifact_store", None)` to get the artifact store from the state store, then apply the same `isinstance` guards.
  5. If neither path finds a valid store, return `None`.

### Step 3: Verify the `isinstance` check logic
- `ArtifactStore` is a `@runtime_checkable` Protocol (see `penguiflow/artifacts.py:122`).
- `NoOpArtifactStore` also structurally satisfies `ArtifactStore` (it has all 7 methods). So the explicit `isinstance(found, NoOpArtifactStore)` exclusion MUST come before the `isinstance(found, ArtifactStore)` inclusion check.
- In the new code, both checks are combined in a single `if` condition using `and not isinstance(found, NoOpArtifactStore)`.

## Required Code

```python
# Target file: penguiflow/cli/playground.py
# REPLACE the entire _discover_artifact_store function (lines 1020-1040) with:

    def _discover_artifact_store() -> Any | None:
        """Discover the artifact store from the running agent (no injection).

        Returns None if the agent has no artifact store configured or is using NoOp.
        """
        from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

        planner = _discover_planner()
        if planner is not None:
            found = getattr(planner, "artifact_store", None)
            if found is None:
                found = getattr(planner, "_artifact_store", None)
            if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore):
                return found

        # Fallback: check the playground state store directly
        if store is not None:
            found = getattr(store, "artifact_store", None)
            if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore):
                return found

        return None
```

The old code to replace is exactly:
```python
    def _discover_artifact_store() -> Any | None:
        """Discover the artifact store from the running agent (no injection).

        Returns None if the agent has no artifact store configured or is using NoOp.
        """
        from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

        planner = _discover_planner()
        if planner is None:
            return None

        store = getattr(planner, "artifact_store", None)
        if store is None:
            store = getattr(planner, "_artifact_store", None)
        if store is None:
            return None
        if isinstance(store, NoOpArtifactStore):
            return None
        if not isinstance(store, ArtifactStore):
            return None
        return store
```

## Exit Criteria (Success)
- [ ] `_discover_artifact_store()` no longer uses `store` as a local variable name (uses `found` instead)
- [ ] When `_discover_planner()` returns `None`, the function does NOT immediately return `None` -- it falls through to the state store fallback
- [ ] The state store fallback checks `store` (the outer closure variable) for an `.artifact_store` attribute
- [ ] The `NoOpArtifactStore` exclusion and `ArtifactStore` inclusion checks are applied to both the planner path and the state store fallback path
- [ ] When both the planner and the state store have valid artifact stores, the planner's store is returned (planner takes priority)
- [ ] `uv run ruff check penguiflow/cli/playground.py` passes
- [ ] `uv run mypy penguiflow/cli/playground.py` passes (or only pre-existing errors)
- [ ] `uv run pytest tests/ -k "playground"` passes (no regressions in existing tests)

## Implementation Notes
- The outer `store` variable is the state store passed to `create_playground_app(state_store=...)`. It is defined at line 825: `store = state_store`. If no state store was provided by the caller, `store` is set to `InMemoryStateStore()` at line 836 or 839. So `store` should never be `None` at runtime, but the `if store is not None` guard is defensive.
- `InMemoryStateStore` (from `penguiflow/state/in_memory.py`) has an `artifact_store` property that returns a `PlaygroundArtifactStore` instance. `PlaygroundArtifactStore` is NOT a subclass of `ArtifactStore` -- it satisfies the protocol structurally. `isinstance(x, ArtifactStore)` will return `True` because `ArtifactStore` is `@runtime_checkable`.
- This change is backwards compatible. All existing tests where the planner has a valid artifact store will continue to work because the planner path is tried first.
- The only behavioral change is: when the planner path fails (planner not found, or planner has NoOp/no artifact store), the function now checks the state store before returning `None`.
- **Depends on Phase 000**: Phase 000 adds the `list` method to `_ScopedArtifactStore` and `_DisabledArtifactStore`. While this phase modifies a different function (`_discover_artifact_store`), doing Phase 000 first avoids any merge conflicts in the same file.

## Verification Commands
```bash
# Lint
uv run ruff check penguiflow/cli/playground.py

# Type check
uv run mypy penguiflow/cli/playground.py

# Run existing playground tests (must all pass -- no regressions)
uv run pytest tests/ -k "playground" -x -q

# Verify the local variable 'store' is no longer used inside _discover_artifact_store
# (This is a rough check; the word 'store' will appear in the outer closure reference and comments, but NOT as a local assignment target)
uv run python -c "
import inspect, re
from penguiflow.cli import playground
# The source of the whole module is too large, but we can check the function exists
# and the closure variable 'store' is accessible (not shadowed)
print('Module loaded successfully')
"
```

---

## Implementation Notes (Post-Implementation)

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes

- **`penguiflow/cli/playground.py`** -- Replaced the `_discover_artifact_store()` function body (lines 1020-1040). Renamed local variable `store` to `found` to avoid shadowing the outer closure variable. Restructured the function to try the planner path first, then fall back to `store.artifact_store` (the outer state store). Both paths apply the same `isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore)` guard checks.

- **`tests/cli/test_playground_artifact_discovery.py`** (NEW) -- Added 4 test cases exercising the fallback behavior:
  1. `test_discover_artifact_store_falls_back_to_state_store` -- Verifies artifacts stored in the state store are returned when the planner is not discoverable.
  2. `test_discover_artifact_store_prefers_planner` -- Verifies the planner's artifact store takes priority over the state store.
  3. `test_discover_artifact_store_skips_noop` -- Verifies that a `NoOpArtifactStore` on the state store is correctly filtered out, resulting in 501.
  4. `test_discover_artifact_store_list_endpoint_uses_fallback` -- Verifies the `/artifacts` list endpoint returns artifacts from the state store fallback.

- **`tests/cli/test_playground_endpoints.py`** -- Updated 3 existing tests that expected 501 responses when no planner artifact store was found. With the state store fallback, the `InMemoryStateStore`'s `PlaygroundArtifactStore` is now discovered, so the endpoint returns 404 (artifact not found) instead of 501 (storage not enabled):
  - `test_get_artifact_no_store_returns_501` renamed to `test_get_artifact_no_planner_store_returns_404`, now asserts 404.
  - `test_get_artifact_meta_no_store_returns_501` renamed to `test_get_artifact_meta_no_planner_store_returns_404`, now asserts 404.
  - `test_discover_returns_none_for_noop_store` renamed to `test_discover_skips_noop_planner_store_falls_back_to_state_store`, now asserts 404 (since the planner's NoOp is skipped but the state store's valid artifact store is found via fallback).

- **`tests/test_playground_phase3.py`** -- Updated `test_get_artifact_not_enabled` (renamed to `test_get_artifact_not_found_with_state_store_fallback`). Same behavioral change: passes an `InMemoryStateStore` as state_store, so the fallback finds its artifact store, resulting in 404 instead of 501.

### Key Considerations

1. **Existing test expectations changed by design.** The entire point of this phase is to add a fallback path, which means tests that previously expected 501 ("artifact storage not enabled") now correctly get 404 ("artifact not found") because the state store's artifact store is found. This is the intended behavioral change: the artifact system is now "enabled" (via state store fallback) even when the planner has no artifact store.

2. **MagicMock attribute auto-creation.** All tests that need `_discover_planner()` to return `None` explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None` to prevent MagicMock from auto-creating attributes that would satisfy the `@runtime_checkable` Protocol checks. This follows the existing pattern documented at `tests/test_playground_phase3.py:646-649`.

3. **`PlaygroundArtifactStore` passes `isinstance(x, ArtifactStore)`.** The `ArtifactStore` Protocol is `@runtime_checkable`, and `PlaygroundArtifactStore` structurally satisfies it (all 7 methods), so `isinstance` checks succeed without inheritance.

### Assumptions

- The phase file states the test file should be `tests/cli/test_playground_artifact_discovery.py`. This follows the convention of other playground tests in `tests/cli/`.
- The `InMemoryStateStore()` always creates a valid `PlaygroundArtifactStore` via its `_artifact_store` field, so `store.artifact_store` always returns a non-None, non-NoOp artifact store. This means the 501 "not enabled" response is now only reachable when both the planner path and the state store path fail (e.g., the state store has a `NoOpArtifactStore` or no `artifact_store` attribute at all).
- The phase file did not explicitly mention updating existing tests, but the behavioral change necessarily invalidates tests that asserted 501 when a valid state store with artifacts was provided. Updating these tests is required to avoid regressions.

### Deviations from Plan

- **Updated 3 existing tests in `tests/cli/test_playground_endpoints.py` and 1 in `tests/test_playground_phase3.py`.** The phase file did not mention updating existing tests, but the behavioral change (state store fallback) means these tests' 501 expectations are no longer correct. The tests were updated to assert 404 (artifact not found via the state store's artifact store) with updated docstrings explaining the new behavior. This is a necessary consequence of the code change, not a discretionary deviation.

### Potential Risks & Reviewer Attention Points

1. **501 is now harder to trigger.** Since `InMemoryStateStore` is always created as a default (lines 836/839), the artifact system is effectively always enabled in the playground. The 501 "Artifact storage not enabled" response is now only reachable when the state store itself has a `NoOpArtifactStore` or lacks an `artifact_store` attribute entirely. This is intentional per the plan but worth noting.

2. **The `test_discover_skips_noop_planner_store_falls_back_to_state_store` test.** This test's expected behavior changed subtly: the planner's `NoOpArtifactStore` is correctly skipped, but the `MockAgentWrapper`'s own `InMemoryStateStore` (assigned at line 31 of the mock) provides a valid artifact store via the fallback. The test now asserts 404 instead of 501, which is correct but may surprise a reviewer who expects the NoOp test to still yield 501.

3. **No test for the truly-no-artifact-store case.** To get a 501 response now, one would need a state store that has no `artifact_store` attribute or has a `NoOpArtifactStore`. The new test `test_discover_artifact_store_skips_noop` covers this case (using a `MagicMock` state store with `NoOpArtifactStore`).

### Files Modified

- `penguiflow/cli/playground.py` -- Modified `_discover_artifact_store()` function
- `tests/cli/test_playground_artifact_discovery.py` -- **New file** with 4 test cases
- `tests/cli/test_playground_endpoints.py` -- Updated 3 existing test expectations (501 to 404)
- `tests/test_playground_phase3.py` -- Updated 1 existing test expectation (501 to 404)
