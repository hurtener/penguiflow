# Phase 000: Add missing `list` method to `_ScopedArtifactStore` and `_DisabledArtifactStore`

## Objective
Both `_ScopedArtifactStore` and `_DisabledArtifactStore` (inner classes inside `create_playground_app` in `penguiflow/cli/playground.py`) implement 6 of the 7 methods required by the `ArtifactStore` protocol (`penguiflow/artifacts.py:122`). They are missing the `list` method. Without it, any call to `.list()` raises `AttributeError`, and `isinstance(x, ArtifactStore)` fails because the `@runtime_checkable` protocol check requires all methods to be present.

## Tasks
1. Add a `list` method to `_ScopedArtifactStore` that delegates to the inner store with scope defaulting.
2. Add a `list` method to `_DisabledArtifactStore` that returns an empty list.

## Detailed Steps

### Step 1: Add `list` to `_ScopedArtifactStore` (line ~1113 in `penguiflow/cli/playground.py`)
- Open `penguiflow/cli/playground.py`.
- Locate the `_ScopedArtifactStore` class (currently at line 1058).
- After the `exists` method (currently ending at line 1113), add a new `list` method.
- The method must use a local import for `ArtifactRef` and `ArtifactScope` (since this class is inside the `create_playground_app` closure, local imports are the convention used elsewhere in the file).
- The method delegates to `self._store.list(scope=scope or self._scope)` -- i.e., if no scope is provided, it falls back to the default scope injected at construction time.

### Step 2: Add `list` to `_DisabledArtifactStore` (line ~1134 in `penguiflow/cli/playground.py`)
- Locate the `_DisabledArtifactStore` class (currently at line 1115).
- After the `exists` method (currently ending at line 1134), add a new `list` method.
- Since this store is a disabled shim, the method returns an empty list `[]`.
- Use a local import for `ArtifactRef` and `ArtifactScope` to match the protocol signature exactly.

## Required Code

```python
# Target file: penguiflow/cli/playground.py
# Insert AFTER the `exists` method of `_ScopedArtifactStore` (after line 1113):

        async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]":
            from penguiflow.artifacts import ArtifactRef, ArtifactScope

            return await self._store.list(scope=scope or self._scope)
```

```python
# Target file: penguiflow/cli/playground.py
# Insert AFTER the `exists` method of `_DisabledArtifactStore` (after line 1134):

        async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]":
            from penguiflow.artifacts import ArtifactRef, ArtifactScope  # noqa: F811

            return []
```

After applying both changes, the two classes should look like this (showing only the tail end of each):

**`_ScopedArtifactStore`** (end of class):
```python
        async def exists(self, artifact_id: str):
            return await self._store.exists(artifact_id)

        async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]":
            from penguiflow.artifacts import ArtifactRef, ArtifactScope

            return await self._store.list(scope=scope or self._scope)
```

**`_DisabledArtifactStore`** (end of class):
```python
        async def exists(self, _artifact_id: str):
            return False

        async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]":
            from penguiflow.artifacts import ArtifactRef, ArtifactScope  # noqa: F811

            return []
```

## Exit Criteria (Success)
- [ ] `_ScopedArtifactStore` has a `list` method with signature `async def list(self, *, scope: "ArtifactScope | None" = None) -> "list[ArtifactRef]"`
- [ ] `_ScopedArtifactStore.list` delegates to `self._store.list(scope=scope or self._scope)`
- [ ] `_DisabledArtifactStore` has a `list` method with the same signature
- [ ] `_DisabledArtifactStore.list` returns `[]`
- [ ] Both methods use local imports for `ArtifactRef` and `ArtifactScope`
- [ ] `uv run ruff check penguiflow/cli/playground.py` passes with no errors
- [ ] `uv run mypy penguiflow/cli/playground.py` passes (or only shows pre-existing errors unrelated to this change)
- [ ] `uv run pytest tests/ -k "playground"` passes (no regressions)

## Implementation Notes
- These classes are defined **inside** the `create_playground_app` function (they are closure-scoped). This is why local imports are used rather than top-level imports.
- The `ArtifactStore` protocol is `@runtime_checkable` (see `penguiflow/artifacts.py:122`). After adding `list`, both classes will pass `isinstance(x, ArtifactStore)` checks. Before this fix, they did NOT pass that check because the protocol requires all 7 methods.
- The `ArtifactScope` and `ArtifactRef` types are defined in `penguiflow/artifacts.py`. They are Pydantic models.
- The `# noqa: F811` on the `_DisabledArtifactStore` import suppresses the "redefinition of unused" warning from ruff, since the `ArtifactScope` import is used only in the type annotation. If ruff does not flag it, the noqa comment can be omitted.
- No other files need to be modified in this phase.

