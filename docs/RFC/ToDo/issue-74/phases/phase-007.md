# Phase 007: Tests for `list`, `_scope_matches`, and Store Implementations

## Objective
Add comprehensive tests for the `ArtifactStore.list()` method (across all implementations) and the `_scope_matches` helper function. All tests go in the existing `tests/test_artifacts.py` file. This is the first of three test phases.

## Tasks
1. Add `TestInMemoryArtifactStoreList` test class
2. Add `TestNoOpArtifactStoreList` test class
3. Add `TestPlaygroundArtifactStoreList` test class
4. Add `TestEventEmittingProxyList` test class
5. Add `TestScopeMatches` test class

## Detailed Steps

### Step 1: Add imports needed for new tests
At the top of `tests/test_artifacts.py`, ensure these additional imports are present:
```python
import time
from unittest.mock import AsyncMock

from penguiflow.artifacts import _scope_matches
from penguiflow.state.in_memory import PlaygroundArtifactStore
from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy
```

### Step 2: Add `TestInMemoryArtifactStoreList` class
Place after the existing `TestInMemoryArtifactStoreEviction` class. Tests:

| Test | Description |
|---|---|
| `test_list_empty_store` | `list()` on a fresh store returns `[]`. |
| `test_list_no_scope_returns_all` | Store several artifacts (some with scope, some without). `list(scope=None)` returns all. |
| `test_list_filters_by_tenant_id` | Store with different `tenant_id`. Filter returns only matching. |
| `test_list_filters_by_session_id` | Same pattern for `session_id`. |
| `test_list_filters_by_user_id` | Same pattern for `user_id`. |
| `test_list_filters_multiple_dimensions` | Filter on `tenant_id` + `session_id` together. |
| `test_list_unscoped_artifacts_match_none_filter` | Artifacts with `scope=None` match all-None filter but fail non-None filter. |
| `test_list_expired_artifacts_excluded` | Manipulate `created_at` to expire an artifact; `list()` should not return it. |

### Step 3: Add `TestNoOpArtifactStoreList` class
| Test | Description |
|---|---|
| `test_list_always_empty` | `NoOpArtifactStore().list()` returns `[]` regardless of scope. |

### Step 4: Add `TestPlaygroundArtifactStoreList` class
| Test | Description |
|---|---|
| `test_list_scoped_to_session` | Store in two sessions. Filter by `session_id="s1"` returns only s1 artifacts. |
| `test_list_no_scope_aggregates_all_sessions` | `list(scope=None)` returns artifacts from all sessions. |

### Step 5: Add `TestEventEmittingProxyList` class
| Test | Description |
|---|---|
| `test_list_delegates_to_inner_store` | Verify delegation returns same results as inner store. |

### Step 6: Add `TestScopeMatches` class
| Test | Description |
|---|---|
| `test_none_artifact_scope_matches_all_none_filter` | `_scope_matches(None, ArtifactScope())` is `True`. |
| `test_none_artifact_scope_fails_non_none_filter` | `_scope_matches(None, ArtifactScope(tenant_id="t1"))` is `False`. |
| `test_exact_match` | All fields match -> `True`. |
| `test_partial_filter_match` | Filter has one field, artifact has that + others -> `True`. |
| `test_mismatch` | Filter field != artifact field -> `False`. |
| `test_none_filter_field_matches_any` | None filter field matches any artifact value. |

## Required Code

```python
# Target file: tests/test_artifacts.py
# Add these imports at the top (merge with existing imports):

import time

from penguiflow.artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    ArtifactStore,
    InMemoryArtifactStore,
    NoOpArtifactStore,
    _scope_matches,
    discover_artifact_store,
)
from penguiflow.state.in_memory import PlaygroundArtifactStore
```

