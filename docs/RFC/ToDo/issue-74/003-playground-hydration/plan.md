# Artifact Hydration on Playground Session Start

## Context

When a playground session is resumed (e.g. browser refresh), the Artifacts panel starts empty and only shows artifacts as new `artifact_stored` SSE events arrive. Pre-existing artifacts from the session are invisible until the agent produces new ones. The fix: call `ArtifactStore.list()` at session init to hydrate the panel with existing artifacts.

## Changes (~6 files, 1 new endpoint)

### 0. Upstream fix: Propagate full scope in artifact storage paths

Two artifact storage paths only set `session_id` on the scope, omitting `tenant_id`/`user_id`. This causes `artifact_store.list(scope=...)` to return no results when filtering by `tenant_id`/`user_id`, since `_scope_matches` requires exact match for non-None filter fields. Fix both paths to propagate the full scope from `tool_context` (matching the existing pattern in `sessions/session_kv.py:412-417`).

**0a. File:** `penguiflow/planner/artifact_handling.py` — modify `_resolve_scope` (line 47-57)

Change from:
```python
def _resolve_scope(self, scope: ArtifactScope | None) -> ArtifactScope | None:
    """Inject session_id from trajectory if scope is missing."""
    if scope is not None:
        return scope
    tool_ctx = self._trajectory.tool_context
    if tool_ctx and isinstance(tool_ctx, dict):
        session_id = tool_ctx.get("session_id")
        if session_id:
            return ArtifactScope(session_id=str(session_id))
    return None
```

To:
```python
def _resolve_scope(self, scope: ArtifactScope | None) -> ArtifactScope | None:
    """Inject scope fields from trajectory if scope is missing."""
    if scope is not None:
        return scope
    tool_ctx = self._trajectory.tool_context
    if tool_ctx and isinstance(tool_ctx, dict):
        session_id = tool_ctx.get("session_id")
        if session_id:
            return ArtifactScope(
                session_id=str(session_id),
                tenant_id=tool_ctx.get("tenant_id"),
                user_id=tool_ctx.get("user_id"),
                trace_id=tool_ctx.get("trace_id"),
            )
    return None
```

**0b. File:** `penguiflow/sessions/tool_jobs.py` — modify `_extract_artifacts_from_observation` signature (line 165) and its call site (line 283)

Change function signature — replace `session_id: str | None` with `scope: ArtifactScope | None`:
```python
async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifact_store: ArtifactStore,
    scope: ArtifactScope | None,
) -> list[dict[str, Any]]:
```

Inside the function, **delete line 192** (`scope = ArtifactScope(session_id=session_id) if session_id else None`). The inner `_store` function will now resolve `scope` via closure capture from the outer function's `scope` parameter — no changes to `_store`'s signature needed. The resulting `_store` body should be:

```python
async def _store(
    item: Any,
    *,
    item_index: int | None,
    _field_name: str = field_name,
    _node_name: str = node_name,
) -> None:
    serialized = json.dumps(item, ensure_ascii=False)
    # `scope` is now captured from the outer function's parameter (no local override)
    ref = await artifact_store.put_text(
        serialized,
        mime_type="application/json",
        filename=f"{_node_name}.{_field_name}.json",
        namespace=f"tool_artifact.{_node_name}.{_field_name}",
        scope=scope,
        meta={
            "node": _node_name,
            "field": _field_name,
            "item_index": item_index,
        },
    )
    artifact_type = _infer_artifact_type(item)
    compact_meta = _extract_compact_metadata(item)
    stub: dict[str, Any] = {
        "artifact": ref.model_dump(mode="json"),
        **compact_meta,
    }
    if artifact_type:
        stub["type"] = artifact_type
    entry: dict[str, Any] = {
        "node": _node_name,
        "field": _field_name,
        "artifact": stub,
    }
    if item_index is not None:
        entry["item_index"] = item_index
    artifacts.append(entry)
```

