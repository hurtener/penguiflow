# Phase 004: `ResourceCache` -- remove stored `ArtifactStore`, use `ctx.artifacts` per-call

## Objective

`ResourceCache` is constructed once per `ToolNode` with `artifact_store=ctx._artifacts` cached in `self._artifact_store`. If a `ToolNode` is reused across sessions with different scopes, the cached store holds the wrong scope. This phase removes the stored `artifact_store` from the constructor and instead uses `ctx.artifacts` (the `ScopedArtifacts` facade) directly in `get_or_fetch` and `_fetch_and_store`, which already receive `ctx: ToolContext` as a parameter. It also updates the construction call site in `node.py` and fixes 5 test constructions in `tests/test_toolnode_phase2.py`.

## Tasks
1. Remove `artifact_store` parameter from `ResourceCache.__init__` in `resources.py`.
2. Replace 3 `self._artifact_store` call sites with `ctx.artifacts` in `resources.py`.
3. Update the `TYPE_CHECKING` import to remove `ArtifactStore` in `resources.py`.
4. Update the `ResourceCache` construction in `node.py` to remove `artifact_store=`.
5. Update 5 test constructions in `tests/test_toolnode_phase2.py`.

## Detailed Steps

### Step 1: Update `ResourceCache.__init__` in `penguiflow/tools/resources.py`

- Locate the constructor at lines 144-162.
- Remove the `artifact_store: ArtifactStore` parameter from the signature.
- Remove `self._artifact_store = artifact_store` from the constructor body (line 157).
- Update the docstring to remove the `artifact_store` arg description.

**Before (lines 144-162):**
```python
def __init__(
    self,
    artifact_store: ArtifactStore,
    namespace: str,
    config: ResourceCacheConfig | None = None,
) -> None:
    """Initialize the resource cache.

    Args:
        artifact_store: Store for binary/large text content
        namespace: ToolNode namespace for artifact naming
        config: Cache configuration
    """
    self._artifact_store = artifact_store
    self._namespace = namespace
    self._config = config or ResourceCacheConfig()
    self._entries: dict[str, _CacheEntry] = {}
    self._lock = asyncio.Lock()
    self._time_source = time.monotonic
```

**After:**
```python
def __init__(
    self,
    namespace: str,
    config: ResourceCacheConfig | None = None,
) -> None:
    """Initialize the resource cache.

    Args:
        namespace: ToolNode namespace for artifact naming
        config: Cache configuration
    """
    self._namespace = namespace
    self._config = config or ResourceCacheConfig()
    self._entries: dict[str, _CacheEntry] = {}
    self._lock = asyncio.Lock()
    self._time_source = time.monotonic
```

### Step 2: Replace `self._artifact_store` with `ctx.artifacts` in `resources.py`

There are 3 call sites using `self._artifact_store`. Replace each with `ctx.artifacts`:

**Call site 1 -- line 188** (in `get_or_fetch`, checking if artifact still exists):
```python
# Before:
if await self._artifact_store.exists(entry.artifact_ref.id):
# After:
if await ctx.artifacts.exists(entry.artifact_ref.id):
```

**Call site 2 -- line 249** (in `_fetch_and_store`, storing binary blob):
```python
# Before:
ref = await self._artifact_store.put_bytes(
    data,
    mime_type=mime_type or "application/octet-stream",
    namespace=f"{self._namespace}.resource",
)
# After:
ref = await ctx.artifacts.upload(
    data,
    mime_type=mime_type or "application/octet-stream",
    namespace=f"{self._namespace}.resource",
)
```

**Call site 3 -- line 275** (in `_fetch_and_store`, storing large text):
```python
# Before:
ref = await self._artifact_store.put_text(
    text,
    mime_type=mime_type or "text/plain",
    namespace=f"{self._namespace}.resource",
)
# After:
ref = await ctx.artifacts.upload(
    text,
    mime_type=mime_type or "text/plain",
    namespace=f"{self._namespace}.resource",
)
```

Both `get_or_fetch` (line 164) and `_fetch_and_store` (line 200) already receive `ctx: ToolContext` as a parameter, so `ctx.artifacts` is accessible without any signature changes.

### Step 3: Update `TYPE_CHECKING` import in `resources.py`

- Locate lines 22-24:
  ```python
  if TYPE_CHECKING:
      from penguiflow.artifacts import ArtifactRef, ArtifactStore
      from penguiflow.planner.context import ToolContext
  ```
- Remove `ArtifactStore` from the import (it is no longer used anywhere in the file after the constructor change):
  ```python
  if TYPE_CHECKING:
      from penguiflow.artifacts import ArtifactRef
      from penguiflow.planner.context import ToolContext
  ```
