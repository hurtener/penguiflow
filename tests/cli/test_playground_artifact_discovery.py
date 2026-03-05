"""Tests for _discover_artifact_store() fallback to state store.

The _discover_artifact_store() function is a closure inside create_playground_app
and cannot be called directly. All tests exercise it indirectly through the
artifact HTTP endpoints using AsyncClient with ASGITransport.

A 501 response means discovery returned None; a 200 (or 404) means discovery
found a valid store.
"""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore, NoOpArtifactStore
from penguiflow.cli.playground import create_playground_app
from penguiflow.cli.playground_state import InMemoryStateStore


class TestArtifactDiscoveryFallback:
    """Verify _discover_artifact_store() falls back to the state store."""

    @pytest.mark.asyncio
    async def test_discover_artifact_store_falls_back_to_state_store(self) -> None:
        """When the planner is not discoverable, the state store's artifact store is used."""
        state_store = InMemoryStateStore()
        scope = ArtifactScope(session_id="test-session")
        ref = await state_store.artifact_store.put_bytes(
            b"fallback artifact data",
            mime_type="application/octet-stream",
            filename="fallback.bin",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly set _planner and _orchestrator to None so MagicMock
            # auto-created attributes don't bypass the None checks in
            # _discover_planner().
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
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    params={"session_id": "test-session"},
                )

                assert response.status_code == 200
                assert response.content == b"fallback artifact data"
                assert response.headers["content-type"] == "application/octet-stream"
                assert "fallback.bin" in response.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_discover_artifact_store_prefers_planner(self) -> None:
        """When both planner and state store have artifact stores, planner wins."""
        # Create a separate artifact store for the planner
        planner_artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="test-session")
        ref = await planner_artifact_store.put_bytes(
            b"planner artifact data",
            mime_type="text/plain",
            filename="planner.txt",
            scope=scope,
        )

        # State store has its own artifact store (different data)
        state_store = InMemoryStateStore()
        await state_store.artifact_store.put_bytes(
            b"state store artifact data",
            mime_type="text/plain",
            filename="state.txt",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            mock_wrapper._planner = MagicMock(artifact_store=planner_artifact_store)

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=state_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                # The planner's artifact should be found
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    params={"session_id": "test-session"},
                )

                assert response.status_code == 200
                assert response.content == b"planner artifact data"

    @pytest.mark.asyncio
    async def test_discover_artifact_store_skips_noop(self) -> None:
        """When the state store's artifact store is NoOp, discovery returns None (501)."""
        mock_store = MagicMock()
        mock_store.artifact_store = NoOpArtifactStore()
        # Remove session persistence attributes so SessionManager doesn't try to use the mock
        del mock_store.save_task
        del mock_store.save_event
        del mock_store.load_history

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly set _planner and _orchestrator to None so the planner
            # path returns None and the fallback is exercised.
            mock_wrapper._planner = None
            mock_wrapper._orchestrator = None

            app = create_playground_app(
                project_root=tmpdir,
                agent=mock_wrapper,
                state_store=mock_store,
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get("/artifacts/nonexistent")

                assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_discover_artifact_store_list_endpoint_uses_fallback(self) -> None:
        """GET /artifacts list endpoint finds artifacts via state store fallback."""
        state_store = InMemoryStateStore()
        scope = ArtifactScope(session_id="test-session")
        ref = await state_store.artifact_store.put_bytes(
            b"listed artifact data",
            mime_type="text/plain",
            filename="listed.txt",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly set _planner and _orchestrator to None so the planner
            # path returns None and the fallback is exercised.
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
                response = await client.get(
                    "/artifacts",
                    params={"session_id": "test-session"},
                )

                assert response.status_code == 200
                data = response.json()
                assert len(data) > 0
                # Verify the artifact we stored is in the list
                artifact_ids = [a["id"] for a in data]
                assert ref.id in artifact_ids
