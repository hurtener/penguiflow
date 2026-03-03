# Phase 002: Playground resource scope fix and GET /artifacts list endpoint

## Objective
Apply the final upstream scope fix to the playground resource reading endpoint (plan section 0d) and add the new `GET /artifacts` list endpoint (plan section 1). Both changes are in `penguiflow/cli/playground.py`. The list endpoint is the backend API that the frontend will call to hydrate the Artifacts panel on session resume. Also add backend integration tests for the new endpoint.

## Tasks
1. Add `tenant_id` and `user_id` query parameters to the `GET /resources/{namespace}/{uri:path}` endpoint and propagate them into `ArtifactScope` (plan section 0d)
2. Add the new `GET /artifacts` list endpoint before the existing `GET /artifacts/{artifact_id}` endpoint (plan section 1)
3. Add integration tests for the new endpoint to `tests/cli/test_playground_endpoints.py`

## Detailed Steps

### Step 1: Fix resource endpoint scope (section 0d)
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground.py`
- Find the `read_resource` endpoint at line 2067-2073
- Add `tenant_id: str | None = None,` and `user_id: str | None = None,` parameters to the function signature, between `session_id` and `x_session_id`
- Find the `ArtifactScope` construction at line 2116-2119
- Change `ArtifactScope(session_id=resolved_session)` to `ArtifactScope(session_id=resolved_session, tenant_id=tenant_id, user_id=user_id)`
- **Important:** `trace_id` is intentionally omitted from HTTP endpoints -- HTTP callers don't have trace context

### Step 2: Add GET /artifacts list endpoint (section 1)
- In the same file, find the `# --- Artifact Endpoints ---` section comment at line 1917
- Insert the new `list_artifacts` endpoint **before** the existing `@app.get("/artifacts/{artifact_id}")` at line 1919
- Route ordering matters: `GET /artifacts` (exact match) must be registered before `GET /artifacts/{artifact_id}` (path parameter) to avoid FastAPI treating "artifacts" as an `artifact_id` value
- The endpoint uses a **local import** of `ArtifactScope` (follows existing pattern at line 2114)
- Uses `_discover_artifact_store()` (existing helper) to find the store
- Uses `_LOGGER` (existing logger at line 81) for warnings -- do NOT create a new logger
- Returns `[]` (not error) when artifact store is absent or session is missing -- hydration is best-effort
- Excludes `scope` field from returned dicts via `ref.model_dump(exclude={"scope"})` to prevent leaking tenant/user/trace metadata to the client

### Step 3: Add integration tests
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/cli/test_playground_endpoints.py`
- Add tests inside or after the existing `TestArtifactEndpoints` class (currently at line 544)
- Tests 1-2 use the default `MockAgentWrapper` (no artifact store)
- Tests 3-6 require an `InMemoryArtifactStore` injected via `MockAgentWrapper._planner.artifact_store`
- Pre-populate the store with `await store.put_text(...)` using `ArtifactScope(session_id=..., tenant_id=..., user_id=...)` to match query params

## Required Code

```python
# Target file: penguiflow/cli/playground.py
# Replace the read_resource endpoint signature (lines 2067-2073) with:

    @app.get("/resources/{namespace}/{uri:path}")
    async def read_resource(
        namespace: str,
        uri: str,
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> Mapping[str, Any]:
```

```python
# Target file: penguiflow/cli/playground.py
# Replace the ArtifactScope construction (lines 2116-2119) with:

            scoped_store = _ScopedArtifactStore(
                artifact_store,
                ArtifactScope(
                    session_id=resolved_session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                ),
            )
```

```python
# Target file: penguiflow/cli/playground.py
# Insert BEFORE the existing @app.get("/artifacts/{artifact_id}") at line 1919.
# This should go after the "# --- Artifact Endpoints ---" comment (line 1917)
# and before the existing get_artifact endpoint.

    @app.get("/artifacts")
    async def list_artifacts(
        session_id: str | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
    ) -> list[Mapping[str, Any]]:
        """List artifacts for a session (best-effort hydration)."""
        artifact_store = _discover_artifact_store()
        if artifact_store is None:
            return []

        resolved_session = session_id or x_session_id
        if resolved_session is None:
            return []

        from penguiflow.artifacts import ArtifactScope

        scope = ArtifactScope(
            session_id=resolved_session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        try:
            refs = await artifact_store.list(scope=scope)
        except Exception:
            _LOGGER.warning("Failed to list artifacts for session %s", resolved_session, exc_info=True)
            return []
        return [ref.model_dump(exclude={"scope"}) for ref in refs]
```

```python
# Target file: tests/cli/test_playground_endpoints.py
# Add these imports at the top (after existing imports):

import asyncio
from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore

# Add these tests. They can go inside the existing TestArtifactEndpoints class
# or as a new class after it. Using a new class for clarity:

class TestListArtifactsEndpoint:
    """Tests for GET /artifacts list endpoint."""

    def test_list_artifacts_no_store_returns_empty(self, tmp_path: Path) -> None:
        """When no artifact store is configured, returns []."""
        wrapper = MockAgentWrapper()
        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", params={"session_id": "sess-1"})
        assert response.status_code == 200
        assert response.json() == []

    def test_list_artifacts_no_session_returns_empty(self, tmp_path: Path) -> None:
        """When no session ID is provided (no query param, no header), returns []."""
        store = InMemoryArtifactStore()
        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_artifacts_valid_session(self, tmp_path: Path) -> None:
        """Valid session with artifacts returns list of artifact dicts without scope field."""
        store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="sess-1", tenant_id="t-1", user_id="u-1")
        asyncio.get_event_loop().run_until_complete(
            store.put_text("hello", mime_type="text/plain", filename="test.txt", scope=scope)
        )

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", params={
            "session_id": "sess-1",
            "tenant_id": "t-1",
            "user_id": "u-1",
        })
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        # Verify scope field is excluded from response
        assert "scope" not in data[0]
        assert "id" in data[0]

    def test_list_artifacts_session_via_header(self, tmp_path: Path) -> None:
        """Session via X-Session-ID header resolves correctly."""
        store = InMemoryArtifactStore()
        scope = ArtifactScope(session_id="header-sess")
        asyncio.get_event_loop().run_until_complete(
            store.put_text("data", scope=scope)
        )

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", headers={"X-Session-ID": "header-sess"})
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_list_artifacts_query_param_priority_over_header(self, tmp_path: Path) -> None:
        """Session via query param takes priority over header when both provided."""
        store = InMemoryArtifactStore()
        scope_query = ArtifactScope(session_id="query-sess")
        scope_header = ArtifactScope(session_id="header-sess")
        asyncio.get_event_loop().run_until_complete(
            store.put_text("query data", scope=scope_query)
        )
        asyncio.get_event_loop().run_until_complete(
            store.put_text("header data", scope=scope_header)
        )

        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get(
            "/artifacts",
            params={"session_id": "query-sess"},
            headers={"X-Session-ID": "header-sess"},
        )
        assert response.status_code == 200
        data = response.json()
        # Should return only the query-sess artifact
        assert len(data) == 1

    def test_list_artifacts_empty_session(self, tmp_path: Path) -> None:
        """Empty session (no artifacts stored) returns []."""
        store = InMemoryArtifactStore()
        wrapper = MockAgentWrapper()
        wrapper._planner = MagicMock()
        wrapper._planner.artifact_store = store

        app = create_playground_app(project_root=tmp_path, agent=wrapper)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/artifacts", params={"session_id": "empty-sess"})
        assert response.status_code == 200
        assert response.json() == []