Change the call site (line 280-289) to build the scope there:
```python
session_id = snapshot.session_id if isinstance(snapshot.session_id, str) and snapshot.session_id else None
tool_ctx = snapshot.tool_context or {}
artifact_scope = ArtifactScope(
    session_id=session_id,
    tenant_id=tool_ctx.get("tenant_id"),
    user_id=tool_ctx.get("user_id"),
    trace_id=tool_ctx.get("trace_id"),
) if session_id else None
extracted_artifacts = []
if isinstance(payload, Mapping):
    extracted_artifacts = await _extract_artifacts_from_observation(
        node_name=spec.name,
        out_model=spec.out_model,
        observation=payload,
        artifact_store=ctx._artifacts,
        scope=artifact_scope,
    )
```

`ArtifactScope` is already imported at line 22 — no import change needed.

### 1. Backend: Add `GET /artifacts` list endpoint

**File:** `penguiflow/cli/playground.py` — insert before line 1919 (before `GET /artifacts/{artifact_id}` to avoid path parameter collision)

```python
@app.get("/artifacts")
async def list_artifacts(
    session_id: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> list[Mapping[str, Any]]:
```

- Obtain the artifact store via `_discover_artifact_store()` (line 1020). If it returns `None`, return `[]` immediately (hydration is best-effort, unlike existing endpoints that return 501)
- Resolve session_id: `resolved_session = session_id or x_session_id` (matches existing `get_artifact`/`get_artifact_meta` endpoints). If `resolved_session` is `None`, return `[]`
- **Import `ArtifactScope` locally** inside the function body: `from penguiflow.artifacts import ArtifactScope` (follows existing pattern at line 2114 — top-level imports use lazy loading for optional deps)
- Builds `ArtifactScope(session_id=resolved_session, tenant_id=tenant_id, user_id=user_id)` from the resolved params
- Calls `artifact_store.list(scope=scope)` on the discovered store **inside a `try/except Exception`** — on any `Exception`, log a warning and return `[]` (best-effort: hydration failures must not break the playground; use `except Exception` to preserve `SystemExit`/`KeyboardInterrupt`)
- Returns `[ref.model_dump(exclude={"scope"}) for ref in refs]` — exclude `scope` to avoid leaking scoping metadata to the client

### 2. Frontend: Add `listArtifacts()` API function

**File:** `penguiflow/cli/playground_ui/src/lib/services/api.ts` — after `getArtifactMeta()`

```typescript
export async function listArtifacts(
  sessionId: string,
  tenantId?: string,
  userId?: string,
): Promise<ArtifactRef[] | null>
```

- Uses `new URL()` + `url.searchParams.set()` to build the query string (matches `listTasks` pattern at line 237, not raw string interpolation)
- Conditionally sets `tenant_id` and `user_id` params only when provided
- Uses `fetchWithErrorHandling<ArtifactRef[]>()` which returns `Result<ArtifactRef[], ApiError>` — unwrap the `Result`: return `result.data` on `result.ok`, return `null` on `!result.ok` (matches `listTasks` convention)
- Returns `null` on failure (consistent with `listTasks` convention in this file)

### 3. Frontend: Add `hydrate()` to artifacts store

**File:** `penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts` — add to interface + implementation

```typescript
hydrate(refs: ArtifactRef[]): void
```

- Bulk-loads artifacts into the Map via a single new Map creation (for Svelte 5 `$state` reactivity)
- Map key is `ref.id` (same artifact ID as `event.artifact_id` in `addArtifact`)
- Uses add-if-absent strategy: skips artifacts already present (SSE-delivered artifacts take priority)
- Early return if `refs` is empty

### 4. Frontend: Wire hydration into session stream start

**File:** `penguiflow/cli/playground_ui/src/lib/services/session-stream.ts`

**4a.** Widen `SessionStreamStores` Pick type to include `setupStore` (line 8-11):
```typescript
type SessionStreamStores = Pick<
  AppStores,
  'tasksStore' | 'notificationsStore' | 'chatStore' | 'artifactsStore' | 'interactionsStore' | 'setupStore'
>;
```

