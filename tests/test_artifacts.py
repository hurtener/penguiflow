"""Tests for the artifacts module."""

from __future__ import annotations

import time

import pytest

from penguiflow.artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    ArtifactStore,
    InMemoryArtifactStore,
    NoOpArtifactStore,
    ScopedArtifacts,
    _scope_matches,
    discover_artifact_store,
)
from penguiflow.state.in_memory import PlaygroundArtifactStore


class TestArtifactRef:
    """Tests for ArtifactRef model."""

    def test_minimal_ref(self) -> None:
        """Test creating a minimal ArtifactRef."""
        ref = ArtifactRef(id="test_abc123")
        assert ref.id == "test_abc123"
        assert ref.mime_type is None
        assert ref.size_bytes is None
        assert ref.filename is None
        assert ref.sha256 is None
        assert ref.scope is None
        assert ref.source == {}

    def test_full_ref(self) -> None:
        """Test creating a fully populated ArtifactRef."""
        scope = ArtifactScope(
            tenant_id="tenant1",
            user_id="user1",
            session_id="session1",
            trace_id="trace1",
        )
        ref = ArtifactRef(
            id="pdf_abc123def456",
            mime_type="application/pdf",
            size_bytes=1024,
            filename="report.pdf",
            sha256="a" * 64,
            scope=scope,
            source={"tool": "tableau.download_workbook"},
        )
        assert ref.id == "pdf_abc123def456"
        assert ref.mime_type == "application/pdf"
        assert ref.size_bytes == 1024
        assert ref.filename == "report.pdf"
        assert ref.sha256 == "a" * 64
        assert ref.scope.session_id == "session1"
        assert ref.source["tool"] == "tableau.download_workbook"

    def test_ref_serialization(self) -> None:
        """Test that ArtifactRef serializes to JSON correctly."""
        ref = ArtifactRef(
            id="test_123",
            mime_type="text/plain",
            size_bytes=100,
        )
        data = ref.model_dump()
        assert data["id"] == "test_123"
        assert data["mime_type"] == "text/plain"
        assert data["size_bytes"] == 100

        # Verify it can be reconstructed
        ref2 = ArtifactRef.model_validate(data)
        assert ref2.id == ref.id


class TestArtifactRetentionConfig:
    """Tests for ArtifactRetentionConfig model."""

    def test_defaults(self) -> None:
        """Test default retention config values."""
        config = ArtifactRetentionConfig()
        assert config.ttl_seconds == 3600
        assert config.max_artifact_bytes == 50 * 1024 * 1024
        assert config.max_session_bytes == 500 * 1024 * 1024
        assert config.max_trace_bytes == 100 * 1024 * 1024
        assert config.max_artifacts_per_trace == 100
        assert config.max_artifacts_per_session == 1000
        assert config.cleanup_strategy == "lru"

    def test_custom_config(self) -> None:
        """Test custom retention config."""
        config = ArtifactRetentionConfig(
            ttl_seconds=7200,
            max_artifact_bytes=10 * 1024 * 1024,
            cleanup_strategy="fifo",
        )
        assert config.ttl_seconds == 7200
        assert config.max_artifact_bytes == 10 * 1024 * 1024
        assert config.cleanup_strategy == "fifo"


