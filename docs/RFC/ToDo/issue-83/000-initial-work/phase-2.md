# Phase 002: Add unit tests for artifact discovery fallback behavior

## Objective
Add a new test file `tests/cli/test_playground_artifact_discovery.py` with 4 test cases that verify the `_discover_artifact_store()` fallback behavior introduced in Phase 0. The tests exercise the function indirectly through the artifact HTTP endpoints because `_discover_artifact_store()` is a closure inside `create_playground_app` and cannot be called directly. This phase depends on both Phase 0 (the fallback logic) and Phase 1 (the `list` method on `_ScopedArtifactStore`/`_DisabledArtifactStore`, needed by the list endpoint test).

## Tasks
1. Create the test file with proper imports and class structure.
2. Implement 4 test cases covering: state store fallback, planner preference, NoOp skipping, and list endpoint fallback.

## Detailed Steps

### Step 1: Create `tests/cli/test_playground_artifact_discovery.py`
- The `tests/cli/` directory already exists and contains other playground test files.
- Follow the import and fixture patterns from `tests/test_playground_phase3.py:348-380`.

### Step 2: Implement the test class with 4 test cases

All tests use `AsyncClient` with `ASGITransport` to make HTTP requests to the app. This matches the existing artifact test patterns in `tests/test_playground_phase3.py:382-664` and naturally supports async setup (e.g., `await store.artifact_store.put_bytes(...)`) without `asyncio.run()` workarounds.

**Critical -- MagicMock auto-creates attributes.** `MagicMock()` auto-creates any attribute on access, which means `getattr(mock, "_planner")` returns a new `MagicMock` instead of `None`. Worse, a MagicMock instance passes `isinstance(x, ArtifactStore)` because `@runtime_checkable` Protocol only checks attribute existence and MagicMock satisfies that. This means `_discover_planner()` and `_discover_artifact_store()` will find fake planner/artifact stores from auto-created attributes, silently bypassing the state store fallback path. **For any test that needs `_discover_planner()` to return `None`, you MUST explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`.** See `tests/test_playground_phase3.py:646-649` for the existing pattern.

**Important -- artifact scoping in tests.** `InMemoryStateStore.artifact_store` returns a `PlaygroundArtifactStore` (a session-scoped facade wrapping `InMemoryArtifactStore`). When calling `put_bytes`, you **must** pass `scope=ArtifactScope(session_id="test-session")` so the artifact is discoverable. The `GET` request must use the **same** `session_id` (e.g., `?session_id=test-session`). Without matching scopes, the `list` and `get` calls will return empty/None even though the artifact was stored.

**Imports to use:**
- `InMemoryStateStore` from `penguiflow.cli.playground_state` (re-exports `penguiflow.state.in_memory.InMemoryStateStore`; has `.artifact_store` property). Use this import path to match existing playground tests.
- `InMemoryArtifactStore`, `NoOpArtifactStore`, `ArtifactScope` from `penguiflow.artifacts`
- `MagicMock`, `AsyncMock` from `unittest.mock`
- `create_playground_app` from `penguiflow.cli.playground`
- `AsyncClient`, `ASGITransport` from `httpx`

### Step 3: Test case details

#### Test 1: `test_discover_artifact_store_falls_back_to_state_store`
- **Purpose:** When the agent wrapper has no discoverable planner (orchestrator-based agent), but the custom state store has a valid `artifact_store`, the artifact endpoints should return 200 (not 501).
- **Setup:**
  1. Create an `InMemoryStateStore`.
  2. Store an artifact: `ref = await state_store.artifact_store.put_bytes(b"fallback data", mime_type="text/plain", scope=ArtifactScope(session_id="test-session"))`.
  3. Create `mock_wrapper = MagicMock()` with `initialize = AsyncMock()`, `shutdown = AsyncMock()`.
  4. **Explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`** to prevent MagicMock auto-attributes.
  5. Create app: `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=state_store)`.
- **Request:** `GET /artifacts/{ref.id}?session_id=test-session`
- **Assert:** `response.status_code == 200` and `response.content == b"fallback data"`.

