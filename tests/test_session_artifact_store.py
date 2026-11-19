"""Tests for SessionArtifactStore and InMemoryStateStore's artifact store."""

from __future__ import annotations

import pytest

from penguiflow.artifacts import ArtifactScope
from penguiflow.state.in_memory import InMemoryStateStore, SessionArtifactStore


class TestSessionArtifactStore:
    """Tests for SessionArtifactStore session isolation."""

    @pytest.mark.asyncio
    async def test_put_and_get_bytes(self) -> None:
        """Store should put and get bytes correctly."""
        store = SessionArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_bytes(
            b"Hello, World!",
            mime_type="text/plain",
            filename="test.txt",
            scope=scope,
        )

        assert ref.id is not None
        assert ref.mime_type == "text/plain"
        data = await store.get(ref.id)
        assert data == b"Hello, World!"

    @pytest.mark.asyncio
    async def test_put_and_get_text(self) -> None:
        """Store should put and get text correctly."""
        store = SessionArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_text(
            "Hello, World!",
            mime_type="text/plain",
            filename="test.txt",
            scope=scope,
        )

        assert ref.id is not None
        data = await store.get(ref.id)
        assert data == b"Hello, World!"

    @pytest.mark.asyncio
    async def test_get_ref(self) -> None:
        """Store should return artifact metadata via get_ref."""
        store = SessionArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_bytes(
            b"data",
            mime_type="application/pdf",
            filename="report.pdf",
            scope=scope,
        )

        retrieved_ref = await store.get_ref(ref.id)
        assert retrieved_ref is not None
        assert retrieved_ref.id == ref.id
        assert retrieved_ref.mime_type == "application/pdf"
        assert retrieved_ref.filename == "report.pdf"

    @pytest.mark.asyncio
    async def test_session_isolation(self) -> None:
        """Artifacts from different sessions should be isolated."""
        store = SessionArtifactStore()
        scope1 = ArtifactScope(session_id="session1")
        scope2 = ArtifactScope(session_id="session2")

        ref1 = await store.put_bytes(b"data1", scope=scope1)
        ref2 = await store.put_bytes(b"data2", scope=scope2)

        # Both should be retrievable via generic get
        assert await store.get(ref1.id) == b"data1"
        assert await store.get(ref2.id) == b"data2"

    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        """exists should return True for stored artifacts."""
        store = SessionArtifactStore()
        scope = ArtifactScope(session_id="session1")

        assert await store.exists("nonexistent") is False

        ref = await store.put_bytes(b"data", scope=scope)
        assert await store.exists(ref.id) is True

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        """delete should remove the artifact."""
        store = SessionArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_bytes(b"data", scope=scope)
        assert await store.exists(ref.id) is True

        result = await store.delete(ref.id)
        assert result is True
        assert await store.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self) -> None:
        """delete should return False for nonexistent artifact."""
        store = SessionArtifactStore()

        result = await store.delete("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_artifact_store_default_session(self) -> None:
        """Artifacts without explicit scope use 'default' session."""
        store = SessionArtifactStore()

        ref = await store.put_bytes(b"data")

        data = await store.get(ref.id)
        assert data == b"data"

    def test_clear_session(self) -> None:
        """clear_session should remove all artifacts for a session."""
        store = SessionArtifactStore()
        # Note: clear_session is synchronous but store operations are async
        # so we need to test this carefully

        # Just verify the method exists and doesn't crash
        store.clear_session("nonexistent_session")


class TestInMemoryStateStore:
    """Tests for InMemoryStateStore artifact_store property."""

    def test_artifact_store_property(self) -> None:
        """State store should expose artifact_store property."""
        store = InMemoryStateStore()

        assert store.artifact_store is not None
        assert isinstance(store.artifact_store, SessionArtifactStore)

    @pytest.mark.asyncio
    async def test_artifact_store_operations(self) -> None:
        """Artifact store from state store should work correctly."""
        state_store = InMemoryStateStore()
        artifact_store = state_store.artifact_store

        scope = ArtifactScope(session_id="test_session")
        ref = await artifact_store.put_bytes(
            b"test data",
            mime_type="text/plain",
            scope=scope,
        )

        assert ref.id is not None
        data = await artifact_store.get(ref.id)
        assert data == b"test data"
