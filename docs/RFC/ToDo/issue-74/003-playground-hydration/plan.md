# Artifact Hydration on Playground Session Start

## Context

When a playground session is resumed (e.g. browser refresh), the Artifacts panel starts empty and only shows artifacts as new `artifact_stored` SSE events arrive. Pre-existing artifacts from the session are invisible until the agent produces new ones. The fix: call `ArtifactStore.list()` at session init to hydrate the panel with existing artifacts.

## Changes (~8 files, 1 new endpoint)

> **Application order for `playground.py`:** Sections 0d and 1 both modify this file. Apply section 0d first (modifies `read_resource` at line 2068+), then section 1 (inserts before line 1919). Since 0d's changes are below 1's insertion point, applying 0d first keeps all line references stable. Alternatively, use function/decorator anchors (`read_resource`, `get_artifact`) rather than line numbers.

### 0. Upstream fix: Propagate full scope in artifact storage paths

Four artifact storage paths only set `session_id` on the scope (or no scope at all), omitting `tenant_id`/`user_id`. This causes `artifact_store.list(scope=...)` to return no results when filtering by `tenant_id`/`user_id`, since `_scope_matches` requires exact match for non-None filter fields. Fix all paths to propagate the full scope from `tool_context` (matching the existing pattern in `sessions/session_kv.py:412-417`).

**Paths already correct (no changes needed):**
- `penguiflow/sessions/session_kv.py:412-417` — reference pattern, sets all 4 fields from `tool_context`
- `penguiflow/artifacts.py:261-278` — `ScopedArtifacts` constructor sets all 4 fields
- `penguiflow/tools/node.py` (8 call sites) — all run inside `ToolNode` methods which only execute in the planner context, where `ctx._artifacts` is the `_EventEmittingArtifactStoreProxy`; fix **0a** corrects `_resolve_scope` to inject the full scope for these calls
- `penguiflow/tools/resources.py:244,270` — `ResourceCache` receives `ctx._artifacts` (the proxy in planner context); full scope provided after fix **0a**
- `penguiflow/state/in_memory.py:75` — `scope_filter` is only a store partitioning key; actual artifact scope passes through from callers

**0a. File:** `penguiflow/planner/artifact_handling.py` — modify `_resolve_scope` (line 47-57)

Change from:
```python
def _resolve_scope(self, scope: ArtifactScope | None) -> ArtifactScope | None:
    """Inject session_id from trajectory if scope is missing."""
    if scope is not None:
        return scope
    # Get session_id from trajectory's tool_context for proper session scoping
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

Inside the **inner `_store` closure** (not the outer function), **replace line 192** (`scope = ArtifactScope(session_id=session_id) if session_id else None`) with the comment `# `scope` is now captured from the outer function's parameter (no local override)`. Note: line 192 is a local variable definition inside `_store`'s body — removing it causes Python to resolve `scope` via closure capture from the enclosing `_extract_artifacts_from_observation`'s `scope` parameter instead. No changes to `_store`'s signature needed. The resulting `_store` body should be (note the comment on the former line 192 is prescriptive — add it to the code):

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

**0c. File:** `penguiflow/planner/payload_builders.py` — modify `_clamp_observation` (line 119) and its wrapper in `react.py`

`_clamp_observation` stores oversized observations as artifacts via `artifact_store.put_text()` (line 152) but passes **no `scope` at all**. The `artifact_store` here is the raw `ReactPlanner._artifact_store` (not the `_EventEmittingArtifactStoreProxy` which has `_resolve_scope`). Fix by threading scope through from the active trajectory.

Add a `scope: ArtifactScope | None = None` parameter to `_clamp_observation`:
```python
async def _clamp_observation(
    *,
    observation: dict[str, Any],
    spec_name: str,
    trajectory_step: int,
    config: ObservationGuardrailConfig,
    artifact_store: ArtifactStore,
    artifact_registry: ArtifactRegistry,
    active_trajectory: Trajectory | None,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
    scope: ArtifactScope | None = None,
) -> tuple[dict[str, Any], bool]:
```

Pass `scope` to the `put_text` call (line 152):
```python
ref = await artifact_store.put_text(
    serialized,
    namespace=f"observation.{spec_name}",
    scope=scope,
)
```

Add `ArtifactScope` import at the top of `payload_builders.py`:
```python
from ..artifacts import ArtifactScope
```

