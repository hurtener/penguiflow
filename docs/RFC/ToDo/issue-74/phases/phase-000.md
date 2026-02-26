# Phase 000: Add `list` Method to ArtifactStore Protocol and All Implementations

## Objective
Add a `list(scope)` method to the `ArtifactStore` protocol and implement it across all store implementations: `NoOpArtifactStore`, `InMemoryArtifactStore`, `PlaygroundArtifactStore`, and `_EventEmittingArtifactStoreProxy`. Also add the `_scope_matches` module-level helper function used by filtering implementations. This is Enhancement 1 from the plan.

## Tasks
1. Add `list` method signature to the `ArtifactStore` protocol in `penguiflow/artifacts.py`
2. Add `_scope_matches` helper function in `penguiflow/artifacts.py`
3. Implement `list` on `NoOpArtifactStore` and `InMemoryArtifactStore` in `penguiflow/artifacts.py`
4. Implement `list` on `PlaygroundArtifactStore` in `penguiflow/state/in_memory.py`
5. Implement `list` delegation on `_EventEmittingArtifactStoreProxy` in `penguiflow/planner/artifact_handling.py`

## Detailed Steps

### Step 1: Add `list` to ArtifactStore protocol — `penguiflow/artifacts.py`
- Locate the `ArtifactStore` protocol class (currently ends at line ~218, after the `exists()` method).
- Add the `list` method inside the class body, after `exists()`:

```python
    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts matching the given scope filter.
        None fields in scope = don't filter on that dimension.
        If scope is None, returns all artifacts.
        """
        ...
```

### Step 2: Add `_scope_matches` helper — `penguiflow/artifacts.py`
- Add a module-level helper function near `_generate_artifact_id` (around line 257), outside any class, in the Implementations section.
- This function is reusable by `InMemoryArtifactStore` and `PlaygroundArtifactStore`.

### Step 3: Implement `list` on `NoOpArtifactStore` — `penguiflow/artifacts.py`
- Add `async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:` to `NoOpArtifactStore`.
- It should always return `[]`.

### Step 4: Implement `list` on `InMemoryArtifactStore` — `penguiflow/artifacts.py`
- Add `list` method to `InMemoryArtifactStore`.
- Call `self._expire_old_artifacts()` first.
- If `scope` is `None`, return all refs: `[stored.ref for stored in self._artifacts.values()]`.
- If `scope` is provided, filter using `_scope_matches`: `[stored.ref for stored in self._artifacts.values() if _scope_matches(stored.ref.scope, scope)]`.

### Step 5: Implement `list` on `PlaygroundArtifactStore` — `penguiflow/state/in_memory.py`
- Add `list` method to `PlaygroundArtifactStore` (after the `exists` method, before `clear_session`).
- Import `_scope_matches` is NOT needed here -- delegate to the inner `InMemoryArtifactStore.list()`.
- Lock strategy: snapshot the stores dict under the lock, then iterate and call `list()` on each store outside the lock.
- If `scope` is not `None` and `scope.session_id` is set, query only that session's inner store.
- Otherwise iterate all session stores and aggregate results.

### Step 6: Implement `list` delegation on `_EventEmittingArtifactStoreProxy` — `penguiflow/planner/artifact_handling.py`
- Add `list` method after the existing `exists` method (line ~147).
- Simple delegation: `return await self._store.list(scope=scope)`.
- No event emission needed for reads.

## Required Code

```python
# Target file: penguiflow/artifacts.py
# Add inside ArtifactStore protocol class, after exists() method (after line 218):

    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts matching the given scope filter.
        None fields in scope = don't filter on that dimension.
        If scope is None, returns all artifacts.
        """
        ...
```

```python
# Target file: penguiflow/artifacts.py
# Add as module-level function near _generate_artifact_id (around line 268, after it):

def _scope_matches(artifact_scope: ArtifactScope | None, filter_scope: ArtifactScope) -> bool:
    """Check if an artifact's scope matches a filter scope.

    Matching rules:
    - If filter_scope has a non-None field, the artifact must have the same value
      in that field to match.
    - If filter_scope has a None field, any value (including None) matches.
    - If the artifact has no scope at all (artifact_scope is None), it is treated
      as having all-None fields -- so it matches any filter field that is None,
      but FAILS any filter field that is non-None.
    """
    for field in ("tenant_id", "user_id", "session_id", "trace_id"):
        filter_val = getattr(filter_scope, field)
        if filter_val is None:
            continue  # None filter = wildcard, matches anything
        artifact_val = getattr(artifact_scope, field) if artifact_scope is not None else None
        if artifact_val != filter_val:
            return False
    return True
```

```python
# Target file: penguiflow/artifacts.py
# Add to NoOpArtifactStore class, after the exists() method (after line 382):

    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """No-op list always returns empty."""
        return []
```

```python
# Target file: penguiflow/artifacts.py
# Add to InMemoryArtifactStore class, after the exists() method (after line 526):

    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts matching the given scope filter."""
        await self._expire_old_artifacts()
        if scope is None:
            return [stored.ref for stored in self._artifacts.values()]
        return [stored.ref for stored in self._artifacts.values() if _scope_matches(stored.ref.scope, scope)]
```

```python
# Target file: penguiflow/state/in_memory.py
# Add to PlaygroundArtifactStore class, after exists() method (after line 181), before clear_session():

    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts, optionally filtered by scope."""
        if scope is not None and scope.session_id is not None:
            async with self._lock:
                store = self._stores.get(scope.session_id)
            if store is None:
                return []
            return await store.list(scope=scope)
        # No session filter -- snapshot all stores, iterate outside lock
        async with self._lock:
            stores = list(self._stores.values())
        results: list[ArtifactRef] = []
        for store in stores:
            results.extend(await store.list(scope=scope))
        return results
```

```python
# Target file: penguiflow/planner/artifact_handling.py
# Add to _EventEmittingArtifactStoreProxy class, after exists() method (after line 147):

    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """Delegate list to underlying store (no event for reads)."""
        return await self._store.list(scope=scope)
```

## Exit Criteria (Success)
- [ ] `ArtifactStore` protocol class contains `async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]`
- [ ] Module-level `_scope_matches(artifact_scope, filter_scope)` function exists in `penguiflow/artifacts.py`
- [ ] `NoOpArtifactStore.list()` returns `[]` always
- [ ] `InMemoryArtifactStore.list()` filters by scope using `_scope_matches` and expires old artifacts first
- [ ] `PlaygroundArtifactStore.list()` delegates to inner stores with correct lock strategy
- [ ] `_EventEmittingArtifactStoreProxy.list()` delegates to `self._store.list(scope=scope)`
- [ ] `uv run ruff check .` passes with zero errors
- [ ] `uv run mypy` passes with zero new errors
- [ ] `uv run pytest tests/test_artifacts.py` passes (existing tests still pass)

## Implementation Notes
- The `_scope_matches` function and the `_check_scope` method (added in Phase 003) have intentionally different semantics for `None` artifact fields. Do NOT confuse them.
- `PlaygroundArtifactStore.list()` does NOT need to import `_scope_matches` -- it delegates to the inner `InMemoryArtifactStore.list()` which handles filtering.
- The `ArtifactRef` import is already present in `penguiflow/state/in_memory.py`.
- The `_EventEmittingArtifactStoreProxy` import of `ArtifactRef` and `ArtifactScope` is already present in `penguiflow/planner/artifact_handling.py`.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv run ruff check penguiflow/artifacts.py penguiflow/state/in_memory.py penguiflow/planner/artifact_handling.py
uv run mypy
uv run pytest tests/test_artifacts.py -x -q
```