class TestNoOpArtifactStore:
    """Tests for NoOpArtifactStore."""

    @pytest.mark.asyncio
    async def test_put_bytes_returns_truncated_ref(self) -> None:
        """Test that put_bytes returns a truncated ref with warning."""
        store = NoOpArtifactStore()
        data = b"Hello, World!"

        ref = await store.put_bytes(
            data,
            mime_type="text/plain",
            filename="test.txt",
        )

        assert ref.id.startswith("art_")
        assert ref.mime_type == "text/plain"
        assert ref.size_bytes == len(data)
        assert ref.filename == "test.txt"
        assert ref.sha256 is not None
        assert ref.source["truncated"] is True
        assert "warning" in ref.source

    @pytest.mark.asyncio
    async def test_put_text_returns_truncated_ref_with_preview(self) -> None:
        """Test that put_text returns a truncated ref with preview."""
        store = NoOpArtifactStore(max_inline_preview=10)
        text = "Hello, World! This is a longer text."

        ref = await store.put_text(
            text,
            mime_type="text/plain",
            filename="test.txt",
        )

        assert ref.id.startswith("art_")
        assert ref.source["truncated"] is True
        assert "preview" in ref.source
        assert ref.source["preview"].startswith("Hello, Wor")

    @pytest.mark.asyncio
    async def test_get_returns_none(self) -> None:
        """Test that get always returns None for NoOp store."""
        store = NoOpArtifactStore()
        result = await store.get("any_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_exists_returns_false(self) -> None:
        """Test that exists always returns False for NoOp store."""
        store = NoOpArtifactStore()
        result = await store.exists("any_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_returns_false(self) -> None:
        """Test that delete always returns False for NoOp store."""
        store = NoOpArtifactStore()
        result = await store.delete("any_id")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_ref_returns_none(self) -> None:
        """Test that get_ref always returns None for NoOp store."""
        store = NoOpArtifactStore()
        result = await store.get_ref("any_id")
        assert result is None


class TestInMemoryArtifactStore:
    """Tests for InMemoryArtifactStore."""

    @pytest.mark.asyncio
    async def test_put_and_get_bytes(self) -> None:
        """Test storing and retrieving bytes."""
        store = InMemoryArtifactStore()
        data = b"Hello, World!"

        ref = await store.put_bytes(
            data,
            mime_type="text/plain",
            filename="test.txt",
        )

        assert ref.id is not None
        assert ref.mime_type == "text/plain"
        assert ref.size_bytes == len(data)

        # Retrieve
        retrieved = await store.get(ref.id)
        assert retrieved == data

    @pytest.mark.asyncio
    async def test_put_and_get_text(self) -> None:
        """Test storing and retrieving text."""
        store = InMemoryArtifactStore()
        text = "Hello, World!"

        ref = await store.put_text(
            text,
            mime_type="text/plain",
            filename="test.txt",
        )

        assert ref.id is not None
        assert ref.mime_type == "text/plain"

        # Retrieve as bytes
        retrieved = await store.get(ref.id)
        assert retrieved == text.encode("utf-8")

    @pytest.mark.asyncio
    async def test_get_ref(self) -> None:
        """Test retrieving artifact metadata."""
        store = InMemoryArtifactStore()
        data = b"Test data"

        ref = await store.put_bytes(
            data,
            mime_type="application/octet-stream",
            filename="data.bin",
            meta={"source": "test"},
        )

        retrieved_ref = await store.get_ref(ref.id)
        assert retrieved_ref is not None
        assert retrieved_ref.id == ref.id
        assert retrieved_ref.mime_type == "application/octet-stream"
        assert retrieved_ref.filename == "data.bin"

    @pytest.mark.asyncio
    async def test_deduplication(self) -> None:
        """Test that identical content is deduplicated."""
        store = InMemoryArtifactStore()
        data = b"Duplicate content"

        ref1 = await store.put_bytes(data, mime_type="text/plain")
        ref2 = await store.put_bytes(data, mime_type="text/plain")

        # Same content should produce same ID
        assert ref1.id == ref2.id
        # Only one artifact should be stored
        assert store.count == 1

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        """Test deleting an artifact."""
        store = InMemoryArtifactStore()
        data = b"To be deleted"

        ref = await store.put_bytes(data)

        assert await store.exists(ref.id) is True
        assert await store.delete(ref.id) is True
        assert await store.exists(ref.id) is False
        assert await store.get(ref.id) is None

    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        """Test checking artifact existence."""
        store = InMemoryArtifactStore()

        assert await store.exists("nonexistent") is False

        ref = await store.put_bytes(b"data")
        assert await store.exists(ref.id) is True

    @pytest.mark.asyncio
    async def test_size_limit_enforcement(self) -> None:
        """Test that artifacts exceeding size limit are rejected."""
        config = ArtifactRetentionConfig(max_artifact_bytes=100)
        store = InMemoryArtifactStore(retention=config)

        # Should succeed
        await store.put_bytes(b"x" * 50)

        # Should fail
        with pytest.raises(ValueError, match="exceeds limit"):
            await store.put_bytes(b"x" * 200)

    @pytest.mark.asyncio
    async def test_count_limit_eviction(self) -> None:
        """Test that artifacts are evicted when count limit is reached."""
        config = ArtifactRetentionConfig(max_artifacts_per_session=3)
        store = InMemoryArtifactStore(retention=config)

        # Store 3 artifacts
        ref1 = await store.put_bytes(b"data1", namespace="ns1")
        ref2 = await store.put_bytes(b"data2", namespace="ns2")
        ref3 = await store.put_bytes(b"data3", namespace="ns3")

        assert store.count == 3

        # Store a 4th - should evict the oldest (ref1)
        await store.put_bytes(b"data4", namespace="ns4")

        assert store.count == 3
        assert await store.exists(ref1.id) is False
        assert await store.exists(ref2.id) is True
        assert await store.exists(ref3.id) is True

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        """Test clearing all artifacts."""
        store = InMemoryArtifactStore()

        await store.put_bytes(b"data1")
        await store.put_bytes(b"data2")
        await store.put_bytes(b"data3")

        assert store.count == 3
        assert store.total_bytes > 0

        store.clear()

        assert store.count == 0
        assert store.total_bytes == 0

    @pytest.mark.asyncio
    async def test_namespace_in_id(self) -> None:
        """Test that namespace is included in artifact ID."""
        store = InMemoryArtifactStore()

        ref = await store.put_bytes(b"data", namespace="tableau")

        assert ref.id.startswith("tableau_")

    @pytest.mark.asyncio
    async def test_scope_assignment(self) -> None:
        """Test that scope is correctly assigned to artifacts."""
        scope = ArtifactScope(session_id="session123", trace_id="trace456")
        store = InMemoryArtifactStore()

        ref = await store.put_bytes(b"data", scope=scope)

        assert ref.scope is not None
        assert ref.scope.session_id == "session123"
        assert ref.scope.trace_id == "trace456"

    @pytest.mark.asyncio
    async def test_scope_filter(self) -> None:
        """Test that scope_filter is applied to all artifacts."""
        scope = ArtifactScope(session_id="default_session")
        store = InMemoryArtifactStore(scope_filter=scope)

        ref = await store.put_bytes(b"data")

        assert ref.scope is not None
        assert ref.scope.session_id == "default_session"


class TestDiscoverArtifactStore:
    """Tests for discover_artifact_store function."""

    def test_discover_from_attribute(self) -> None:
        """Test discovering artifact store from attribute."""

        class MockStateStore:
            artifact_store = InMemoryArtifactStore()

        state_store = MockStateStore()
        discovered = discover_artifact_store(state_store)

        assert discovered is not None
        assert isinstance(discovered, InMemoryArtifactStore)

    def test_discover_from_protocol_implementation(self) -> None:
        """Test discovering artifact store when object implements protocol."""
        store = InMemoryArtifactStore()
        discovered = discover_artifact_store(store)

        assert discovered is store

    def test_discover_returns_none_for_incompatible(self) -> None:
        """Test that discover returns None for incompatible objects."""

        class IncompatibleStore:
            pass

        discovered = discover_artifact_store(IncompatibleStore())
        assert discovered is None

    def test_discover_returns_none_for_none(self) -> None:
        """Test that discover handles None gracefully."""
        discovered = discover_artifact_store(None)
        assert discovered is None


class TestArtifactStoreProtocol:
    """Tests to verify protocol compliance."""

    def test_in_memory_implements_protocol(self) -> None:
        """Test that InMemoryArtifactStore implements ArtifactStore."""
        store = InMemoryArtifactStore()
        assert isinstance(store, ArtifactStore)

    def test_noop_implements_protocol(self) -> None:
        """Test that NoOpArtifactStore implements ArtifactStore."""
        store = NoOpArtifactStore()
        assert isinstance(store, ArtifactStore)


class TestInMemoryArtifactStoreEviction:
    """Tests for eviction and TTL behavior."""

    @pytest.mark.asyncio
    async def test_ttl_zero_skips_expiration(self) -> None:
        """Test that TTL of 0 disables expiration checks (line 531)."""
        config = ArtifactRetentionConfig(ttl_seconds=0)
        store = InMemoryArtifactStore(retention=config)

        ref = await store.put_bytes(b"data")
        # Even with TTL=0, the artifact should persist
        assert await store.exists(ref.id) is True
        # Calling get triggers _expire_old_artifacts which should return early
        data = await store.get(ref.id)
        assert data == b"data"

    @pytest.mark.asyncio
    async def test_cleanup_strategy_none_skips_eviction(self) -> None:
        """Test that cleanup_strategy='none' prevents eviction (line 546)."""
        config = ArtifactRetentionConfig(
            max_artifacts_per_session=2,
            cleanup_strategy="none",
        )
        store = InMemoryArtifactStore(retention=config)

        # Store 2 artifacts (at limit)
        ref1 = await store.put_bytes(b"data1", namespace="ns1")
        ref2 = await store.put_bytes(b"data2", namespace="ns2")
        assert store.count == 2

        # Store a 3rd - with cleanup_strategy='none', eviction is skipped
        # so the artifact is still added (exceeds limit)
        ref3 = await store.put_bytes(b"data3", namespace="ns3")
        assert store.count == 3
        assert await store.exists(ref1.id) is True
        assert await store.exists(ref2.id) is True
        assert await store.exists(ref3.id) is True

    @pytest.mark.asyncio
    async def test_fifo_eviction_strategy(self) -> None:
        """Test FIFO eviction strategy removes oldest first (lines 554-556)."""
        config = ArtifactRetentionConfig(
            max_artifacts_per_session=2,
            cleanup_strategy="fifo",
        )
        store = InMemoryArtifactStore(retention=config)

        # Store 2 artifacts (at limit)
        ref1 = await store.put_bytes(b"first", namespace="ns1")
        ref2 = await store.put_bytes(b"second", namespace="ns2")
        assert store.count == 2

        # Store a 3rd - should evict first-in (ref1)
        ref3 = await store.put_bytes(b"third", namespace="ns3")

        assert store.count == 2
        assert await store.exists(ref1.id) is False  # Evicted (first in)
        assert await store.exists(ref2.id) is True
        assert await store.exists(ref3.id) is True

    @pytest.mark.asyncio
    async def test_size_based_eviction(self) -> None:
        """Test eviction when total session bytes limit is exceeded."""
        config = ArtifactRetentionConfig(
            max_session_bytes=100,  # 100 bytes total limit
            max_artifacts_per_session=100,  # High count limit
        )
        store = InMemoryArtifactStore(retention=config)

        # Store first artifact (50 bytes)
        ref1 = await store.put_bytes(b"x" * 50, namespace="ns1")
        assert store.total_bytes == 50

        # Store second artifact (40 bytes) - still under limit
        ref2 = await store.put_bytes(b"y" * 40, namespace="ns2")
        assert store.total_bytes == 90

        # Store third artifact (30 bytes) - would exceed 100, triggers eviction
        ref3 = await store.put_bytes(b"z" * 30, namespace="ns3")

        # First artifact should be evicted to make room
        assert await store.exists(ref1.id) is False
        assert await store.exists(ref2.id) is True
        assert await store.exists(ref3.id) is True
        assert store.total_bytes <= 100

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self) -> None:
        """Test that getting a non-existent artifact returns None."""
        store = InMemoryArtifactStore()
        result = await store.get("nonexistent_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_ref_nonexistent_returns_none(self) -> None:
        """Test that getting ref of non-existent artifact returns None."""
        store = InMemoryArtifactStore()
        result = await store.get_ref("nonexistent_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self) -> None:
        """Test that deleting a non-existent artifact returns False."""
        store = InMemoryArtifactStore()
        result = await store.delete("nonexistent_id")
        assert result is False


class TestNoOpArtifactStoreWarning:
    """Tests for NoOpArtifactStore warning behavior."""

    @pytest.mark.asyncio
    async def test_warning_only_once(self) -> None:
        """Test that warning is only logged once per store instance."""
        store = NoOpArtifactStore()

        # First put_bytes should set _warned = True
        ref1 = await store.put_bytes(b"data1")
        assert store._warned is True

        # Second put_bytes should not log warning again
        ref2 = await store.put_bytes(b"data2")
        assert ref1.source["warning"] is not None
        assert ref2.source["warning"] is not None

    @pytest.mark.asyncio
    async def test_put_text_warning(self) -> None:
        """Test that put_text also triggers warning on first call."""
        store = NoOpArtifactStore()

        # First put_text should set _warned = True
        ref = await store.put_text("test text")
        assert store._warned is True
        assert ref.source["truncated"] is True

    @pytest.mark.asyncio
    async def test_put_bytes_with_namespace_and_scope(self) -> None:
        """Test put_bytes with all optional parameters."""
        store = NoOpArtifactStore()
        scope = ArtifactScope(session_id="sess1")

        ref = await store.put_bytes(
            b"data",
            mime_type="application/octet-stream",
            filename="file.bin",
            namespace="myns",
            scope=scope,
            meta={"key": "value"},
        )

        assert ref.id.startswith("myns_")
        assert ref.scope == scope
        assert ref.source["key"] == "value"

    @pytest.mark.asyncio
    async def test_put_text_with_namespace_and_scope(self) -> None:
        """Test put_text with all optional parameters."""
        store = NoOpArtifactStore()
        scope = ArtifactScope(session_id="sess1")

        ref = await store.put_text(
            "hello world",
            mime_type="text/plain",
            filename="file.txt",
            namespace="myns",
            scope=scope,
            meta={"key": "value"},
        )

        assert ref.id.startswith("myns_")
        assert ref.scope == scope
        assert ref.source["key"] == "value"


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


class TestScopedArtifactsImmutability:
    """Tests for ScopedArtifacts immutability."""

    def _make_facade(self, **kwargs) -> ScopedArtifacts:
        defaults = dict(tenant_id=None, user_id=None, session_id=None, trace_id=None)
        defaults.update(kwargs)
        return ScopedArtifacts(InMemoryArtifactStore(), **defaults)

    def test_cannot_reassign_store(self) -> None:
        """Reassigning _store raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError, match="immutable"):
            facade._store = InMemoryArtifactStore()

    def test_cannot_reassign_scope(self) -> None:
        """Reassigning _scope raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError, match="immutable"):
            facade._scope = ArtifactScope()

    def test_cannot_reassign_read_scope(self) -> None:
        """Reassigning _read_scope raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError, match="immutable"):
            facade._read_scope = ArtifactScope()

    def test_cannot_add_new_attribute(self) -> None:
        """Adding a new attribute raises AttributeError."""
        facade = self._make_facade()
        with pytest.raises(AttributeError):
            facade.foo = "bar"

    def test_scope_property_returns_correct_value(self) -> None:
        """scope property returns the ArtifactScope passed at construction."""
        facade = self._make_facade(tenant_id="t1", session_id="s1", trace_id="tr1")
        assert facade.scope.tenant_id == "t1"
        assert facade.scope.session_id == "s1"
        assert facade.scope.trace_id == "tr1"
        assert facade.scope.user_id is None


class TestScopedArtifactsUpload:
    """Tests for ScopedArtifacts.upload()."""

    @pytest.mark.asyncio
    async def test_upload_bytes(self) -> None:
        """upload(bytes) stores via put_bytes and returns ArtifactRef with full scope."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"pdf data", mime_type="application/pdf", filename="report.pdf")

        assert ref.mime_type == "application/pdf"
        assert ref.filename == "report.pdf"
        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        assert ref.scope.trace_id == "tr1"
        # Verify data was actually stored
        data = await store.get(ref.id)
        assert data == b"pdf data"

    @pytest.mark.asyncio
    async def test_upload_str(self) -> None:
        """upload(str) stores via put_text, defaults mime_type to text/plain."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id=None
        )
        ref = await facade.upload("hello world")

        assert ref.mime_type == "text/plain"
        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        data = await store.get(ref.id)
        assert data == b"hello world"

    @pytest.mark.asyncio
    async def test_upload_str_custom_mime(self) -> None:
        """upload(str, mime_type=...) respects the provided mime type."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        ref = await facade.upload("col1,col2\n1,2", mime_type="text/csv")
        assert ref.mime_type == "text/csv"

    @pytest.mark.asyncio
    async def test_upload_passes_namespace(self) -> None:
        """upload(bytes, namespace=...) forwards namespace to the store."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        ref = await facade.upload(b"data", namespace="my_tool")
        assert ref.id.startswith("my_tool_")

    @pytest.mark.asyncio
    async def test_upload_passes_meta(self) -> None:
        """upload(bytes, meta=...) forwards meta to the store."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        ref = await facade.upload(b"data", meta={"key": "val"})
        assert ref.source.get("key") == "val"

    @pytest.mark.asyncio
    async def test_upload_injects_full_scope(self) -> None:
        """Upload injects full scope including trace_id."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"data")

        assert ref.scope is not None
        assert ref.scope.tenant_id == "t1"
        assert ref.scope.user_id == "u1"
        assert ref.scope.session_id == "s1"
        assert ref.scope.trace_id == "tr1"


class TestScopedArtifactsDownload:
    """Tests for ScopedArtifacts.download()."""

    @pytest.mark.asyncio
    async def test_download_same_scope(self) -> None:
        """Upload via facade, then download returns the bytes."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"secret data")
        result = await facade.download(ref.id)
        assert result == b"secret data"

    @pytest.mark.asyncio
    async def test_download_different_trace_same_session(self) -> None:
        """Different trace_id but same tenant/user/session: download succeeds."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        result = await facade2.download(ref.id)
        assert result == b"data"

    @pytest.mark.asyncio
    async def test_download_different_tenant_denied(self) -> None:
        """Different tenant_id: download returns None."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id=None, trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t2", user_id=None, session_id=None, trace_id=None
        )
        result = await facade2.download(ref.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_download_different_session_denied(self) -> None:
        """Different session_id: download returns None."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id="s1", trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id="s2", trace_id=None
        )
        result = await facade2.download(ref.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_download_unscoped_artifact_allowed(self) -> None:
        """Artifacts with scope=None are accessible to any facade."""
        store = InMemoryArtifactStore()
        # Store directly with no scope
        ref = await store.put_bytes(b"open data", namespace="ns1")
        assert ref.scope is None

        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        result = await facade.download(ref.id)
        assert result == b"open data"

    @pytest.mark.asyncio
    async def test_download_nonexistent_returns_none(self) -> None:
        """download() for nonexistent ID returns None."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        result = await facade.download("nonexistent_id")
        assert result is None


class TestScopedArtifactsGetMetadata:
    """Tests for ScopedArtifacts.get_metadata()."""

    @pytest.mark.asyncio
    async def test_get_metadata_returns_ref(self) -> None:
        """Upload, then get_metadata returns the ArtifactRef with correct fields."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id="s1", trace_id="tr1"
        )
        ref = await facade.upload(b"data", mime_type="application/pdf", filename="doc.pdf")

        metadata = await facade.get_metadata(ref.id)
        assert metadata is not None
        assert metadata.id == ref.id
        assert metadata.mime_type == "application/pdf"
        assert metadata.filename == "doc.pdf"
        assert metadata.scope is not None
        assert metadata.scope.tenant_id == "t1"

    @pytest.mark.asyncio
    async def test_get_metadata_different_trace_allowed(self) -> None:
        """Different trace, same tenant/user/session: returns ref."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr1"
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t1", user_id="u1", session_id="s1", trace_id="tr2"
        )
        metadata = await facade2.get_metadata(ref.id)
        assert metadata is not None
        assert metadata.id == ref.id

    @pytest.mark.asyncio
    async def test_get_metadata_different_tenant_denied(self) -> None:
        """Different tenant_id: returns None."""
        store = InMemoryArtifactStore()
        facade1 = ScopedArtifacts(
            store, tenant_id="t1", user_id=None, session_id=None, trace_id=None
        )
        ref = await facade1.upload(b"data")

        facade2 = ScopedArtifacts(
            store, tenant_id="t2", user_id=None, session_id=None, trace_id=None
        )
        metadata = await facade2.get_metadata(ref.id)
        assert metadata is None

    @pytest.mark.asyncio
    async def test_get_metadata_nonexistent_returns_none(self) -> None:
        """get_metadata() for unknown ID returns None."""
        store = InMemoryArtifactStore()
        facade = ScopedArtifacts(
            store, tenant_id=None, user_id=None, session_id=None, trace_id=None
        )
        result = await facade.get_metadata("nonexistent_id")
        assert result is None


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