```python
# Target file: tests/test_artifacts.py
# Append after the last existing test class (TestNoOpArtifactStoreWarning):


class TestInMemoryArtifactStoreList:
    """Tests for InMemoryArtifactStore.list()."""

    @pytest.mark.asyncio
    async def test_list_empty_store(self) -> None:
        """list() on a fresh store returns []."""
        store = InMemoryArtifactStore()
        result = await store.list()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_no_scope_returns_all(self) -> None:
        """list(scope=None) returns all artifacts."""
        store = InMemoryArtifactStore()
        await store.put_bytes(b"a", namespace="ns1", scope=ArtifactScope(tenant_id="t1"))
        await store.put_bytes(b"b", namespace="ns2")  # no scope
        await store.put_text("c", namespace="ns3", scope=ArtifactScope(session_id="s1"))

        result = await store.list(scope=None)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_list_filters_by_tenant_id(self) -> None:
        """Filter by tenant_id returns only matching artifacts."""
        store = InMemoryArtifactStore()
        await store.put_bytes(b"a", namespace="ns1", scope=ArtifactScope(tenant_id="t1"))
        await store.put_bytes(b"b", namespace="ns2", scope=ArtifactScope(tenant_id="t2"))
        await store.put_bytes(b"c", namespace="ns3", scope=ArtifactScope(tenant_id="t1"))

        result = await store.list(scope=ArtifactScope(tenant_id="t1"))
        assert len(result) == 2
        assert all(r.scope is not None and r.scope.tenant_id == "t1" for r in result)

    @pytest.mark.asyncio
    async def test_list_filters_by_session_id(self) -> None:
        """Filter by session_id returns only matching artifacts."""
        store = InMemoryArtifactStore()
        await store.put_bytes(b"a", namespace="ns1", scope=ArtifactScope(session_id="s1"))
        await store.put_bytes(b"b", namespace="ns2", scope=ArtifactScope(session_id="s2"))

        result = await store.list(scope=ArtifactScope(session_id="s1"))
        assert len(result) == 1
        assert result[0].scope is not None and result[0].scope.session_id == "s1"

    @pytest.mark.asyncio
    async def test_list_filters_by_user_id(self) -> None:
        """Filter by user_id returns only matching artifacts."""
        store = InMemoryArtifactStore()
        await store.put_bytes(b"a", namespace="ns1", scope=ArtifactScope(user_id="u1"))
        await store.put_bytes(b"b", namespace="ns2", scope=ArtifactScope(user_id="u2"))

        result = await store.list(scope=ArtifactScope(user_id="u1"))
        assert len(result) == 1
        assert result[0].scope is not None and result[0].scope.user_id == "u1"

    @pytest.mark.asyncio
    async def test_list_filters_multiple_dimensions(self) -> None:
        """Filter on tenant_id + session_id together returns only matching artifacts."""
        store = InMemoryArtifactStore()
        await store.put_bytes(
            b"a", namespace="ns1", scope=ArtifactScope(tenant_id="t1", session_id="s1")
        )
        await store.put_bytes(
            b"b", namespace="ns2", scope=ArtifactScope(tenant_id="t1", session_id="s2")
        )
        await store.put_bytes(
            b"c", namespace="ns3", scope=ArtifactScope(tenant_id="t2", session_id="s1")
        )

        result = await store.list(scope=ArtifactScope(tenant_id="t1", session_id="s1"))
        assert len(result) == 1
        ref = result[0]
        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        assert ref.scope.session_id == "s1"

    @pytest.mark.asyncio
    async def test_list_unscoped_artifacts_match_none_filter(self) -> None:
        """Unscoped artifacts match all-None filter but fail non-None filter."""
        store = InMemoryArtifactStore()
        await store.put_bytes(b"unscoped", namespace="ns1")  # scope=None

        # All-None filter -> matches
        result = await store.list(scope=ArtifactScope())
        assert len(result) == 1

        # Non-None filter -> does not match
        result = await store.list(scope=ArtifactScope(tenant_id="t1"))
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_list_expired_artifacts_excluded(self) -> None:
        """Expired artifacts are not returned by list()."""
        config = ArtifactRetentionConfig(ttl_seconds=10)
        store = InMemoryArtifactStore(retention=config)

        ref = await store.put_bytes(b"data", namespace="ns1")
        assert len(await store.list()) == 1

        # Manipulate created_at to simulate expiration
        stored = store._artifacts[ref.id]
        # Replace with a new _StoredArtifact with old created_at
        from penguiflow.artifacts import _StoredArtifact

        store._artifacts[ref.id] = _StoredArtifact(
            ref=stored.ref,
            data=stored.data,
            created_at=time.time() - 20,  # 20 seconds ago, TTL is 10
        )

        result = await store.list()
        assert len(result) == 0


class TestNoOpArtifactStoreList:
    """Tests for NoOpArtifactStore.list()."""

    @pytest.mark.asyncio
    async def test_list_always_empty(self) -> None:
        """NoOpArtifactStore.list() returns [] regardless of scope."""
        store = NoOpArtifactStore()
        assert await store.list() == []
        assert await store.list(scope=ArtifactScope(tenant_id="t1")) == []
        assert await store.list(scope=None) == []


class TestPlaygroundArtifactStoreList:
    """Tests for PlaygroundArtifactStore.list()."""

    @pytest.mark.asyncio
    async def test_list_scoped_to_session(self) -> None:
        """list(scope=session_id) returns only that session's artifacts."""
        store = PlaygroundArtifactStore()
        await store.put_bytes(b"a", namespace="ns1", scope=ArtifactScope(session_id="s1"))
        await store.put_bytes(b"b", namespace="ns2", scope=ArtifactScope(session_id="s2"))

        result = await store.list(scope=ArtifactScope(session_id="s1"))
        assert len(result) == 1
        assert result[0].scope is not None and result[0].scope.session_id == "s1"

    @pytest.mark.asyncio
    async def test_list_no_scope_aggregates_all_sessions(self) -> None:
        """list(scope=None) returns artifacts from all sessions."""
        store = PlaygroundArtifactStore()
        await store.put_bytes(b"a", namespace="ns1", scope=ArtifactScope(session_id="s1"))
        await store.put_bytes(b"b", namespace="ns2", scope=ArtifactScope(session_id="s2"))
        await store.put_bytes(b"c", namespace="ns3", scope=ArtifactScope(session_id="s1"))

        result = await store.list(scope=None)
        assert len(result) == 3


class TestEventEmittingProxyList:
    """Tests for _EventEmittingArtifactStoreProxy.list()."""

    @pytest.mark.asyncio
    async def test_list_delegates_to_inner_store(self) -> None:
        """Verify list() delegates to the wrapped store and returns same results."""
        from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy
        from penguiflow.planner.trajectory import Trajectory

        inner = InMemoryArtifactStore()
        await inner.put_bytes(b"data", namespace="ns1", scope=ArtifactScope(tenant_id="t1"))

        trajectory = Trajectory(query="test")
        proxy = _EventEmittingArtifactStoreProxy(
            store=inner,
            emit_event=lambda e: None,
            time_source=time.time,
            trajectory=trajectory,
        )

        result = await proxy.list(scope=ArtifactScope(tenant_id="t1"))
        inner_result = await inner.list(scope=ArtifactScope(tenant_id="t1"))
        assert len(result) == len(inner_result)
        assert result[0].id == inner_result[0].id

        # Also test with no filter
        all_result = await proxy.list()
        assert len(all_result) == 1


class TestScopeMatches:
    """Tests for _scope_matches helper function."""

    def test_none_artifact_scope_matches_all_none_filter(self) -> None:
        """None artifact scope matches all-None filter."""
        assert _scope_matches(None, ArtifactScope()) is True

    def test_none_artifact_scope_fails_non_none_filter(self) -> None:
        """None artifact scope fails when filter has non-None field."""
        assert _scope_matches(None, ArtifactScope(tenant_id="t1")) is False

    def test_exact_match(self) -> None:
        """All fields match returns True."""
        scope = ArtifactScope(tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1")
        filter_scope = ArtifactScope(tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1")
        assert _scope_matches(scope, filter_scope) is True

    def test_partial_filter_match(self) -> None:
        """Filter with one field matches artifact with that field + others."""
        scope = ArtifactScope(tenant_id="t1", user_id="u1", session_id="s1")
        filter_scope = ArtifactScope(tenant_id="t1")
        assert _scope_matches(scope, filter_scope) is True

    def test_mismatch(self) -> None:
        """Mismatched field returns False."""
        scope = ArtifactScope(tenant_id="t1")
        filter_scope = ArtifactScope(tenant_id="t2")
        assert _scope_matches(scope, filter_scope) is False

    def test_none_filter_field_matches_any(self) -> None:
        """None filter field matches any artifact value for that dimension."""
        scope = ArtifactScope(tenant_id="t1", session_id="s1")
        filter_scope = ArtifactScope(session_id="s1")  # tenant_id filter is None
        assert _scope_matches(scope, filter_scope) is True
```