**4b.** In `start()`, after the existing `listTasks` call (line 99-103), read tenant/user from `setupStore`:
```typescript
const { tenantId, userId } = this.stores.setupStore;
listArtifacts(sessionId, tenantId, userId).then(refs => {
  if (refs && refs.length > 0) {
    this.stores.artifactsStore.hydrate(refs);
  }
});
```

Also add `listArtifacts` to the existing import on line 2: `import { listTasks, listArtifacts } from './api';`

Follows the exact same pattern as the existing `listTasks` hydration. The full `AppStores` object is already passed from `App.svelte` (line 43), so widening the Pick type is sufficient. The null-check on `refs` matches the `listTasks` convention (returns `null` on failure).

## Deduplication

Handled naturally:
- **Hydration first, SSE later:** `addArtifact` overwrites via `Map.set` — safe, same data
- **SSE first, hydration later:** `hydrate` skips existing keys — SSE version preserved
- No duplicates possible since Map keys are artifact IDs

## Tests

### Upstream scope tests

**File:** `tests/test_artifacts.py` — add to existing file

1. **`_resolve_scope` includes tenant_id/user_id/trace_id** → when trajectory's `tool_context` has `session_id`, `tenant_id`, `user_id`, and `trace_id`, the returned `ArtifactScope` contains all four fields

**File:** `tests/test_tool_jobs.py` — add to existing file

2. **`_extract_artifacts_from_observation` receives full scope** → import the private function directly via `from penguiflow.sessions.tool_jobs import _extract_artifacts_from_observation`. When called with a scope containing `session_id`, `tenant_id`, `user_id`, and `trace_id`, verify via `artifact_store.get_ref()` that stored artifacts have matching scope fields (inspect the store directly, not just the returned dicts)

### Backend endpoint tests (`tests/cli/test_playground_endpoints.py` — add to existing `TestArtifactEndpoints` class)

**Test fixture setup:** Tests 3-6 require an artifact store injected into the mock agent. `_discover_artifact_store()` traverses `agent_wrapper → _planner → artifact_store`. To configure:
- Use `InMemoryArtifactStore` from `penguiflow.artifacts`
- Set it on the mock planner: give `MockAgentWrapper` a `_planner` attribute (or nested `_orchestrator._planner`) with an `artifact_store` attribute pointing to the `InMemoryArtifactStore` instance
- Pre-populate with `await store.put_text(...)` using an `ArtifactScope(session_id=..., tenant_id=..., user_id=...)` to match the query params used in the test
- Use `pytest`'s async fixtures or call `asyncio.run()` / `loop.run_until_complete()` for setup if needed

Test scenarios for the `GET /artifacts` endpoint:

1. **No artifact store configured** → returns `[]` (not error)
2. **No session provided** (no query param, no header) → returns `[]`
3. **Valid session with artifacts** → returns list of artifact dicts (without `scope` field)
4. **Session via `X-Session-ID` header** → resolves correctly, returns matching artifacts
5. **Session via query param takes priority** over header when both provided
6. **Empty session (no artifacts stored)** → returns `[]`

### Frontend tests (`penguiflow/cli/playground_ui/tests/unit/stores/artifacts.test.ts` — add to existing file)

Test scenarios for the `hydrate()` method:

1. **Hydrate empty store** → all refs added, count matches
2. **Hydrate with empty array** → no-op, store unchanged
3. **Hydrate skips existing artifacts** → SSE-delivered artifact preserved, not overwritten

## Verification

1. `uv run pytest tests/test_artifacts.py -k "resolve_scope"` — upstream scope fix (artifact_handling)
2. `uv run pytest tests/test_tool_jobs.py -k "extract_artifacts"` — upstream scope fix (tool_jobs)
3. `uv run pytest tests/cli/ -k "test_list_artifacts"` — new backend endpoint tests
4. `cd penguiflow/cli/playground_ui && npm test` — new frontend store tests
5. `uv run ruff check . && uv run mypy` — lint/types pass
6. Manual: start playground, produce artifacts, refresh browser → artifacts panel should show pre-existing artifacts immediately