- `ArtifactRef` is still needed (used in `_CacheEntry.artifact_ref` type annotation).

### Step 4: Update `ResourceCache` construction in `penguiflow/tools/node.py`

- Locate lines 1773-1779:
  ```python
  self._resource_cache = ResourceCache(
      artifact_store=ctx._artifacts,
      namespace=self.config.name,
      config=ResourceCacheConfig(
          inline_text_if_under_chars=self.config.artifact_extraction.resources.inline_text_if_under_chars,
      ),
  )
  ```
- Remove the `artifact_store=ctx._artifacts` argument:
  ```python
  self._resource_cache = ResourceCache(
      namespace=self.config.name,
      config=ResourceCacheConfig(
          inline_text_if_under_chars=self.config.artifact_extraction.resources.inline_text_if_under_chars,
      ),
  )
  ```
- After this change, there should be zero remaining `ctx._artifacts` references in `node.py`.

### Step 5: Update 5 test constructions in `tests/test_toolnode_phase2.py`

All 5 `ResourceCache(artifact_store, ...)` calls must drop the first positional argument:

**Line 96** (fixture `resource_cache`):
```python
# Before:
return ResourceCache(artifact_store, "test", config)
# After:
return ResourceCache("test", config)
```

**Line 228** (test `test_cache_get_or_fetch_text_large`):
```python
# Before:
cache = ResourceCache(artifact_store, "test", config)
# After:
cache = ResourceCache("test", config)
```

**Line 335** (test `test_cache_eviction`):
```python
# Before:
cache = ResourceCache(artifact_store, "test", config)
# After:
cache = ResourceCache("test", config)
```

**Line 368** (test `test_cache_disabled`):
```python
# Before:
cache = ResourceCache(artifact_store, "test", config)
# After:
cache = ResourceCache("test", config)
```

**Line 693** (test `test_handle_resource_updated_invalidates_cache`):
```python
# Before:
cache = ResourceCache(store, "test", cache_config)
# After:
cache = ResourceCache("test", cache_config)
```

The `DummyCtx` in `test_toolnode_phase2.py` already exposes `ctx.artifacts` as `ScopedArtifacts` (lines 62-70), so test calls to `cache.get_or_fetch(uri, read_fn, ctx)` will work without further changes.

## Required Code

```python
# Target file: penguiflow/tools/resources.py
# Updated constructor (lines 144-162):

def __init__(
    self,
    namespace: str,
    config: ResourceCacheConfig | None = None,
) -> None:
    """Initialize the resource cache.

    Args:
        namespace: ToolNode namespace for artifact naming
        config: Cache configuration
    """
    self._namespace = namespace
    self._config = config or ResourceCacheConfig()
    self._entries: dict[str, _CacheEntry] = {}
    self._lock = asyncio.Lock()
    self._time_source = time.monotonic
```

```python
# Target file: penguiflow/tools/resources.py
# Updated TYPE_CHECKING import (lines 22-24):

if TYPE_CHECKING:
    from penguiflow.artifacts import ArtifactRef
    from penguiflow.planner.context import ToolContext
```

```python
# Target file: penguiflow/tools/node.py
# Updated ResourceCache construction (lines 1773-1779):

self._resource_cache = ResourceCache(
    namespace=self.config.name,
    config=ResourceCacheConfig(
        inline_text_if_under_chars=self.config.artifact_extraction.resources.inline_text_if_under_chars,
    ),
)
```

## Exit Criteria (Success)
- [ ] `ResourceCache.__init__` no longer accepts `artifact_store` parameter
- [ ] `self._artifact_store` does not appear anywhere in `penguiflow/tools/resources.py`
- [ ] `ArtifactStore` is not imported in `penguiflow/tools/resources.py`
- [ ] `ctx.artifacts.exists(` appears in `get_or_fetch` (line ~188)
- [ ] `ctx.artifacts.upload(` appears twice in `_fetch_and_store` (lines ~249 and ~275)
- [ ] `ResourceCache` construction in `node.py` no longer passes `artifact_store=`
- [ ] Zero remaining `ctx._artifacts` references in `penguiflow/tools/node.py`
- [ ] All 5 test constructions in `tests/test_toolnode_phase2.py` are updated
- [ ] No import or syntax errors in any modified file
- [ ] All tests pass

