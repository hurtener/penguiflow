# Phase 003: Create `ScopedArtifacts` Facade Class

## Objective
Implement the `ScopedArtifacts` class in `penguiflow/artifacts.py`. This is the core of Enhancement 3 -- a facade that wraps an `ArtifactStore` and automatically injects scope (tenant_id, user_id, session_id, trace_id) on writes, enforces scope on reads, and is immutable after construction. The class is added to `__all__` but not yet wired into any contexts (that happens in Phases 004-005).

## Tasks
1. Add `ScopedArtifacts` class to `penguiflow/artifacts.py`
2. Add `"ScopedArtifacts"` to `__all__`

## Detailed Steps

### Step 1: Add `ScopedArtifacts` class -- `penguiflow/artifacts.py`
- Place the class between the Protocol section (after `ArtifactStore`, around line 220) and the Discovery section (`discover_artifact_store`, around line 226). This keeps the facade logically adjacent to the protocol it wraps.
- The class has `__slots__ = ("_store", "_scope", "_read_scope")`.
- `__init__` uses `object.__setattr__` for all slot assignments because `__setattr__` is overridden to raise `AttributeError` (immutability).
- `_read_scope` is the same as `_scope` but with `trace_id=None` -- used for `list()` so reads return all artifacts for the tenant/user/session, not just the current trace.
- Public methods: `scope` (property), `upload`, `download`, `get_metadata`, `list`, `exists`, `delete`.
- Private method: `_check_scope` -- access control check (different from `_scope_matches`).

### Step 2: Add to `__all__`
- Add `"ScopedArtifacts"` to the `__all__` list in `penguiflow/artifacts.py`.

## Required Code

```python
# Target file: penguiflow/artifacts.py
# Add "ScopedArtifacts" to __all__ (around line 24-32):
__all__ = [
    "ArtifactRef",
    "ArtifactScope",
    "ArtifactStore",
    "ArtifactRetentionConfig",
    "NoOpArtifactStore",
    "InMemoryArtifactStore",
    "ScopedArtifacts",
    "discover_artifact_store",
]
```

```python
# Target file: penguiflow/artifacts.py
# Add BETWEEN the ArtifactStore protocol (ends ~line 220) and the Discovery section (starts ~line 222).
# The exact insertion point is after the protocol class closing and before the Discovery comment block.

class ScopedArtifacts:
    """Scoped facade over ArtifactStore for tool developers.

    Automatically injects tenant_id/user_id/session_id/trace_id on writes
    and enforces scope on reads. Immutable after construction.
    """

    __slots__ = ("_store", "_scope", "_read_scope")

    def __init__(
        self,
        store: ArtifactStore,
        *,
        tenant_id: str | None,
        user_id: str | None,
        session_id: str | None,
        trace_id: str | None,
    ) -> None:
        # IMPORTANT: Must use object.__setattr__ because __setattr__ is
        # overridden to raise AttributeError (immutability).
        object.__setattr__(self, "_store", store)
        object.__setattr__(
            self,
            "_scope",
            ArtifactScope(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
            ),
        )
        # Read scope: same as _scope but without trace_id, so reads
        # return all artifacts for the tenant/user/session.
        object.__setattr__(
            self,
            "_read_scope",
            ArtifactScope(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                trace_id=None,
            ),
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ScopedArtifacts is immutable")

    @property
    def scope(self) -> ArtifactScope:
        """Read-only access to the fixed scope."""
        return self._scope

    async def upload(
        self,
        data: bytes | str,
        *,
        mime_type: str | None = None,
        filename: str | None = None,
        namespace: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRef:
        """Store data and return an ArtifactRef. Scope is always injected."""
        if isinstance(data, str):
            return await self._store.put_text(
                data,
                mime_type=mime_type or "text/plain",  # resolve None -> "text/plain"
                filename=filename,
                namespace=namespace,
                scope=self._scope,
                meta=meta,
            )
        else:
            return await self._store.put_bytes(
                data,
                mime_type=mime_type,  # None is valid for bytes
                filename=filename,
                namespace=namespace,
                scope=self._scope,
                meta=meta,
            )

    async def download(self, artifact_id: str) -> bytes | None:
        """Retrieve artifact bytes by ID (scope-checked)."""
        ref = await self._store.get_ref(artifact_id)
        if ref is None:
            return None
        if not self._check_scope(ref):
            return None
        return await self._store.get(artifact_id)

    async def get_metadata(self, artifact_id: str) -> ArtifactRef | None:
        """Retrieve artifact metadata by ID (scope-checked)."""
        ref = await self._store.get_ref(artifact_id)
        if ref is None:
            return None
        if not self._check_scope(ref):
            return None
        return ref

    async def list(self) -> list[ArtifactRef]:
        """List artifacts matching this facade's tenant/user/session (not trace)."""
        return await self._store.list(scope=self._read_scope)

    async def exists(self, artifact_id: str) -> bool:
        """Check if an artifact exists and passes scope check."""
        ref = await self._store.get_ref(artifact_id)
        if ref is None:
            return False
        return self._check_scope(ref)

    async def delete(self, artifact_id: str) -> bool:
        """Delete an artifact if it passes scope check."""
        ref = await self._store.get_ref(artifact_id)
        if ref is None:
            return False
        if not self._check_scope(ref):
            return False
        return await self._store.delete(artifact_id)

    def _check_scope(self, ref: ArtifactRef) -> bool:
        """Check if an artifact's scope is compatible with this facade's scope.

        Access control semantics (NOT filtering):
        - If artifact has no scope (ref.scope is None) -> PASS (unrestricted artifact).
        - For each of tenant_id, user_id, session_id (NOT trace_id):
          if BOTH facade field and artifact field are non-None, they must match.
          If either is None, that dimension passes.
        """
        if ref.scope is None:
            return True
        for field in ("tenant_id", "user_id", "session_id"):
            facade_val = getattr(self._scope, field)
            artifact_val = getattr(ref.scope, field)
            if facade_val is not None and artifact_val is not None and facade_val != artifact_val:
                return False
        return True
```

