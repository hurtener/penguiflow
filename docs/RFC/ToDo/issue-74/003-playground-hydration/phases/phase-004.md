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

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-03

### Summary of Changes
- **`session-stream.ts`**: Widened `SessionStreamStores` Pick type to include `'setupStore'`, added `listArtifacts` to the import from `'./api'`, and inserted the artifact hydration call in the `start()` method immediately after the existing `listTasks` hydration block. The hydration call destructures `tenantId` and `userId` from `this.stores.setupStore`, calls `listArtifacts(sessionId, tenantId, userId)`, and pipes results to `this.stores.artifactsStore.hydrate(refs)` using the same fire-and-forget `.then()` pattern as the existing `listTasks` call.
- **`session-stream.test.ts`**: Updated the test to include `createSetupStore` in imports and pass `setupStore` to `createSessionStreamManager()`, since widening the `SessionStreamStores` type made `setupStore` a required property.
- **`ArtifactItem.test.ts`**: Added missing `namespace: null` to the `mockArtifact` test fixture to match the `ArtifactRef` interface (which gained the `namespace` field in Phase 003).

### Key Considerations
- The hydration call is placed after the `listTasks` call but before the `EventSource` setup, matching the existing pattern. Both are fire-and-forget (no `await`), so the SSE connection is not blocked by either hydration call.
- The `tenantId` and `userId` are read synchronously from the reactive setup store at the moment `start()` is called. This is correct because these values are set during the initial app setup and remain stable for the duration of the session.
- The `refs && refs.length > 0` guard handles both the `null` failure case and the empty-array case, avoiding unnecessary reactive store updates.

### Assumptions
- The `ArtifactItem.test.ts` fix for the missing `namespace` field was a pre-existing issue introduced in Phase 003 when the `ArtifactRef` type gained the `namespace` property. The test was not updated at that time. This fix is necessary for `svelte-check` to pass with zero errors.
- The `tsc --noEmit` command in the verification section fails with a pre-existing `TS2688: Cannot find type definition file for 'node'` error because `@types/node` is not installed in the playground UI workspace. This is unrelated to the phase changes. The `svelte-check` tool (the project's actual TypeScript validator for Svelte) passes with zero errors and zero warnings, which is the correct verification path.

### Deviations from Plan
- **Added `setupStore` to session-stream test**: The plan did not mention updating the test file, but widening `SessionStreamStores` to include `'setupStore'` made it a required property, causing a TypeScript error in the existing test. The fix was minimal: import `createSetupStore` and pass it in the mock stores object.
- **Fixed `namespace` field in ArtifactItem test**: The plan did not mention this file, but `svelte-check` flagged it as a type error. Added `namespace: null` to the mock fixture to satisfy the `ArtifactRef` interface. This was a pre-existing gap from Phase 003.

### Potential Risks & Reviewer Attention Points
- The hydration call captures `tenantId` and `userId` synchronously at call time. If a user changes these values in the setup panel after `start()` has been called but before the promise resolves, the API call will use the original values. This is the correct behavior since the session was started with those credentials.
- No error logging is added for the `listArtifacts` failure case in `session-stream.ts` because `listArtifacts` in `api.ts` already logs `'artifacts list failed'` to the console on error. Adding another log here would be redundant.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/session-stream.ts` (modified: import, type, hydration call)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/tests/unit/services/session-stream.test.ts` (modified: added setupStore to test mock)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/tests/unit/components/sidebar-right/ArtifactItem.test.ts` (modified: added missing namespace field)