## Implementation Notes
- `ScopedArtifacts.exists()` is scope-checked (returns `False` for artifacts from a different scope), unlike `ArtifactStore.exists()` which only checks storage. This is intentional -- scope enforcement is the purpose of this migration. Since `ResourceCache` stores and reads back its own artifacts within the same session, the scope is consistent and this works correctly.
- Depends on Phase 003 (Phase 003 migrates the 9 `put_bytes`/`put_text` call sites in `node.py`; this phase handles the remaining `ctx._artifacts` reference at the `ResourceCache` init).
- After this phase, `penguiflow/tools/node.py` should have zero references to `ctx._artifacts`.

## Verification Commands
```bash
# Verify no remaining self._artifact_store in resources.py:
grep -n "self._artifact_store" penguiflow/tools/resources.py && echo "FAIL" || echo "OK"

# Verify no remaining ctx._artifacts in node.py:
grep -n "ctx._artifacts" penguiflow/tools/node.py && echo "FAIL" || echo "OK"

# Lint and type check:
uv run ruff check penguiflow/tools/resources.py penguiflow/tools/node.py
uv run mypy penguiflow/tools/resources.py penguiflow/tools/node.py

# Run relevant tests:
uv run pytest tests/test_toolnode_phase2.py -v
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes
- **`penguiflow/tools/resources.py`**: Removed `artifact_store: ArtifactStore` parameter from `ResourceCache.__init__`, removed `self._artifact_store` assignment, updated docstring. Replaced 3 `self._artifact_store` call sites with `ctx.artifacts`: `self._artifact_store.exists()` -> `ctx.artifacts.exists()` in `get_or_fetch`, and `self._artifact_store.put_bytes()` / `self._artifact_store.put_text()` -> `ctx.artifacts.upload()` in `_fetch_and_store`. Removed `ArtifactStore` from the `TYPE_CHECKING` import (kept `ArtifactRef` which is still used).
- **`penguiflow/tools/node.py`**: Removed `artifact_store=ctx._artifacts` argument from `ResourceCache` construction (line 1774). This was the last remaining `ctx._artifacts` reference in `node.py`.
- **`tests/test_toolnode_phase2.py`**: Updated 5 `ResourceCache` constructions to drop the `artifact_store`/`store` first positional argument. Also removed the now-unused `store = InMemoryArtifactStore()` variable in `test_toolnode_handle_resource_updated` to fix a ruff F841 lint error.

### Key Considerations
- The `ScopedArtifacts.upload()` method accepts both `bytes` and `str` data, so it serves as a unified replacement for both `ArtifactStore.put_bytes()` and `ArtifactStore.put_text()`. The `mime_type` and `namespace` keyword arguments are compatible.
- The `ScopedArtifacts.exists()` method performs scope checking (returns `False` for artifacts from a different scope), which is stricter than `ArtifactStore.exists()`. This is intentional and correct because `ResourceCache` stores and reads back its own artifacts within the same session, so scope is always consistent.
- Both `get_or_fetch` and `_fetch_and_store` already receive `ctx: ToolContext` as a parameter, so no signature changes were needed to access `ctx.artifacts`.

### Assumptions
- The `DummyCtx` in the test file already exposes `ctx.artifacts` as a `ScopedArtifacts` instance (confirmed at lines 62-70), so tests work without further modification.
- The docstrings/comments in `resources.py` that still mention "ArtifactStore" (module docstring line 7, class docstring lines 134 and 137) are descriptive references, not code dependencies, and were intentionally left as-is since the phase plan did not call for updating them.

### Deviations from Plan
- Removed the unused `store = InMemoryArtifactStore()` variable in `test_toolnode_handle_resource_updated` (line 688). This variable became unused after removing it from the `ResourceCache` constructor call, and ruff flagged it as an F841 error. The phase plan did not explicitly mention this cleanup, but it was necessary to pass lint checks.

### Potential Risks & Reviewer Attention Points
- The docstrings in `ResourceCache` class (lines 134-137) still reference "ArtifactStore" in descriptive text. Consider updating these to reference "ScopedArtifacts" or "ctx.artifacts" for accuracy in a follow-up.
- Verify that no other code paths construct `ResourceCache` outside the three files modified here (confirmed via grep -- no other construction sites exist).

### Files Modified
- `penguiflow/tools/resources.py` -- Removed `artifact_store` from constructor, replaced 3 `self._artifact_store` usages with `ctx.artifacts`, updated `TYPE_CHECKING` import
- `penguiflow/tools/node.py` -- Removed `artifact_store=ctx._artifacts` from `ResourceCache` construction
- `tests/test_toolnode_phase2.py` -- Updated 5 `ResourceCache` constructions, removed 1 unused variable
