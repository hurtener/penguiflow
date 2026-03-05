# Phase 002: Add unit tests for artifact discovery fallback behavior

## Objective
Create a new test file `tests/cli/test_playground_artifact_discovery.py` with 4 test cases that validate the `_discover_artifact_store()` fallback logic added in Phase 001. Since `_discover_artifact_store()` is a closure inside `create_playground_app` and cannot be called directly, all tests exercise it indirectly through the artifact HTTP endpoints using `AsyncClient` with `ASGITransport`.

## Tasks
1. Create the new test file `tests/cli/test_playground_artifact_discovery.py`.
2. Implement 4 test cases covering: state store fallback, planner preference, NoOp exclusion, and list endpoint fallback.

## Detailed Steps

### Step 1: Create test file with imports and class structure
- Create `tests/cli/test_playground_artifact_discovery.py`.
- Import the following (matching patterns from `tests/test_playground_phase3.py`):
  - `tempfile` (for temporary directories)
  - `pytest` (for test markers)
  - `MagicMock`, `AsyncMock` from `unittest.mock`
  - `ASGITransport`, `AsyncClient` from `httpx`
  - `create_playground_app` from `penguiflow.cli.playground`
  - `InMemoryStateStore` from `penguiflow.cli.playground_state` (this re-exports from `penguiflow.state.in_memory`)
  - `ArtifactScope`, `InMemoryArtifactStore`, `NoOpArtifactStore` from `penguiflow.artifacts`
- Create a test class `TestArtifactDiscoveryFallback`.

### Step 2: Implement `test_discover_artifact_store_falls_back_to_state_store`
- **Goal**: When the agent wrapper has no discoverable planner but the custom state store has a valid `artifact_store`, the `GET /artifacts/{id}` endpoint should return 200 (not 501).
- Create an `InMemoryStateStore()`.
- Store an artifact via `await state_store.artifact_store.put_bytes(b"hello from state store", scope=ArtifactScope(session_id="test-session"))`.
- Capture the returned `ArtifactRef` and use its `.id` in the GET request.
- Create a `MagicMock()` for the agent wrapper. **Critically**: set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None` to prevent MagicMock from auto-creating attributes that would be picked up by `_discover_planner()`.
- Call `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=state_store)`.
- Use `AsyncClient` with `ASGITransport` to issue `GET /artifacts/{ref.id}?session_id=test-session`.
- Assert `response.status_code == 200` and `response.content == b"hello from state store"`.

### Step 3: Implement `test_discover_artifact_store_prefers_planner`
- **Goal**: When both the planner and the state store have artifact stores, the planner's store is used (existing behavior preserved).
- Create a standalone `InMemoryArtifactStore()` for the planner.
- Store an artifact in the planner's store: `await planner_artifact_store.put_bytes(b"from planner", scope=ArtifactScope(session_id="test-session"))`.
- Create an `InMemoryStateStore()` (which has its own separate artifact store).
- Create a `MagicMock()` for the agent wrapper. Set `mock_wrapper._planner = MagicMock(artifact_store=planner_artifact_store)`.
- Call `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=state_store)`.
- Issue `GET /artifacts/{ref.id}?session_id=test-session`.
- Assert `response.status_code == 200` and `response.content == b"from planner"`.

### Step 4: Implement `test_discover_artifact_store_skips_noop`
- **Goal**: When the state store's artifact store is a `NoOpArtifactStore`, the fallback returns `None` (endpoint returns 501).
- Create a `MagicMock()` for the state store: `mock_store = MagicMock()`. Set `mock_store.artifact_store = NoOpArtifactStore()`.
- Remove any `save_task` / `save_event` / `load_history` attributes so `SessionManager` doesn't try to use the mock as a real store: `del mock_store.save_task; del mock_store.save_event; del mock_store.load_history`.
- Create a `MagicMock()` for the agent wrapper. Set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`.
- Call `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=mock_store)`.
- Issue `GET /artifacts/any-id`.
- Assert `response.status_code == 501`.

