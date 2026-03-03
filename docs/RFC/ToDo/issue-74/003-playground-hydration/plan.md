# Artifact Hydration on Playground Session Start

## Context

When a playground session is resumed (e.g. browser refresh), the Artifacts panel starts empty and only shows artifacts as new `artifact_stored` SSE events arrive. Pre-existing artifacts from the session are invisible until the agent produces new ones. The fix: call `ArtifactStore.list()` at session init to hydrate the panel with existing artifacts.

## Changes (~4 files, 1 new endpoint)

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

- Builds `ArtifactScope(session_id=..., tenant_id=..., user_id=...)` from the provided params
- Calls `artifact_store.list(scope=scope)`
- Returns `[ref.model_dump() for ref in refs]`
- Returns `[]` (not error) when no store configured or no session provided — hydration is best-effort

### 2. Frontend: Add `listArtifacts()` API function

**File:** `penguiflow/cli/playground_ui/src/lib/services/api.ts` — after `getArtifactMeta()`

```typescript
export async function listArtifacts(
  sessionId: string,
  tenantId?: string,
  userId?: string,
): Promise<ArtifactRef[]>
```

- Builds query string: `GET /artifacts?session_id={sessionId}&tenant_id={tenantId}&user_id={userId}`
- Returns `[]` on failure (fire-and-forget, never blocks UI)

### 3. Frontend: Add `hydrate()` to artifacts store

**File:** `penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts` — add to interface + implementation

```typescript
hydrate(refs: ArtifactRef[]): void
```

- Bulk-loads artifacts into the Map
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
  if (refs.length > 0) {
    this.stores.artifactsStore.hydrate(refs);
  }
});
```

Follows the exact same pattern as the existing `listTasks` hydration. The full `AppStores` object is already passed from `App.svelte` (line 43), so widening the Pick type is sufficient.

## Deduplication

Handled naturally:
- **Hydration first, SSE later:** `addArtifact` overwrites via `Map.set` — safe, same data
- **SSE first, hydration later:** `hydrate` skips existing keys — SSE version preserved
- No duplicates possible since Map keys are artifact IDs

## Verification

1. `uv run pytest tests/cli/ -k "test_list_artifacts"` — new backend tests
2. `cd penguiflow/cli/playground_ui && npm test` — new frontend store tests
3. `uv run ruff check . && uv run mypy` — lint/types pass
4. Manual: start playground, produce artifacts, refresh browser → artifacts panel should show pre-existing artifacts immediately
