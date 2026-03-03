# Phase 004: Frontend -- Wire hydration into session stream start

## Objective
Connect all the pieces: when a playground session stream starts (including on browser refresh/resume), call `listArtifacts()` and feed the results into `artifactsStore.hydrate()`. This is the final integration step that makes pre-existing artifacts visible in the Artifacts panel immediately on session resume.

## Tasks
1. Widen the `SessionStreamStores` Pick type to include `setupStore` (plan section 4a)
2. Add `listArtifacts` import and hydration call in the `start()` method (plan section 4b)

## Detailed Steps

### Step 1: Widen SessionStreamStores type
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/session-stream.ts`
- Find the `SessionStreamStores` type at lines 8-11:
  ```typescript
  type SessionStreamStores = Pick<
    AppStores,
    'tasksStore' | 'notificationsStore' | 'chatStore' | 'artifactsStore' | 'interactionsStore'
  >;
  ```
- Add `'setupStore'` to the Pick union so the session stream manager can read `tenantId` and `userId` from the setup store

### Step 2: Add listArtifacts import
- Find the existing import at line 2: `import { listTasks } from './api';`
- Change to: `import { listTasks, listArtifacts } from './api';`

### Step 3: Add hydration call in start()
- Find the `start(sessionId: string)` method at line 93
- Locate the existing `listTasks` hydration block at lines 99-103:
  ```typescript
  listTasks(sessionId).then(tasks => {
    if (tasks) {
      this.stores.tasksStore.setTasks(tasks);
    }
  });
  ```
- Insert the artifact hydration call **immediately after** this block (before the `const url = new URL(...)` line at 104)
- Read `tenantId` and `userId` from `this.stores.setupStore`
- Call `listArtifacts(sessionId, tenantId, userId)` and pipe results to `this.stores.artifactsStore.hydrate()`
- Follow the exact same fire-and-forget `.then()` pattern as `listTasks`

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/session-stream.ts
// Change line 2 from:
//   import { listTasks } from './api';
// To:
import { listTasks, listArtifacts } from './api';
```

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/session-stream.ts
// Replace lines 8-11 from:
//   type SessionStreamStores = Pick<
//     AppStores,
//     'tasksStore' | 'notificationsStore' | 'chatStore' | 'artifactsStore' | 'interactionsStore'
//   >;
// To:
type SessionStreamStores = Pick<
  AppStores,
  'tasksStore' | 'notificationsStore' | 'chatStore' | 'artifactsStore' | 'interactionsStore' | 'setupStore'
>;
```

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/session-stream.ts
// Insert after the listTasks hydration block (after line 103, before the const url = new URL(...) line):

    const { tenantId, userId } = this.stores.setupStore;
    listArtifacts(sessionId, tenantId, userId).then(refs => {
      if (refs && refs.length > 0) {
        this.stores.artifactsStore.hydrate(refs);
      }
    });
```

## Exit Criteria (Success)
- [ ] `SessionStreamStores` type includes `'setupStore'` in the Pick union
- [ ] `listArtifacts` is imported from `'./api'`
- [ ] `start()` method calls `listArtifacts(sessionId, tenantId, userId)` after the existing `listTasks` call
- [ ] The hydration call reads `tenantId` and `userId` from `this.stores.setupStore`
- [ ] The `.then()` callback checks `refs && refs.length > 0` before calling `hydrate()`
- [ ] TypeScript compilation succeeds with no errors
- [ ] The full frontend test suite still passes (no regressions)

## Implementation Notes
- The `AppStores` type (defined in `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/stores/index.ts` at line 96) already includes `setupStore: SetupStore`. The full `AppStores` object is passed from `App.svelte` (line 43) when creating the `SessionStreamManager`, so widening the `Pick` type is sufficient -- no changes to the caller are needed.
- The `SetupStore` interface (in `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/stores/features/setup.svelte.ts`) exposes `tenantId: string` and `userId: string` as reactive getters with default values `'playground-tenant'` and `'playground-user'`.
- The `listArtifacts` function (added in Phase 003) returns `Promise<ArtifactRef[] | null>` -- `null` on API failure. The null-check `if (refs && refs.length > 0)` handles both failure (`null`) and empty responses.
- **Deduplication is handled naturally:**
  - If hydration completes first, later SSE `artifact_stored` events call `addArtifact()` which uses `Map.set` (overwrites) -- safe, same data.
  - If SSE events arrive first, `hydrate()` uses add-if-absent (`!newMap.has(ref.id)`) -- SSE version preserved.
  - No duplicates possible since Map keys are artifact IDs.
- This is the final phase. After completion, the full feature is wired end-to-end: session resume triggers `listArtifacts()` API call, backend queries `ArtifactStore.list()` with proper scope, frontend hydrates the artifacts panel.

## Verification Commands
```bash
# TypeScript compilation check
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npx tsc --noEmit 2>&1 | head -30

# Run full frontend test suite to check for regressions
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm test

# Full backend verification (all phases combined)
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_artifact_handling.py tests/test_payload_builders.py tests/test_tool_jobs.py tests/cli/test_playground_endpoints.py -v
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check . && uv run mypy
```
