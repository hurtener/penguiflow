"""Tests for the artifacts module."""

from __future__ import annotations

import pytest

from penguiflow.artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    ArtifactStore,
    InMemoryArtifactStore,
    NoOpArtifactStore,
    discover_artifact_store,
)


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