```

## Exit Criteria (Success)
- [ ] `GET /resources/{namespace}/{uri:path}` endpoint accepts `tenant_id` and `user_id` query params
- [ ] `ArtifactScope` in `read_resource` includes `tenant_id` and `user_id`
- [ ] `GET /artifacts` endpoint exists and is registered before `GET /artifacts/{artifact_id}`
- [ ] `GET /artifacts` returns `[]` when no artifact store is configured (not 501)
- [ ] `GET /artifacts` returns `[]` when no session is provided
- [ ] `GET /artifacts` returns artifact dicts without `scope` field when artifacts exist
- [ ] `GET /artifacts` resolves session from `X-Session-ID` header as fallback
- [ ] All new tests in `tests/cli/test_playground_endpoints.py` pass
- [ ] No ruff lint errors in modified files
- [ ] No mypy type errors in modified files

## Implementation Notes
- **Route ordering in FastAPI:** `GET /artifacts` (exact path) must be registered before `GET /artifacts/{artifact_id}` (path parameter). If reversed, FastAPI would match `/artifacts` as `artifact_id=""` on the parameterized route. Insert the new endpoint before line 1919.
- **Application order within `playground.py`:** The plan recommends applying section 0d first (modifies `read_resource` at line 2068+), then section 1 (inserts before line 1919). Since 0d's changes are below 1's insertion point, applying 0d first keeps all line references stable. Alternatively, use function/decorator anchors rather than line numbers.
- **`_discover_artifact_store()`** is an existing helper that traverses `agent_wrapper -> _planner -> artifact_store` and returns `None` for `NoOpArtifactStore`. The test fixture setup mirrors this by setting `wrapper._planner.artifact_store`.
- **`_LOGGER`** already exists at line 81 of `playground.py` -- do NOT create a new logger.
- **Local import** of `ArtifactScope` follows the existing pattern at line 2114 (playground uses lazy loading for optional dependencies).
- **`ref.model_dump(exclude={"scope"})`** prevents leaking scoping metadata to the client. The `ArtifactRef` Pydantic model has a `scope` field that contains `tenant_id`, `user_id`, `session_id`, and `trace_id`.
- The `asyncio.get_event_loop().run_until_complete()` pattern in tests is needed because `store.put_text()` is async but the test setup is synchronous. Alternatively, use `pytest`'s async fixtures or `asyncio.run()`.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/cli/test_playground_endpoints.py -k "test_list_artifacts" -v
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/cli/playground.py tests/cli/test_playground_endpoints.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy penguiflow/cli/playground.py
```