## Exit Criteria (Success)
- [ ] `ScopedArtifacts` class exists in `penguiflow/artifacts.py` with all 7 public methods (`scope`, `upload`, `download`, `get_metadata`, `list`, `exists`, `delete`) and 1 private method (`_check_scope`)
- [ ] `"ScopedArtifacts"` is in `__all__`
- [ ] `ScopedArtifacts.__setattr__` raises `AttributeError` (immutability)
- [ ] `__slots__` is `("_store", "_scope", "_read_scope")`
- [ ] `upload` handles both `bytes` and `str`, defaulting `mime_type` to `"text/plain"` for `str`
- [ ] `_check_scope` checks `tenant_id`, `user_id`, `session_id` only (NOT `trace_id`)
- [ ] `_check_scope` passes when artifact has `scope=None` (unrestricted)
- [ ] `list()` uses `self._read_scope` (no `trace_id`)
- [ ] `uv run ruff check penguiflow/artifacts.py` passes
- [ ] `uv run mypy` passes with zero new errors
- [ ] `uv run pytest tests/test_artifacts.py` passes (existing tests still pass)

## Implementation Notes
- **WARNING -- `_check_scope` must NOT delegate to `_scope_matches`:** These two functions have intentionally different semantics for `None` artifact fields:
  - `_scope_matches` (used by `list()` filtering): artifact field=`None` + filter field=non-`None` -> **FAIL** (the artifact doesn't positively match the filter)
  - `_check_scope` (used by `download`/`exists`/`delete` access control): artifact field=`None` + facade field=non-`None` -> **PASS** (artifact has no restriction on that dimension, so access is allowed)
- Do NOT reuse `_scope_matches` inside `_check_scope`. They are independent implementations.
- The `upload` method passes the **full** `self._scope` (including `trace_id`) to `put_bytes`/`put_text` so newly created artifacts are tagged with the trace that produced them. Only reads are broadened.
- Because `put_text`'s signature is `mime_type: str = "text/plain"` (not `str | None`), `upload` must NOT pass `None` through when data is `str`. The `mime_type or "text/plain"` pattern resolves this.
- This phase depends on Phase 000 (the `list` method on `ArtifactStore` must exist) and Phase 002 (the Enhancement 2 checkpoint must have passed).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv run ruff check penguiflow/artifacts.py
uv run mypy
uv run pytest tests/test_artifacts.py -x -q
```