## Exit Criteria (Success)
- [ ] `TestInMemoryArtifactStoreList` class exists with 8 test methods, all passing
- [ ] `TestNoOpArtifactStoreList` class exists with 1 test method, passing
- [ ] `TestPlaygroundArtifactStoreList` class exists with 2 test methods, all passing
- [ ] `TestEventEmittingProxyList` class exists with 1 test method, passing
- [ ] `TestScopeMatches` class exists with 6 test methods, all passing
- [ ] `uv run ruff check tests/test_artifacts.py` passes
- [ ] `uv run pytest tests/test_artifacts.py -x -q` passes

## Implementation Notes
- All new test classes go in the existing `tests/test_artifacts.py` file, appended after the last existing class.
- The `_StoredArtifact` import is needed for the TTL expiration test to manipulate `created_at`.
- The `_EventEmittingArtifactStoreProxy` test requires creating a `Trajectory` instance (from `penguiflow.planner.trajectory`).
- `_scope_matches` is a module-level function (not a class method), imported directly.
- The `PlaygroundArtifactStore` import comes from `penguiflow.state.in_memory`.
- These tests depend on Phase 000 (the `list` method must exist on all stores).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
uv run ruff check tests/test_artifacts.py
uv run pytest tests/test_artifacts.py -x -q -v
```
