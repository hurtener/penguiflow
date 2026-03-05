# Phase 001: Add missing `list` method to `_ScopedArtifactStore` and `_DisabledArtifactStore`

## Objective
Both `_ScopedArtifactStore` and `_DisabledArtifactStore` are duck-type implementations of the `ArtifactStore` protocol (`penguiflow/artifacts.py:122`). The protocol requires 7 methods: `put_bytes`, `put_text`, `get`, `get_ref`, `delete`, `exists`, and `list`. Both classes implement the first 6 but are missing `list`. This means any code calling `.list()` on these stores raises `AttributeError`, and because `ArtifactStore` is `@runtime_checkable`, these classes also fail `isinstance(x, ArtifactStore)` checks. This phase adds the missing `list` method to both classes.

## Tasks
1. Add `list` method to `_ScopedArtifactStore` that delegates to the inner store with default scope injection.
2. Add `list` method to `_DisabledArtifactStore` that returns an empty list.

## Detailed Steps

### Step 1: Add `list` method to `_ScopedArtifactStore` (after `exists` at line 1113)
- The class is defined at line 1058 inside the `create_playground_app` closure.
- Add the `list` method after the `exists` method (line 1112-1113).
- The method must match the `ArtifactStore` protocol signature: `async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]`.
- Use a local import for `ArtifactRef` and `ArtifactScope` since these classes are inside a closure: `from penguiflow.artifacts import ArtifactRef, ArtifactScope`.
- Delegate to `self._store.list(scope=scope or self._scope)` -- inject the default scope when none is provided, matching the pattern used by `put_bytes` and `put_text`.

### Step 2: Add `list` method to `_DisabledArtifactStore` (after `exists` at line 1134)
- The class is defined at line 1115 inside the `create_playground_app` closure.
- Add the `list` method after the `exists` method (line 1133-1134).
- The method must match the protocol signature: `async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]`.
- Use the same local import: `from penguiflow.artifacts import ArtifactRef, ArtifactScope`.
- Return an empty list `[]` -- consistent with the disabled/no-op behavior of all other methods in this class.

## Required Code

```python
# Target file: penguiflow/cli/playground.py
# Add after the `exists` method in _ScopedArtifactStore (after line 1113)

        async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]":
            from penguiflow.artifacts import ArtifactRef, ArtifactScope

            return await self._store.list(scope=scope or self._scope)
```

```python
# Target file: penguiflow/cli/playground.py
# Add after the `exists` method in _DisabledArtifactStore (after line 1134)

        async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]":
            from penguiflow.artifacts import ArtifactRef, ArtifactScope

            return []
```

### Context: where these classes are used

Both classes are instantiated in endpoint handlers inside `create_playground_app`:
- The `/resources/read` endpoint (around line 2141-2155) creates `_ScopedArtifactStore(artifact_store, scope)` or `_DisabledArtifactStore()` and passes them as the artifact store into `MinimalCtx` / `MinimalToolCtx`.
- The `/apps/{namespace}/call-tool` endpoint (around line 2231-2245) does the same.
- Any tool or resource handler that calls `.list()` on these context objects will now work correctly instead of raising `AttributeError`.

### Context: the `ArtifactStore` protocol signature for `list`

From `penguiflow/artifacts.py:224-230`:
```python
    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts matching the given scope filter.

        None fields in scope = don't filter on that dimension.
        If scope is None, returns all artifacts.
        """
        ...
```

## Exit Criteria (Success)
- [ ] `_ScopedArtifactStore` in `penguiflow/cli/playground.py` has a `list` method that delegates to `self._store.list(scope=scope or self._scope)`.
- [ ] `_DisabledArtifactStore` in `penguiflow/cli/playground.py` has a `list` method that returns `[]`.
- [ ] Both `list` methods have the correct signature: `async def list(self, *, scope: ... = None) -> list[ArtifactRef]`.
- [ ] Both methods include the local import `from penguiflow.artifacts import ArtifactRef, ArtifactScope`.
- [ ] No import errors or syntax errors in the file.
- [ ] `uv run ruff check penguiflow/cli/playground.py` passes.
- [ ] `uv run mypy penguiflow/cli/playground.py` passes.
- [ ] `uv run pytest tests/ -k "playground"` passes (existing tests still green).

## Implementation Notes
- These classes are defined inside the `create_playground_app` closure, so top-level imports are not available. Use local imports inside each method body.
- The `ArtifactScope` and `ArtifactRef` types are already imported locally in nearby code (e.g., the `/artifacts` endpoint at line 1935 does `from penguiflow.artifacts import ArtifactScope`), so the pattern is consistent with the codebase.
- The type annotations use string literals (`"ArtifactScope | None"`, `"list[ArtifactRef]"`) because the types come from a local import. Alternatively, if the existing methods in the class do not use string annotations, match the existing style (the existing methods use `Any` for these type positions). Check the existing code and match whichever style is used -- the key requirement is that the code runs without `NameError`.
- This phase depends on Phase 0 having been completed (both phases modify the same file, and the line numbers shift after Phase 0's changes).

## Verification Commands
```bash
# Lint check
uv run ruff check penguiflow/cli/playground.py

# Type check
uv run mypy penguiflow/cli/playground.py

# Run existing playground tests (should all pass)
uv run pytest tests/ -k "playground" -x -q

# Verify both classes now have a 'list' method
grep -n "async def list" penguiflow/cli/playground.py
```