## Verification Commands
```bash
# Lint check
uv run ruff check penguiflow/cli/playground.py

# Type check
uv run mypy penguiflow/cli/playground.py

# Run existing playground tests to ensure no regressions
uv run pytest tests/ -k "playground" -x -q

# Quick structural check: both classes now have 7 methods matching the protocol
uv run python -c "
from penguiflow.artifacts import ArtifactStore
import inspect
protocol_methods = [m for m in dir(ArtifactStore) if not m.startswith('_')]
print('ArtifactStore protocol methods:', sorted(protocol_methods))
assert 'list' in protocol_methods, 'list must be in protocol'
print('OK: protocol requires list method')
"
```

---

## Implementation Notes (Post-Implementation)

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes
- Added `list` method to `_ScopedArtifactStore` (line 1115 in `penguiflow/cli/playground.py`) that delegates to `self._store.list(scope=scope or self._scope)`.
- Added `list` method to `_DisabledArtifactStore` (line 1139 in `penguiflow/cli/playground.py`) that returns an empty list `[]`.

### Key Considerations
- The plan specified using quoted string annotations (`"ArtifactScope | None"`, `"list[ArtifactRef]"`) with local imports for `ArtifactRef` and `ArtifactScope` inside each method body. However, this approach triggers multiple ruff errors in the project's configuration:
  - `UP037`: ruff's pyupgrade rule requires removing quotes from type annotations (since `from __future__ import annotations` is active at the top of the file, all annotations are already deferred).
  - `F821`: Once quotes are removed by UP037, the bare names `ArtifactScope` and `ArtifactRef` are undefined at the annotation scope (they are only imported inside the method body, not at class/function scope).
  - `F401`: The local imports are flagged as unused because they are not referenced in the method body (only in annotations, which are strings under `__future__` annotations).
- To resolve this, I followed the **existing convention** used by all other methods in both classes: use `Any` for type annotations on the `scope` parameter and return type. This is consistent with `put_bytes`, `put_text`, `get`, `get_ref`, `delete`, and `exists` in both classes, which all use `Any` rather than the specific protocol types.
- The local imports for `ArtifactRef` and `ArtifactScope` were removed since they are not needed at runtime (the `_ScopedArtifactStore.list` method simply delegates to `self._store.list(...)`, and `_DisabledArtifactStore.list` returns a plain `[]`).

### Assumptions
- The `Any` type annotations are sufficient for structural compatibility with the `ArtifactStore` protocol. Since these are duck-typed implementations (not explicitly inheriting from `ArtifactStore`), and the `@runtime_checkable` protocol check only verifies method names exist (not their signatures), using `Any` annotations does not affect protocol conformance.
- The `_DisabledArtifactStore.list` return type uses `list[Any]` instead of bare `Any` to be slightly more precise while still avoiding the import issue. This is a minor improvement over `Any` alone.

### Deviations from Plan
- **Type annotations changed from specific types to `Any`.** The plan specified `scope: "ArtifactScope | None" = None` and `-> "list[ArtifactRef]"` with local imports. This was changed to `scope: Any | None = None` and `-> Any` / `-> list[Any]` to pass ruff's `UP037`, `F821`, and `F401` checks. The root cause is that `playground.py` uses `from __future__ import annotations`, making quoted forward references redundant and triggering ruff's pyupgrade rule, while the local imports become flagged as unused.
- **Local imports for `ArtifactRef` and `ArtifactScope` were omitted.** They were not needed since the annotations use `Any` and the method bodies do not reference these types directly.

### Potential Risks & Reviewer Attention Points
- The type annotations are less precise than the protocol signature. If a future static analysis pass or stricter mypy configuration checks duck-typed protocol conformance at the annotation level, these methods might need updated annotations. For now, this matches the existing convention in both classes and passes all linting/type-checking.
- The `scope=scope or self._scope` pattern in `_ScopedArtifactStore.list` uses truthiness-based defaulting, which is consistent with all other methods in that class (e.g., `put_bytes`, `put_text`). If `scope` is an empty `ArtifactScope(...)` with all-None fields, it would be truthy and would override `self._scope`. This is the same behavior as the existing methods and is unlikely to be a problem in practice.

### Files Modified
- `penguiflow/cli/playground.py` -- Added `list` method to both `_ScopedArtifactStore` (line 1115) and `_DisabledArtifactStore` (line 1139).