#### Test 2: `test_discover_artifact_store_prefers_planner`
- **Purpose:** When both the planner and the state store have artifact stores, the planner's store should be used (existing behavior preserved).
- **Setup:**
  1. Create an `InMemoryArtifactStore()` for the planner.
  2. Store an artifact in the planner store: `ref = await planner_artifact_store.put_bytes(b"planner data", mime_type="text/plain", scope=ArtifactScope(session_id="test-session"))`.
  3. Create `mock_wrapper = MagicMock()` with `initialize = AsyncMock()`, `shutdown = AsyncMock()`.
  4. Set `mock_wrapper._planner = MagicMock(artifact_store=planner_artifact_store)`.
  5. Create a separate `InMemoryStateStore` (which has its own artifact store).
  6. Create app: `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=state_store)`.
- **Request:** `GET /artifacts/{ref.id}?session_id=test-session`
- **Assert:** `response.status_code == 200` and `response.content == b"planner data"`.

#### Test 3: `test_discover_artifact_store_skips_noop`
- **Purpose:** When the state store's artifact store is a `NoOpArtifactStore`, the fallback should return `None` (no false positives).
- **Setup:**
  1. Create a mock state store: `mock_store = MagicMock()` with `mock_store.artifact_store = NoOpArtifactStore()`.
  2. Create `mock_wrapper = MagicMock()` with `initialize = AsyncMock()`, `shutdown = AsyncMock()`.
  3. **Explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`** so the planner path returns None and the fallback is exercised.
  4. Create app: `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=mock_store)`.
- **Request:** `GET /artifacts/some-id`
- **Assert:** `response.status_code == 501`.

#### Test 4: `test_discover_artifact_store_list_endpoint_uses_fallback`
- **Purpose:** When the agent wrapper has no discoverable planner but the custom state store has a valid artifact store with stored artifacts, the `GET /artifacts?session_id=test-session` endpoint should return a non-empty list (not `[]`).
- **Setup:**
  1. Create an `InMemoryStateStore`.
  2. Store an artifact: `await state_store.artifact_store.put_bytes(b"list test", mime_type="text/plain", scope=ArtifactScope(session_id="test-session"))`.
  3. Create `mock_wrapper = MagicMock()` with `initialize = AsyncMock()`, `shutdown = AsyncMock()`.
  4. **Explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`**.
  5. Create app: `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=state_store)`.
- **Request:** `GET /artifacts?session_id=test-session`
- **Assert:** `response.status_code == 200` and `len(response.json()) > 0`.
- **Note:** The `/artifacts` list endpoint returns `[]` (not 501) when discovery fails -- a subtler failure mode that could silently hide a regression. That is why this test specifically checks for a non-empty list.

## Required Code