**File:** `penguiflow/planner/react.py` — modify `_clamp_observation` wrapper (line 1090) to build and pass the scope:
```python
async def _clamp_observation(
    self,
    observation: dict[str, Any],
    spec_name: str,
    trajectory_step: int,
) -> tuple[dict[str, Any], bool]:
    scope: ArtifactScope | None = None
    traj = self._active_trajectory
    if traj is not None:
        tool_ctx = traj.tool_context
        if tool_ctx and isinstance(tool_ctx, dict):
            session_id = tool_ctx.get("session_id")
            if session_id:
                scope = ArtifactScope(
                    session_id=str(session_id),
                    tenant_id=tool_ctx.get("tenant_id"),
                    user_id=tool_ctx.get("user_id"),
                    trace_id=tool_ctx.get("trace_id"),
                )
    return await _clamp_observation_impl(
        observation=observation,
        spec_name=spec_name,
        trajectory_step=trajectory_step,
        config=self._observation_guardrail,
        artifact_store=self._artifact_store,
        artifact_registry=self._artifact_registry,
        active_trajectory=self._active_trajectory,
        emit_event=self._emit_event,
        time_source=self._time_source,
        scope=scope,
    )
```

Add `ArtifactScope` to the existing import from `..artifacts` (already imported at line 13: `from ..artifacts import ArtifactStore` — extend to `from ..artifacts import ArtifactScope, ArtifactStore`).

**0d. File:** `penguiflow/cli/playground.py` — modify resource reading endpoint (line 2067-2119) to propagate `tenant_id`/`user_id`

The `GET /resources/{namespace}/{uri}` endpoint creates `ArtifactScope(session_id=resolved_session)` without `tenant_id`/`user_id`. Add query parameters and propagate them.

> **Note:** `trace_id` is intentionally omitted from HTTP endpoints (steps 0d and 1). HTTP callers don't have trace context — `trace_id` is an internal planner concept. Passing `trace_id=None` in the scope means "don't filter on that dimension", so the list endpoint correctly returns all session artifacts across all traces.

Add `tenant_id` and `user_id` query params to the endpoint signature:
```python
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

Update the `ArtifactScope` construction (line 2118) to include them:
```python
scoped_store = _ScopedArtifactStore(
    artifact_store,
    ArtifactScope(
        session_id=resolved_session,
        tenant_id=tenant_id,
        user_id=user_id,
    ),
)
```

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

Implementation notes:
- Returns `[]` (not error) when artifact store is absent or session is missing — hydration is best-effort, unlike existing endpoints that return 501
- `resolved_session = session_id or x_session_id` matches existing `get_artifact`/`get_artifact_meta` convention
- **Local import** of `ArtifactScope` follows existing pattern at line 2114 (top-level imports use lazy loading for optional deps)
- `except Exception` preserves `SystemExit`/`KeyboardInterrupt` while catching all store failures
- **Logger:** `_LOGGER` already exists at line 81 — do **not** create a new logger variable
- `exclude={"scope"}` prevents leaking scoping metadata (`tenant_id`, `user_id`, `session_id`, `trace_id`) to the client

### 2. Frontend: Add `listArtifacts()` API function

**File:** `penguiflow/cli/playground_ui/src/lib/services/api.ts` — after `getArtifactMeta()`

```typescript
export async function listArtifacts(
  sessionId: string,
  tenantId?: string,
  userId?: string,
): Promise<ArtifactRef[] | null>
```

- Uses `new URL()` + `url.searchParams.set()` to build the query string (matches `listTasks` pattern at line 236, not raw string interpolation)
- Conditionally sets `tenant_id` and `user_id` params only when truthy — use `if (tenantId)` / `if (userId)` so empty strings are omitted (e.g., if user clears the setup fields)
- Uses `fetchWithErrorHandling<ArtifactRef[]>()` which returns `Result<ArtifactRef[], ApiError>` — unwrap the `Result`: return `result.data` on `result.ok`, return `null` on `!result.ok` (matches `listTasks` convention)
- Returns `null` on failure (consistent with `listTasks` convention in this file)

### 3. Frontend: Add `hydrate()` to artifacts store

**File:** `penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts` — add to interface + implementation

Add `hydrate(refs: ArtifactRef[]): void` to the `ArtifactsStore` interface and implement it:

```typescript
hydrate(refs: ArtifactRef[]): void {
  if (refs.length === 0) return;
  const newMap = new Map(artifacts);
  for (const ref of refs) {
    if (!newMap.has(ref.id)) {
      newMap.set(ref.id, ref);
    }
  }
  artifacts = newMap;
},
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

## Scope coverage note

All **internal** penguiflow artifact storage paths are covered by fixes 0a–0d. Specifically: the 8 `ctx._artifacts` call sites in `tools/node.py` only execute inside `ToolNode` methods (planner context, through the proxy fixed in 0a); the tool_jobs post-execution extraction is fixed in 0b; and `SessionKVFacade` already builds full scope independently.

End users should always use the public `ctx.artifacts` API (`ScopedArtifacts`), which injects full scope automatically. The private `ctx._artifacts` property exposes the raw store without scope injection — using it directly is outside the recommended flow and scope correctness is the caller's responsibility.

## Tests

### Upstream scope tests

