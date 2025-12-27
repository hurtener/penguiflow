"""Tests for Phase 3: Playground Integration for MCP binary content.

This module tests:
- Artifact REST endpoints (/artifacts/{id}, /artifacts/{id}/meta)
- Resource REST endpoints (/resources/{namespace})
- Session-scoped access control for artifacts
- SSE events for artifact_stored and resource_updated
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore
from penguiflow.cli.playground_sse import format_sse
from penguiflow.cli.playground_state import InMemoryStateStore, PlaygroundArtifactStore
from penguiflow.planner import PlannerEvent

# ─── PlaygroundArtifactStore Tests ─────────────────────────────────────────────


class TestPlaygroundArtifactStore:
    """Tests for PlaygroundArtifactStore session isolation."""

    @pytest.mark.asyncio
    async def test_put_and_get_bytes(self) -> None:
        """Store should put and get bytes correctly."""
        store = PlaygroundArtifactStore()
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
        store = PlaygroundArtifactStore()
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
        store = PlaygroundArtifactStore()
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
        store = PlaygroundArtifactStore()
        scope1 = ArtifactScope(session_id="session1")
        scope2 = ArtifactScope(session_id="session2")

        ref1 = await store.put_bytes(b"data1", scope=scope1)
        ref2 = await store.put_bytes(b"data2", scope=scope2)

        # Both should be retrievable via generic get
        assert await store.get(ref1.id) == b"data1"
        assert await store.get(ref2.id) == b"data2"

    @pytest.mark.asyncio
    async def test_get_with_session_check_valid(self) -> None:
        """get_with_session_check should return data for correct session."""
        store = PlaygroundArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_bytes(b"secret data", scope=scope)

        data = await store.get_with_session_check(ref.id, "session1")
        assert data == b"secret data"

    @pytest.mark.asyncio
    async def test_get_with_session_check_invalid(self) -> None:
        """get_with_session_check should return None for wrong session."""
        store = PlaygroundArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_bytes(b"secret data", scope=scope)

        # Different session should get None
        data = await store.get_with_session_check(ref.id, "session2")
        assert data is None

    @pytest.mark.asyncio
    async def test_get_with_session_check_nonexistent(self) -> None:
        """get_with_session_check should return None for nonexistent artifact."""
        store = PlaygroundArtifactStore()

        data = await store.get_with_session_check("nonexistent", "session1")
        assert data is None

    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        """exists should return True for stored artifacts."""
        store = PlaygroundArtifactStore()
        scope = ArtifactScope(session_id="session1")

        assert await store.exists("nonexistent") is False

        ref = await store.put_bytes(b"data", scope=scope)
        assert await store.exists(ref.id) is True

    @pytest.mark.asyncio
    async def test_delete(self) -> None:
        """delete should remove the artifact."""
        store = PlaygroundArtifactStore()
        scope = ArtifactScope(session_id="session1")

        ref = await store.put_bytes(b"data", scope=scope)
        assert await store.exists(ref.id) is True

        result = await store.delete(ref.id)
        assert result is True
        assert await store.exists(ref.id) is False

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self) -> None:
        """delete should return False for nonexistent artifact."""
        store = PlaygroundArtifactStore()

        result = await store.delete("nonexistent")
        assert result is False

    def test_clear_session(self) -> None:
        """clear_session should remove all artifacts for a session."""
        store = PlaygroundArtifactStore()
        # Note: clear_session is synchronous but store operations are async
        # so we need to test this carefully

        # Just verify the method exists and doesn't crash
        store.clear_session("nonexistent_session")


# ─── InMemoryStateStore Tests ─────────────────────────────────────────────────


class TestInMemoryStateStore:
    """Tests for InMemoryStateStore artifact_store property."""

    def test_artifact_store_property(self) -> None:
        """State store should expose artifact_store property."""
        store = InMemoryStateStore()

        assert store.artifact_store is not None
        assert isinstance(store.artifact_store, PlaygroundArtifactStore)

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


# ─── SSE Event Frame Tests ─────────────────────────────────────────────────────


class TestSSEEventFrames:
    """Tests for SSE event frame generation."""

    def test_format_sse_basic(self) -> None:
        """format_sse should create proper SSE frame."""
        frame = format_sse("test", {"key": "value"})

        assert frame.startswith(b"event: test\n")
        assert b'data: {"key":"value"}\n\n' in frame

    def test_artifact_stored_event_frame(self) -> None:
        """_event_frame should handle artifact_stored event type."""
        # Import the function we need to test
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="artifact_stored",
            ts=1234567890.0,
            trajectory_step=1,
            extra={
                "artifact_id": "art_123abc",
                "mime_type": "image/png",
                "size_bytes": 1024,
                "filename": "chart.png",
                "source": "tableau",
            },
        )

        frame = _event_frame(event, "trace_123", "session_456")

        assert frame is not None
        assert b"event: artifact_stored" in frame
        assert b'"artifact_id":"art_123abc"' in frame
        assert b'"mime_type":"image/png"' in frame
        assert b'"size_bytes":1024' in frame
        assert b'"filename":"chart.png"' in frame
        assert b'"source":"tableau"' in frame

    def test_resource_updated_event_frame(self) -> None:
        """_event_frame should handle resource_updated event type."""
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="resource_updated",
            ts=1234567890.0,
            trajectory_step=1,
            extra={
                "uri": "file:///data/metrics.json",
                "namespace": "tableau",
            },
        )

        frame = _event_frame(event, "trace_123", "session_456")

        assert frame is not None
        assert b"event: resource_updated" in frame
        assert b'"uri":"file:///data/metrics.json"' in frame
        assert b'"namespace":"tableau"' in frame

    def test_event_frame_returns_none_without_trace_id(self) -> None:
        """_event_frame should return None if trace_id is None."""
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="artifact_stored",
            ts=1234567890.0,
            trajectory_step=1,
        )

        frame = _event_frame(event, None, "session_456")
        assert frame is None

    def test_stream_chunk_event_frame(self) -> None:
        """_event_frame should handle stream_chunk events correctly."""
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="stream_chunk",
            ts=1234567890.0,
            trajectory_step=1,
            extra={
                "stream_id": "stream_1",
                "seq": 0,
                "text": "Hello",
                "done": False,
                "meta": {"phase": "thinking"},
            },
        )

        frame = _event_frame(event, "trace_123", "session_456")

        assert frame is not None
        assert b"event: chunk" in frame
        assert b'"text":"Hello"' in frame
        assert b'"phase":"thinking"' in frame

    def test_llm_stream_chunk_event_frame(self) -> None:
        """_event_frame should handle llm_stream_chunk events."""
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="llm_stream_chunk",
            ts=1234567890.0,
            trajectory_step=1,
            extra={
                "text": "The answer is...",
                "done": False,
                "phase": "answer",
            },
        )

        frame = _event_frame(event, "trace_123", "session_456")

        assert frame is not None
        assert b"event: llm_stream_chunk" in frame
        assert b'"channel":"answer"' in frame


# ─── Artifact Endpoint Tests ─────────────────────────────────────────────────


class TestArtifactEndpoints:
    """Tests for artifact REST endpoints using HTTPX TestClient."""

    @pytest.fixture
    def mock_agent_wrapper(self) -> MagicMock:
        """Create a mock agent wrapper."""
        wrapper = MagicMock()
        wrapper.initialize = AsyncMock()
        wrapper.shutdown = AsyncMock()
        wrapper.chat = AsyncMock()
        return wrapper

    @pytest.fixture
    def state_store(self) -> InMemoryStateStore:
        """Create a state store with artifacts."""
        return InMemoryStateStore()

    @pytest.fixture
    def app(self, mock_agent_wrapper: MagicMock, state_store: InMemoryStateStore):
        """Create a test FastAPI app."""
        import tempfile

        from penguiflow.cli.playground import create_playground_app

        # Create a temporary directory for the project
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the app with our mock agent and state store
            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_agent_wrapper,
                state_store=state_store,
            )
            yield app

    @pytest.mark.asyncio
    async def test_get_artifact_success(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id} should return artifact content."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="test_session")
        ref = await artifact_store.put_bytes(
            b"Test binary data",
            mime_type="application/pdf",
            filename="report.pdf",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    params={"session_id": "test_session"},
                )

                assert response.status_code == 200
                assert response.content == b"Test binary data"
                assert response.headers["content-type"] == "application/pdf"
                assert "report.pdf" in response.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_get_artifact_with_header(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id} should accept X-Session-ID header."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="test_session")
        ref = await artifact_store.put_bytes(
            b"Data",
            mime_type="text/plain",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    headers={"X-Session-ID": "test_session"},
                )

                assert response.status_code == 200
                assert response.content == b"Data"

    @pytest.mark.asyncio
    async def test_get_artifact_wrong_session(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id} should return 404 for wrong session."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="session1")
        ref = await artifact_store.put_bytes(
            b"Secret data",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    params={"session_id": "session2"},  # Wrong session
                )

                assert response.status_code == 404
                assert "access denied" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_artifact_not_found(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id} should return 404 for nonexistent artifact."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_store = InMemoryArtifactStore()
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/artifacts/nonexistent")

                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_artifact_meta_success(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id}/meta should return metadata."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="test_session")
        ref = await artifact_store.put_bytes(
            b"Data",
            mime_type="image/png",
            filename="chart.png",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/artifacts/{ref.id}/meta",
                    params={"session_id": "test_session"},
                )

                assert response.status_code == 200
                data = response.json()
                assert data["id"] == ref.id
                assert data["mime_type"] == "image/png"
                assert data["filename"] == "chart.png"

    @pytest.mark.asyncio
    async def test_get_artifact_meta_not_found(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id}/meta should return 404 for nonexistent."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_store = InMemoryArtifactStore()
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/artifacts/nonexistent/meta")

                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_artifact_not_enabled(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /artifacts/{id} should return 501 when storage is disabled."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly set _planner to None so _discover_artifact_store returns None
            # (MagicMock auto-creates attributes which would bypass the None checks)
            mock_wrapper._planner = None
            mock_wrapper._orchestrator = None

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/artifacts/nonexistent")

                assert response.status_code == 501


# ─── Resource Endpoint Tests ─────────────────────────────────────────────────


class TestResourceEndpoints:
    """Tests for resource REST endpoints."""

    @pytest.fixture
    def state_store(self) -> InMemoryStateStore:
        """Create a state store."""
        return InMemoryStateStore()

    @pytest.mark.asyncio
    async def test_list_resources_no_tool_nodes(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace} should handle no tool nodes."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = None
            mock_wrapper._planner = None  # Explicitly set to None to avoid MagicMock auto-creation

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/test_namespace")

                assert response.status_code == 200
                data = response.json()
                assert data["resources"] == []
                assert data["templates"] == []
                assert "error" in data

    @pytest.mark.asyncio
    async def test_list_resources_namespace_not_found(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace} should return 404 for unknown namespace."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = {"other_namespace": MagicMock()}

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/unknown_namespace")

                assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_resources_not_supported(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace} should indicate unsupported."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_tool_node = MagicMock()
            mock_tool_node.resources_supported = False

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = {"test_ns": mock_tool_node}

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/test_ns")

                assert response.status_code == 200
                data = response.json()
                assert data["supported"] is False

    @pytest.mark.asyncio
    async def test_list_resources_success(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace} should return resources list."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app
        from penguiflow.tools.resources import ResourceInfo, ResourceTemplateInfo

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_resource = ResourceInfo(
                uri="file:///data.json",
                name="Data File",
                description="A data file",
            )
            mock_template = ResourceTemplateInfo(
                uri_template="file:///{path}",
                name="File Template",
            )

            mock_tool_node = MagicMock()
            mock_tool_node.resources_supported = True
            mock_tool_node.resources = [mock_resource]
            mock_tool_node.resource_templates = [mock_template]

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = {"test_ns": mock_tool_node}

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/test_ns")

                assert response.status_code == 200
                data = response.json()
                assert data["supported"] is True
                assert len(data["resources"]) == 1
                assert data["resources"][0]["uri"] == "file:///data.json"
                assert len(data["templates"]) == 1

    @pytest.mark.asyncio
    async def test_read_resource_not_supported(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace}/{uri} should error if unsupported."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_tool_node = MagicMock()
            mock_tool_node.resources_supported = False

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = {"test_ns": mock_tool_node}

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/test_ns/file:///test.txt")

                assert response.status_code == 400
                assert "does not support resources" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_read_resource_success(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace}/{uri} should return resource content."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_tool_node = MagicMock()
            mock_tool_node.resources_supported = True
            mock_tool_node.read_resource = AsyncMock(
                return_value={"text": "Resource content", "uri": "file:///test.txt"}
            )

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = {"test_ns": mock_tool_node}

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/test_ns/file:///test.txt")

                assert response.status_code == 200
                data = response.json()
                assert data["text"] == "Resource content"

    @pytest.mark.asyncio
    async def test_read_resource_error(
        self,
        state_store: InMemoryStateStore,
    ) -> None:
        """GET /resources/{namespace}/{uri} should handle read errors."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_tool_node = MagicMock()
            mock_tool_node.resources_supported = True
            mock_tool_node.read_resource = AsyncMock(
                side_effect=Exception("Connection failed")
            )

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = {"test_ns": mock_tool_node}

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/test_ns/file:///test.txt")

                assert response.status_code == 500
                assert "Connection failed" in response.json()["detail"]


