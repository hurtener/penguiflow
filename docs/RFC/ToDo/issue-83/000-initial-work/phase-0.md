# Phase 000: Fix `_discover_artifact_store()` to fall back to the state store

## Objective
When a custom state store is passed to `create_playground_app(state_store=my_store)`, the `_discover_artifact_store()` function currently only looks at the planner for an artifact store. If the planner is not discoverable (e.g., orchestrator-based agent) or the planner never received the custom store, the function returns `None` and all artifact endpoints return 501 "Artifact storage not enabled" -- even when the custom state store has a perfectly valid artifact store. This phase fixes `_discover_artifact_store()` to fall back to the outer `store` closure variable when planner-based discovery fails.

## Tasks
1. Rename the local variable `store` to `found` inside `_discover_artifact_store()` to avoid shadowing the outer closure variable `store` (the state store, defined at line 825).
2. Restructure the planner discovery path to continue instead of returning `None` early when the planner is not found.
3. Add a fallback block that checks `store.artifact_store` (the state store's artifact store) when the planner path yields nothing.

## Detailed Steps

### Step 1: Open `penguiflow/cli/playground.py` and locate `_discover_artifact_store()` (line 1020)
- The current function body spans lines 1020-1040.
- It starts with `def _discover_artifact_store() -> Any | None:`.

### Step 2: Replace the entire function body
- Keep the function signature and docstring unchanged.
- Keep the existing import `from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore` (line 1025).
- Restructure the planner discovery path so that when `planner is None`, execution falls through instead of returning `None`.
- Rename all uses of the local variable `store` (lines 1031-1040) to `found` to avoid shadowing the outer closure variable `store` which holds the state store (defined at line 825 of the same file: `store = state_store`).
- After the planner path, add a fallback block that checks `store` (the outer closure variable) for an `artifact_store` attribute.

### Step 3: Understand the `isinstance` checks
- `ArtifactStore` is a `@runtime_checkable` Protocol (defined at `penguiflow/artifacts.py:122`). Classes like `PlaygroundArtifactStore` and `InMemoryArtifactStore` do NOT inherit from `ArtifactStore` -- they satisfy the protocol structurally. The `isinstance(x, ArtifactStore)` check works at runtime because of the `@runtime_checkable` decorator.
- `NoOpArtifactStore` also structurally satisfies `ArtifactStore`, which is why the explicit `isinstance(x, NoOpArtifactStore)` exclusion check must come first (or be combined with `and not`).

## Required Code

```python
# Target file: penguiflow/cli/playground.py
# Replace lines 1020-1040 (the entire _discover_artifact_store function body)

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

### What changed vs. the original code

The original code (for reference):
```python
    def _discover_artifact_store() -> Any | None:
        from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore

        planner = _discover_planner()
        if planner is None:
            return None          # <-- PROBLEM: gives up here, never checks state store

        store = getattr(planner, "artifact_store", None)   # <-- shadows outer `store`
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

Changes:
1. `if planner is None: return None` replaced with `if planner is not None:` (conditional block instead of early return).
2. Local variable `store` renamed to `found` throughout (avoids shadowing the outer closure `store`).
3. The four separate `if/return` checks collapsed into a single compound condition: `if found is not None and isinstance(found, ArtifactStore) and not isinstance(found, NoOpArtifactStore)`.
4. New fallback block added after the planner path: checks `store.artifact_store` using the same compound condition.
5. Final `return None` at the end covers the case where neither path found a valid store.

## Exit Criteria (Success)
- [ ] `_discover_artifact_store()` in `penguiflow/cli/playground.py` no longer uses a local variable named `store` (all renamed to `found`).
- [ ] `_discover_artifact_store()` contains a fallback block that checks `store` (the outer closure variable) when the planner path yields nothing.
- [ ] The fallback block checks `getattr(store, "artifact_store", None)` and applies both `isinstance(found, ArtifactStore)` and `not isinstance(found, NoOpArtifactStore)` guards.
- [ ] The function still returns the planner's artifact store when available (existing behavior preserved).
- [ ] No import errors or syntax errors in the file.
- [ ] `uv run ruff check penguiflow/cli/playground.py` passes.
- [ ] `uv run mypy penguiflow/cli/playground.py` passes.
- [ ] `uv run pytest tests/ -k "playground"` passes (existing tests still green).

## Implementation Notes
- The outer closure variable `store` is defined at line 825 of `penguiflow/cli/playground.py`: `store = state_store`. It holds the state store passed to `create_playground_app()`, or an `InMemoryStateStore()` if none was provided.
- `InMemoryStateStore` (from `penguiflow.cli.playground_state`) has an `.artifact_store` property that returns a `PlaygroundArtifactStore` instance.
- `PlaygroundArtifactStore` structurally satisfies the `ArtifactStore` protocol and is NOT a subclass of `NoOpArtifactStore`, so it will correctly pass both `isinstance` checks.
- The `_discover_planner()` function (lines 1008-1018) is unchanged by this phase.
- All artifact endpoints (`GET /artifacts`, `GET /artifacts/{id}`, `GET /artifacts/{id}/meta`, `/resources/read`) call `_discover_artifact_store()` and will benefit from this fix.

## Verification Commands
```bash
# Lint check
uv run ruff check penguiflow/cli/playground.py

# Type check
uv run mypy penguiflow/cli/playground.py

# Run existing playground tests (should all pass)
uv run pytest tests/ -k "playground" -x -q

# Verify the local variable 'store' is no longer used inside _discover_artifact_store
# (only 'found' should appear as the local variable)
grep -n "store" penguiflow/cli/playground.py | grep -A2 -B2 "_discover_artifact_store"
```