**File:** `tests/test_artifact_handling.py` — **create new file** (does not exist yet). `_resolve_scope` is a private method of `_EventEmittingArtifactStoreProxy` in `penguiflow/planner/artifact_handling.py`. Import from `penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy`. Instantiate with a mock `Trajectory` (whose `tool_context` is a dict), a `NoOpArtifactStore` or `InMemoryArtifactStore`, a no-op event emitter `Callable[[PlannerEvent], None]`, and a mock `ArtifactRegistry` (`MagicMock(spec=ArtifactRegistry)`). Use this scaffold:

```python
"""Tests for penguiflow.planner.artifact_handling — scope propagation."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from penguiflow.artifacts import ArtifactScope, NoOpArtifactStore
from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.planner.models import PlannerEvent
from penguiflow.planner.trajectory import Trajectory


def _noop_emit(event: PlannerEvent) -> None:
    pass


def _make_proxy(tool_context: dict[str, Any]) -> _EventEmittingArtifactStoreProxy:
    traj = MagicMock(spec=Trajectory)
    traj.tool_context = tool_context
    registry = MagicMock(spec=ArtifactRegistry)
    return _EventEmittingArtifactStoreProxy(
        store=NoOpArtifactStore(),
        emit_event=_noop_emit,
        time_source=time.monotonic,
        trajectory=traj,
        registry=registry,
    )
```

1. **`_resolve_scope` includes tenant_id/user_id/trace_id** → when trajectory's `tool_context` has `session_id`, `tenant_id`, `user_id`, and `trace_id`, the returned `ArtifactScope` contains all four fields

**File:** `tests/test_tool_jobs.py` — add to existing file

2. **`_extract_artifacts_from_observation` receives full scope** → import the private function directly via `from penguiflow.sessions.tool_jobs import _extract_artifacts_from_observation`. When called with a scope containing `session_id`, `tenant_id`, `user_id`, and `trace_id`, verify via `artifact_store.list(scope=ArtifactScope(session_id=...))` that stored artifacts have matching scope fields on the returned `ArtifactRef` objects (inspect the store directly, not just the returned dicts)

**File:** `tests/test_payload_builders.py` — **create new file** (does not exist yet). Use this scaffold:

```python
"""Tests for penguiflow.planner.payload_builders."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.planner.models import PlannerEvent
from penguiflow.planner.models import ObservationGuardrailConfig
from penguiflow.planner.payload_builders import _clamp_observation


def _make_config(*, auto_artifact_threshold: int = 100) -> ObservationGuardrailConfig:
    """Create an ObservationGuardrailConfig with a low threshold for testing."""
    return ObservationGuardrailConfig(auto_artifact_threshold=auto_artifact_threshold)


def _noop_emit(event: PlannerEvent) -> None:
    pass


def _make_registry() -> ArtifactRegistry:
    """Create a mock ArtifactRegistry for testing."""
    return MagicMock(spec=ArtifactRegistry)
```

Call `_clamp_observation` with:
- `artifact_store=InMemoryArtifactStore()`
- `artifact_registry=_make_registry()`
- `active_trajectory=None` (no trajectory needed for scope tests — scope is passed directly)
- `emit_event=_noop_emit`
- `time_source=time.monotonic`
- `scope=ArtifactScope(...)` (the parameter under test)

3. **`_clamp_observation` stores artifacts with full scope** → call `_clamp_observation` with an `InMemoryArtifactStore`, a large observation exceeding `auto_artifact_threshold`, and a scope with all four fields. Verify via `artifact_store.list()` that the stored artifact has the correct scope fields.

4. **`_clamp_observation` stores artifacts with no scope when None** → call `_clamp_observation` with `scope=None` and verify it doesn't crash (backwards-compatible, scope is optional with default `None`).

### Backend endpoint tests (`tests/cli/test_playground_endpoints.py` — add to existing `TestArtifactEndpoints` class)

**Test fixture setup:** Tests 3-6 require an artifact store injected into the mock agent. `_discover_artifact_store()` traverses `agent_wrapper → _planner → artifact_store`. To configure:
- Use `InMemoryArtifactStore` from `penguiflow.artifacts`
- Set it on the mock planner: give `MockAgentWrapper` a direct `_planner` attribute (the first path `_discover_planner()` checks) with an `artifact_store` attribute pointing to the `InMemoryArtifactStore` instance
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

1. `uv run pytest tests/test_artifact_handling.py -k "resolve_scope"` — upstream scope fix (artifact_handling)
2. `uv run pytest tests/test_tool_jobs.py -k "extract_artifacts"` — upstream scope fix (tool_jobs)
3. `uv run pytest tests/test_payload_builders.py -k "clamp_observation"` — upstream scope fix (payload_builders)
4. `uv run pytest tests/cli/ -k "test_list_artifacts"` — new backend endpoint tests
5. `cd penguiflow/cli/playground_ui && npm test` — new frontend store tests
6. `uv run ruff check . && uv run mypy` — lint/types pass
7. Manual: start playground, produce artifacts, refresh browser → artifacts panel should show pre-existing artifacts immediately