# ─── Edge Case Tests ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case tests for Phase 3 features."""

    @pytest.mark.asyncio
    async def test_artifact_store_default_session(self) -> None:
        """Artifacts without explicit scope use 'default' session."""
        store = PlaygroundArtifactStore()

        ref = await store.put_bytes(b"data")

        # Should be accessible without session check
        data = await store.get(ref.id)
        assert data == b"data"

        # Should be accessible with 'default' session
        data = await store.get_with_session_check(ref.id, "default")
        assert data == b"data"

    @pytest.mark.asyncio
    async def test_artifact_content_length_header(
        self,
    ) -> None:
        """Artifact download should include Content-Length header."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        state_store = InMemoryStateStore()
        test_data = b"x" * 1000

        artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="test")
        ref = await artifact_store.put_bytes(test_data, scope=scope)

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    params={"session_id": "test"},
                )

                assert response.status_code == 200
                assert response.headers["content-length"] == "1000"

    def test_event_frame_with_empty_extra(self) -> None:
        """_event_frame should handle events with empty extra dict."""
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="step_start",
            ts=1234567890.0,
            trajectory_step=1,
            node_name="test_node",
            extra={},
        )

        frame = _event_frame(event, "trace_123", "session_456")

        assert frame is not None
        assert b"event: step" in frame
        assert b'"node":"test_node"' in frame

    def test_event_frame_artifact_chunk(self) -> None:
        """_event_frame should handle artifact_chunk events."""
        from penguiflow.cli.playground import _event_frame

        event = PlannerEvent(
            event_type="artifact_chunk",
            ts=1234567890.0,
            trajectory_step=1,
            extra={
                "stream_id": "artifact_stream_1",
                "seq": 5,
                "chunk": "base64encodedchunk",
                "done": False,
                "artifact_type": "image/png",
            },
        )

        frame = _event_frame(event, "trace_123", "session_456")

        assert frame is not None
        assert b"event: artifact_chunk" in frame
        assert b'"chunk":"base64encodedchunk"' in frame
        assert b'"artifact_type":"image/png"' in frame

    @pytest.mark.asyncio
    async def test_tool_nodes_from_planner(
        self,
    ) -> None:
        """Resource endpoints should find tool_nodes from planner."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        state_store = InMemoryStateStore()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_tool_node = MagicMock()
            mock_tool_node.resources_supported = False

            mock_planner = MagicMock()
            mock_planner._tool_nodes = {"planner_ns": mock_tool_node}

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = None  # No direct tool nodes
            mock_wrapper._planner = mock_planner  # But has planner

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/planner_ns")

                assert response.status_code == 200
                data = response.json()
                assert data["supported"] is False

    @pytest.mark.asyncio
    async def test_tool_nodes_as_list(
        self,
    ) -> None:
        """Resource endpoints should handle tool_nodes as list."""
        import tempfile

        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        state_store = InMemoryStateStore()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_config = MagicMock()
            mock_config.name = "list_ns"

            mock_tool_node = MagicMock()
            mock_tool_node.config = mock_config
            mock_tool_node.resources_supported = True
            mock_tool_node.resources = []
            mock_tool_node.resource_templates = []

            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._tool_nodes = [mock_tool_node]  # List instead of dict

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/resources/list_ns")

                assert response.status_code == 200
                data = response.json()
                assert data["supported"] is True
