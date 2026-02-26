# Phase 009: Tests for `ScopedArtifacts` -- List, Exists, Delete + Final Verification

## Objective
Add the remaining `ScopedArtifacts` facade tests (list, exists, delete) and run the full verification suite to confirm the entire implementation is complete and correct. This is the final phase.

## Tasks
1. Add `TestScopedArtifactsList` test class
2. Add `TestScopedArtifactsExists` test class
3. Add `TestScopedArtifactsDelete` test class
4. Run full verification suite

## Detailed Steps

### Step 1: Add `TestScopedArtifactsList` class
Tests for `ScopedArtifacts.list()` -- verifies that listing is scoped to tenant/user/session but not trace.

### Step 2: Add `TestScopedArtifactsExists` class
Tests for `ScopedArtifacts.exists()` -- verifies scope enforcement on existence checks.

### Step 3: Add `TestScopedArtifactsDelete` class
Tests for `ScopedArtifacts.delete()` -- verifies scope enforcement on deletion.

### Step 4: Run full verification
Run `uv run pytest tests/`, `uv run ruff check .`, and `uv run mypy` to confirm everything passes.

## Required Code

```python
# Target file: tests/test_artifacts.py
# Append after the Phase 008 test classes:


class TestScopedArtifactsList:
    """Tests for ScopedArtifacts.list()."""

    @pytest.mark.asyncio
    async def test_list_returns_own_scope_artifacts(self) -> None:
        """Upload artifacts via facade. list() returns them."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        await facade.upload(b"data1", namespace="ns1")
        await facade.upload(b"data2", namespace="ns2")

        result = await facade.list()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_excludes_different_tenant(self) -> None:
        """Artifacts for a different tenant are excluded from list."""
        store = InMemoryArtifactStore()

        # Store artifacts for two tenants via raw store
        await store.put_bytes(
            b"a", namespace="ns1", scope=ArtifactScope(tenant_id="t1", session_id="s1")
        )
        await store.put_bytes(
            b"b", namespace="ns2", scope=ArtifactScope(tenant_id="t2", session_id="s1")
        )

        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id=None
        )
        result = await facade.list()
        assert len(result) == 1
        assert result[0].scope is not None and result[0].scope.tenant_id == "t1"

    @pytest.mark.asyncio
    async def test_list_includes_all_traces(self) -> None:
        """list() returns artifacts from all traces (same tenant/user/session)."""
        store = InMemoryArtifactStore()

        # Upload with two different trace IDs
        facade_tr1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        await facade_tr1.upload(b"data1", namespace="ns1")

        facade_tr2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        await facade_tr2.upload(b"data2", namespace="ns2")

        # List from either facade should see both
        result = await facade_tr1.list()
        assert len(result) == 2

        result2 = await facade_tr2.list()
        assert len(result2) == 2

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        """No artifacts stored: list() returns []."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id=None
        )
        result = await facade.list()
        assert result == []


class TestScopedArtifactsExists:
    """Tests for ScopedArtifacts.exists()."""

    @pytest.mark.asyncio
    async def test_exists_same_scope(self) -> None:
        """Upload, then exists() returns True."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"data")
        assert await facade.exists(ref.id) is True

    @pytest.mark.asyncio
    async def test_exists_different_tenant_denied(self) -> None:
        """Different tenant: exists() returns False."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id=None, trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t2", user_id=None, session_id=None, trace_id=None
        )
        assert await facade2.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_exists_different_trace_allowed(self) -> None:
        """Different trace, same tenant/user/session: exists() returns True."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        assert await facade2.exists(ref.id) is True

    @pytest.mark.asyncio
    async def test_exists_nonexistent(self) -> None:
        """Unknown ID: exists() returns False."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        assert await facade.exists("nonexistent_id") is False


class TestScopedArtifactsDelete:
    """Tests for ScopedArtifacts.delete()."""

    @pytest.mark.asyncio
    async def test_delete_same_scope(self) -> None:
        """Upload, delete() returns True, artifact is gone."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"data")

        assert await facade.delete(ref.id) is True
        assert await store.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_delete_different_tenant_denied(self) -> None:
        """Different tenant: delete() returns False, artifact still exists."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id=None, trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t2", user_id=None, session_id=None, trace_id=None
        )
        assert await facade2.delete(ref.id) is False
        # Artifact still exists in raw store
        assert await store.exists(ref.id) is True

    @pytest.mark.asyncio
    async def test_delete_different_trace_allowed(self) -> None:
        """Different trace, same tenant/user/session: delete() succeeds."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        assert await facade2.delete(ref.id) is True
        assert await store.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self) -> None:
        """Unknown ID: delete() returns False."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        assert await facade.delete("nonexistent_id") is False
```

## Exit Criteria (Success)
- [ ] `TestScopedArtifactsList` class exists with 4 test methods, all passing
- [ ] `TestScopedArtifactsExists` class exists with 4 test methods, all passing
- [ ] `TestScopedArtifactsDelete` class exists with 4 test methods, all passing
- [ ] `uv run ruff check .` passes with zero errors
- [ ] `uv run mypy` passes with zero new errors
- [ ] `uv run pytest tests/` passes with no new failures (pre-existing 21 failures allowed)
- [ ] Total new test count: 18 tests in this phase + 18 from Phase 007 + 21 from Phase 008 = 57 new tests

## Implementation Notes
- The `list()` tests verify that the facade uses `_read_scope` (no trace_id) for filtering, so artifacts from different traces but the same tenant/user/session are all returned.
- The `exists()` and `delete()` tests verify that `_check_scope` is applied correctly:
  - Different trace is allowed (trace_id is not checked on reads/deletes)
  - Different tenant/session is denied
  - Nonexistent artifacts return `False`
- The `delete()` tests verify both the return value AND the actual state of the raw store (artifact is gone or still present).
- This is the final phase. After completing it, run the full verification suite from the plan:
  1. `uv run pytest tests/` -- no new failures
  2. `uv run ruff check .` -- zero lint errors
  3. `uv run mypy` -- zero new type errors
  4. Verify `ScopedArtifacts` scope immutability
  5. Verify `upload` injects scope correctly
  6. Verify `list()` returns only scoped artifacts
  7. Verify `download`/`get_metadata`/`exists`/`delete` enforce scope check on tenant/user/session only (not trace)
  8. Verify scope check allows access to unscoped artifacts
  9. Verify internal code (`node.py`, `tool_jobs.py`) still works via `ctx._artifacts`
  10. Verify `ToolJobContext` satisfies updated `ToolContext` protocol

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow

# Run just the new tests
uv run pytest tests/test_artifacts.py -x -q -v

# Full verification suite (final gate)
uv run ruff check .
uv run mypy
uv run pytest tests/ -x -q

# Verify test count (should have ~57 new tests in test_artifacts.py beyond the original set)
uv run pytest tests/test_artifacts.py --co -q | tail -5
```