### Step 5: Implement `test_discover_artifact_store_list_endpoint_uses_fallback`
- **Goal**: When the agent wrapper has no discoverable planner but the custom state store has artifacts, the `GET /artifacts?session_id=test-session` endpoint returns a non-empty list (not `[]`).
- This tests the `/artifacts` list endpoint specifically, which returns `[]` (not 501) when discovery fails -- a subtler failure mode.
- Create an `InMemoryStateStore()`.
- Store an artifact: `await state_store.artifact_store.put_bytes(b"listed artifact", mime_type="text/plain", scope=ArtifactScope(session_id="test-session"))`.
- Create a `MagicMock()` for the agent wrapper. Set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None`.
- Call `create_playground_app(project_root=tmpdir, agent=mock_wrapper, state_store=state_store)`.
- Issue `GET /artifacts?session_id=test-session`.
- Assert `response.status_code == 200`.
- Parse the JSON response and assert `len(data) >= 1`.

## Required Code

```python
# Target file: tests/cli/test_playground_artifact_discovery.py

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
            b"hello from state store",
            mime_type="application/octet-stream",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly set to None so MagicMock doesn't auto-create these attributes.
            # MagicMock auto-creates any attribute on access, which would make
            # _discover_planner() find a fake planner instead of returning None.
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

                assert response.status_code == 200, (
                    f"Expected 200 (state store fallback), got {response.status_code}: {response.text}"
                )
                assert response.content == b"hello from state store"

    @pytest.mark.asyncio
    async def test_discover_artifact_store_prefers_planner(self) -> None:
        """When both planner and state store have artifact stores, planner wins."""
        # Planner's artifact store (standalone, not tied to state store)
        planner_artifact_store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="test-session")
        ref = await planner_artifact_store.put_bytes(
            b"from planner",
            mime_type="application/octet-stream",
            scope=scope,
        )

        # State store (has its own separate artifact store)
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

                assert response.status_code == 200, (
                    f"Expected 200 (planner preferred), got {response.status_code}: {response.text}"
                )
                assert response.content == b"from planner"

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
            # Explicitly set to None so _discover_planner() returns None
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
                response = await client.get("/artifacts/any-id")

                assert response.status_code == 501, (
                    f"Expected 501 (NoOp skipped), got {response.status_code}: {response.text}"
                )

    @pytest.mark.asyncio
    async def test_discover_artifact_store_list_endpoint_uses_fallback(self) -> None:
        """GET /artifacts list endpoint finds artifacts via state store fallback."""
        state_store = InMemoryStateStore()
        scope = ArtifactScope(session_id="test-session")
        await state_store.artifact_store.put_bytes(
            b"listed artifact",
            mime_type="text/plain",
            scope=scope,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_wrapper = MagicMock()
            mock_wrapper.initialize = AsyncMock()
            mock_wrapper.shutdown = AsyncMock()
            # Explicitly set to None so _discover_planner() returns None
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
                assert len(data) >= 1, (
                    f"Expected at least 1 artifact in list, got {len(data)}. "
                    "The /artifacts endpoint returns [] (not 501) when discovery fails."
                )
```

## Exit Criteria (Success)
- [ ] File `tests/cli/test_playground_artifact_discovery.py` exists
- [ ] Contains 4 test methods inside `TestArtifactDiscoveryFallback` class
- [ ] `test_discover_artifact_store_falls_back_to_state_store` passes (200, not 501)
- [ ] `test_discover_artifact_store_prefers_planner` passes (200, planner content returned)
- [ ] `test_discover_artifact_store_skips_noop` passes (501 when NoOp is the only artifact store)
- [ ] `test_discover_artifact_store_list_endpoint_uses_fallback` passes (non-empty list from state store)
- [ ] `uv run ruff check tests/cli/test_playground_artifact_discovery.py` passes
- [ ] `uv run pytest tests/cli/test_playground_artifact_discovery.py -x -v` -- all 4 tests pass
- [ ] `uv run pytest tests/ -k "playground" -x -q` -- no regressions in existing tests

## Implementation Notes
- **Depends on Phase 000 and Phase 001**: The `list` endpoint test (`test_discover_artifact_store_list_endpoint_uses_fallback`) calls `artifact_store.list()` on the discovered store. If Phase 000 (adding `list` to `_ScopedArtifactStore`) was not applied, the endpoint would fail with `AttributeError`. The fallback tests depend on Phase 001's changes to `_discover_artifact_store()`.
- **MagicMock auto-creates attributes**: This is the most critical gotcha. `MagicMock()` auto-creates any attribute on access, so `getattr(mock, "_planner")` returns a new `MagicMock` instead of raising `AttributeError`. Worse, a `MagicMock` instance passes `isinstance(x, ArtifactStore)` because `@runtime_checkable` Protocol only checks attribute existence, and `MagicMock` satisfies that. You MUST explicitly set `mock_wrapper._planner = None` and `mock_wrapper._orchestrator = None` for tests where `_discover_planner()` should return `None`. See `tests/test_playground_phase3.py:646-649` for the existing pattern.
- **Artifact scoping**: `InMemoryStateStore.artifact_store` returns a `PlaygroundArtifactStore` (a session-scoped facade). When calling `put_bytes`, you MUST pass `scope=ArtifactScope(session_id="test-session")` so the artifact is discoverable. The GET request must use the same `session_id` (via query param or header). Without matching scopes, `list` and `get` will return empty/None.
- **Import path**: Use `from penguiflow.cli.playground_state import InMemoryStateStore` (not `from penguiflow.state.in_memory import ...`) to match the import pattern used in existing playground tests (`tests/test_playground_phase3.py:18`).
- **`del mock_store.save_task`**: In `test_discover_artifact_store_skips_noop`, the mock state store must NOT have `save_task`/`save_event`/`load_history` attributes, or `SessionManager` will try to use it as a real persistence backend and fail. Deleting these attributes from the `MagicMock` prevents that.
- The test file lives in `tests/cli/` which already exists and contains other playground test files.

## Verification Commands
```bash
# Lint the new test file
uv run ruff check tests/cli/test_playground_artifact_discovery.py

# Run only the new tests (verbose, stop on first failure)
uv run pytest tests/cli/test_playground_artifact_discovery.py -x -v

# Run all playground tests to check for regressions
uv run pytest tests/ -k "playground" -x -q

# Run the full test suite
uv run pytest
```

---

## Implementation Notes (Post-Implementation)

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes

- **`tests/cli/test_playground_artifact_discovery.py`** -- Modified the existing file (created by the phase-001 implementer) to align with the phase-002 specification. The file already contained 4 working test cases that exercised the correct behavior. The following changes were made:
  1. **Renamed class** from `TestDiscoverArtifactStoreFallback` to `TestArtifactDiscoveryFallback` to match the specification.
  2. **Updated module docstring** to match the specified docstring explaining the closure/indirect-testing approach and the 501/200 semantics.
  3. **Moved imports to top level** -- `ASGITransport`, `AsyncClient` from `httpx` and `create_playground_app` from `penguiflow.cli.playground` were moved from inside each test method to the module's top-level import section, matching the specification.
  4. **Updated class docstring** to match: "Verify _discover_artifact_store() falls back to the state store."
  5. **Updated test docstrings** to match the specification for all 4 tests.
  6. **Added `del mock_store.save_task / save_event / load_history`** to `test_discover_artifact_store_skips_noop` as specified, preventing `SessionManager` from treating the mock as a real persistence backend.

### Key Considerations

1. **Pre-existing test file.** The phase-001 implementer created this test file as part of their implementation (documented in their phase-001 notes). The file already contained 4 correctly functioning tests. Rather than rewriting from scratch, I modified the existing file to match the phase-002 specification in class name, docstrings, import structure, and the `del mock_store` defensive pattern.

2. **Cosmetic vs functional differences.** The existing test data values (e.g., `b"fallback artifact data"` vs spec's `b"hello from state store"`) were preserved because they are functionally equivalent -- the tests verify the correct bytes are returned, not the specific content. Changing the data values would have no effect on correctness and would create unnecessary diff noise.

3. **Extra assertions preserved.** The existing file has additional assertions beyond what the spec requires (e.g., checking `content-type` and `content-disposition` headers in test 1, checking `ref.id in artifact_ids` in test 4). These provide strictly more coverage than the specification calls for and were kept.

4. **`del mock_store` pattern.** The specification explicitly calls for `del mock_store.save_task; del mock_store.save_event; del mock_store.load_history` in the NoOp test. The original file lacked this. While the test passed without it (likely because `create_playground_app` doesn't exercise those attributes in the code path tested), the `del` calls are a defensive measure per the spec and were added.

### Assumptions

- The phase-001 implementer's test file was a reasonable starting point and did not need to be deleted and recreated. The phase-002 spec says "Create the new test file" but the file already existed with functionally correct tests.
- The `filename` parameter passed to `put_bytes` (e.g., `filename="fallback.bin"`) is not specified in the phase-002 code but was present in the original file. It was preserved because it does not affect the test logic and provides additional coverage for the artifact storage path.
- Preserving the existing test data values (slightly different from the "Required Code" section) is acceptable since the exit criteria focus on behavior (status codes, content correctness) not exact byte values.

### Deviations from Plan

- **Test data values differ from "Required Code" section.** The spec uses `b"hello from state store"`, `b"from planner"`, `b"listed artifact"` while the existing (preserved) tests use `b"fallback artifact data"`, `b"planner artifact data"`, `b"listed artifact data"`. These differences are purely cosmetic and do not affect test correctness.
- **Additional assertions beyond spec.** Tests 1 and 4 have extra assertions (content-type headers, artifact ID in list) beyond what the spec calls for. These were kept as they add value without risk.
- **Inline `filename` parameter in `put_bytes` calls.** The spec does not include `filename` in its `put_bytes` calls, but the existing tests pass it. This was preserved as it exercises a valid code path.

### Potential Risks & Reviewer Attention Points

1. **The `del mock_store.save_task` pattern in `test_discover_artifact_store_skips_noop`.** While the test passes both with and without these `del` statements, they are a defensive measure against future changes to `create_playground_app` that might exercise the state store's persistence methods during app creation. If `SessionManager`'s initialization changes to call these methods, tests without the `del` statements would produce confusing errors.

2. **Top-level imports of `create_playground_app`.** Moving the import to the top level means the module is imported at test collection time rather than at test execution time. This is the standard pattern per the spec and matches how other test files in the project are structured, but it means any import errors in `playground.py` would prevent collection of all 4 tests rather than failing individually.

### Files Modified

- `tests/cli/test_playground_artifact_discovery.py` -- Modified (class name, docstrings, import structure, `del` statements)