```python
# Target file: tests/cli/test_playground_artifact_discovery.py

"""Tests for _discover_artifact_store() fallback behavior.

When a custom state store is passed to create_playground_app(), the
artifact discovery function should fall back to the state store's
artifact store when planner-based discovery fails.

Tests exercise _discover_artifact_store() indirectly through HTTP
endpoints because it is a closure inside create_playground_app.
"""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore, NoOpArtifactStore
from penguiflow.cli.playground_state import InMemoryStateStore


class TestArtifactDiscoveryFallback:
    """Tests for _discover_artifact_store() state store fallback."""

    @pytest.mark.asyncio
    async def test_discover_artifact_store_falls_back_to_state_store(self) -> None:
        """When the planner is not discoverable, fall back to state store's artifact store."""
        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        state_store = InMemoryStateStore()
        ref = await state_store.artifact_store.put_bytes(
            b"fallback data",
            mime_type="text/plain",
            scope=ArtifactScope(session_id="test-session"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly nullify so MagicMock auto-created attributes
            # don't bypass the planner discovery checks.
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
                assert response.content == b"fallback data"

    @pytest.mark.asyncio
    async def test_discover_artifact_store_prefers_planner(self) -> None:
        """When both planner and state store have artifact stores, prefer the planner's."""
        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        planner_artifact_store = InMemoryArtifactStore()
        ref = await planner_artifact_store.put_bytes(
            b"planner data",
            mime_type="text/plain",
            scope=ArtifactScope(session_id="test-session"),
        )

        state_store = InMemoryStateStore()

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
                response = await client.get(
                    f"/artifacts/{ref.id}",
                    params={"session_id": "test-session"},
                )

                assert response.status_code == 200
                assert response.content == b"planner data"

    @pytest.mark.asyncio
    async def test_discover_artifact_store_skips_noop(self) -> None:
        """When the state store's artifact store is NoOp, discovery should return None."""
        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        mock_store = MagicMock()
        mock_store.artifact_store = NoOpArtifactStore()

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly nullify so the planner path returns None
            # and the state store fallback is actually exercised.
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
                response = await client.get("/artifacts/some-id")

                assert response.status_code == 501

    @pytest.mark.asyncio
    async def test_discover_artifact_store_list_endpoint_uses_fallback(self) -> None:
        """The /artifacts list endpoint should use the state store fallback."""
        from httpx import ASGITransport, AsyncClient

        from penguiflow.cli.playground import create_playground_app

        state_store = InMemoryStateStore()
        await state_store.artifact_store.put_bytes(
            b"list test",
            mime_type="text/plain",
            scope=ArtifactScope(session_id="test-session"),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
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
```

## Exit Criteria (Success)
- [ ] File `tests/cli/test_playground_artifact_discovery.py` exists.
- [ ] The file contains a `TestArtifactDiscoveryFallback` class with 4 test methods.
- [ ] `test_discover_artifact_store_falls_back_to_state_store` passes -- verifies 200 response when planner is absent but state store has artifacts.
- [ ] `test_discover_artifact_store_prefers_planner` passes -- verifies planner's artifact store is used when both are available.
- [ ] `test_discover_artifact_store_skips_noop` passes -- verifies 501 response when state store has `NoOpArtifactStore`.
- [ ] `test_discover_artifact_store_list_endpoint_uses_fallback` passes -- verifies non-empty list from `/artifacts` endpoint when using state store fallback.
- [ ] All 4 tests pass: `uv run pytest tests/cli/test_playground_artifact_discovery.py -v`.
- [ ] No import errors or syntax errors.
- [ ] `uv run ruff check tests/cli/test_playground_artifact_discovery.py` passes.
- [ ] Full test suite still passes: `uv run pytest tests/ -k "playground" -x -q`.

## Implementation Notes
- This phase depends on Phase 0 (the `_discover_artifact_store()` fallback logic) and Phase 1 (the `list` method on `_ScopedArtifactStore`/`_DisabledArtifactStore`). Without Phase 0, tests 1 and 4 will fail (fallback not implemented). Without Phase 1, test 4 may fail if the list endpoint internally creates a `_ScopedArtifactStore` that lacks `list`.
- `InMemoryStateStore` is imported from `penguiflow.cli.playground_state` (not directly from `penguiflow.state.in_memory`) to match existing playground test conventions. See `tests/test_playground_phase3.py:18`.
- The test file uses `pytest.mark.asyncio` on each test method. The project has `asyncio_mode = "auto"` configured, so async tests run automatically, but the explicit marker matches the pattern used in `tests/test_playground_phase3.py`.
- Each test creates its own `tempfile.TemporaryDirectory` for the `project_root` parameter. This is required by `create_playground_app` and matches the existing test pattern.
- The `MagicMock` gotcha (auto-created attributes) is critical. Every test that needs the planner path to return `None` must explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`. This is documented in `tests/test_playground_phase3.py:646-649`.

## Verification Commands
```bash
# Run just the new test file
uv run pytest tests/cli/test_playground_artifact_discovery.py -v

# Run all playground tests to check for regressions
uv run pytest tests/ -k "playground" -x -q

# Lint the new test file
uv run ruff check tests/cli/test_playground_artifact_discovery.py

# Run the full test suite
uv run pytest
```
