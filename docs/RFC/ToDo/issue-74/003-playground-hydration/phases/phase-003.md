# Phase 003: Frontend -- listArtifacts API function and artifacts store hydrate method

## Objective
Add the frontend pieces needed for artifact hydration: a `listArtifacts()` API function that calls the new `GET /artifacts` backend endpoint (from Phase 002), and a `hydrate()` method on the artifacts Svelte store that bulk-loads `ArtifactRef` objects into the reactive Map. Also add frontend unit tests for the `hydrate()` method.

## Tasks
1. Add `listArtifacts()` function to `api.ts` (plan section 2)
2. Add `hydrate()` method to the `ArtifactsStore` interface and implementation (plan section 3)
3. Add unit tests for `hydrate()` to the existing artifacts test file

## Detailed Steps

### Step 1: Add `listArtifacts()` to api.ts
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/api.ts`
- Insert the new function after `getArtifactMeta()` (which ends at line 209) and before `steerTask()` (line 211)
- Follow the `listTasks` pattern at line 236: use `new URL()` + `url.searchParams.set()` for query string construction
- The function signature is `listArtifacts(sessionId: string, tenantId?: string, userId?: string): Promise<ArtifactRef[] | null>`
- Conditionally set `tenant_id` and `user_id` params only when truthy (use `if (tenantId)` / `if (userId)`)
- Use `fetchWithErrorHandling<ArtifactRef[]>()` and return `result.data` on success, `null` on failure
- The `ArtifactRef` type is already imported in this file (used by `getArtifactMeta`)

### Step 2: Add `hydrate()` to artifacts store
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts`
- Add `hydrate(refs: ArtifactRef[]): void;` to the `ArtifactsStore` interface (after `clear(): void;` at line 18)
- Add the implementation inside the `createArtifactsStore()` return object (after `clear()` at line 88, before the closing `};`)
- The implementation: early-return if `refs` is empty, create a new Map from current `artifacts`, add each ref by `ref.id` only if not already present (add-if-absent strategy), then reassign `artifacts` to trigger Svelte 5 `$state` reactivity

### Step 3: Add frontend unit tests
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/tests/unit/stores/artifacts.test.ts`
- Add a new `describe('hydrate', ...)` block after the existing `describe('count', ...)` block (which ends at line 215)
- Import `ArtifactRef` type if not already imported (check current imports at line 3)
- Add three tests:
  1. Hydrate empty store -- all refs added, count matches
  2. Hydrate with empty array -- no-op, store unchanged
  3. Hydrate skips existing artifacts -- SSE-delivered artifact preserved, not overwritten

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/api.ts
// Insert after getArtifactMeta() (line 209) and before steerTask() (line 211):

/**
 * List artifacts for a session (for hydration on session resume).
 * @param sessionId - The session ID to list artifacts for
 * @param tenantId - Optional tenant ID for scope filtering
 * @param userId - Optional user ID for scope filtering
 * @returns Array of ArtifactRef objects, or null on failure
 */
export async function listArtifacts(
  sessionId: string,
  tenantId?: string,
  userId?: string,
): Promise<ArtifactRef[] | null> {
  const url = new URL(`${BASE_URL}/artifacts`, window.location.origin);
  url.searchParams.set('session_id', sessionId);
  if (tenantId) {
    url.searchParams.set('tenant_id', tenantId);
  }
  if (userId) {
    url.searchParams.set('user_id', userId);
  }
  const result = await fetchWithErrorHandling<ArtifactRef[]>(url.toString());
  if (!result.ok) {
    console.error('artifacts list failed', result.error);
    return null;
  }
  return result.data;
}
```

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts
// Add to ArtifactsStore interface (after line 18, before the closing }):

  hydrate(refs: ArtifactRef[]): void;
```

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts
// Add to createArtifactsStore() return object (after clear() at line 88, before the closing };):

    /**
     * Bulk-load artifacts from a list of ArtifactRef objects.
     * Uses add-if-absent strategy: existing artifacts (e.g., from SSE) are not overwritten.
     * Called during session hydration to restore pre-existing artifacts.
     */
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

```typescript
// Target file: penguiflow/cli/playground_ui/tests/unit/stores/artifacts.test.ts
// Add after the describe('count', ...) block (after line 215), before the closing });
// Also add ArtifactRef to the imports at line 3 if not already there:
//   import type { ArtifactRef, ArtifactStoredEvent } from '$lib/types';

  describe('hydrate', () => {
    const createMockRef = (overrides: Partial<ArtifactRef> = {}): ArtifactRef => ({
      id: 'ref-1',
      mime_type: 'application/json',
      size_bytes: 512,
      filename: 'data.json',
      sha256: null,
      namespace: null,
      source: null,
      ...overrides
    });

    it('hydrates empty store with all refs', () => {
      const refs = [
        createMockRef({ id: 'h-1', filename: 'file1.json' }),
        createMockRef({ id: 'h-2', filename: 'file2.json' }),
        createMockRef({ id: 'h-3', filename: 'file3.json' }),
      ];

      artifactsStore.hydrate(refs);

      expect(artifactsStore.count).toBe(3);
      expect(artifactsStore.has('h-1')).toBe(true);
      expect(artifactsStore.has('h-2')).toBe(true);
      expect(artifactsStore.has('h-3')).toBe(true);
    });

    it('hydrate with empty array is no-op', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'existing' }));
      expect(artifactsStore.count).toBe(1);

      artifactsStore.hydrate([]);

      expect(artifactsStore.count).toBe(1);
      expect(artifactsStore.has('existing')).toBe(true);
    });

    it('hydrate skips existing artifacts (SSE-delivered take priority)', () => {
      // SSE-delivered artifact arrives first
      artifactsStore.addArtifact(createMockEvent({
        artifact_id: 'sse-first',
        filename: 'sse-version.pdf'
      }));

      // Hydration arrives later with same ID but different data
      const refs = [
        createMockRef({ id: 'sse-first', filename: 'hydrated-version.pdf' }),
        createMockRef({ id: 'new-artifact', filename: 'new.pdf' }),
      ];

      artifactsStore.hydrate(refs);

      // SSE version preserved, new artifact added
      expect(artifactsStore.count).toBe(2);
      expect(artifactsStore.get('sse-first')?.filename).toBe('sse-version.pdf');
      expect(artifactsStore.get('new-artifact')?.filename).toBe('new.pdf');
    });
  });
```

## Exit Criteria (Success)
- [ ] `listArtifacts()` function exists in `api.ts` with correct signature `(sessionId: string, tenantId?: string, userId?: string): Promise<ArtifactRef[] | null>`
- [ ] `listArtifacts()` uses `new URL()` + `searchParams.set()` pattern (not raw string interpolation)
- [ ] `listArtifacts()` conditionally sets `tenant_id` and `user_id` only when truthy
- [ ] `ArtifactsStore` interface includes `hydrate(refs: ArtifactRef[]): void`
- [ ] `createArtifactsStore()` implements `hydrate()` with add-if-absent semantics
- [ ] `hydrate()` creates a new Map for Svelte 5 `$state` reactivity (not mutating in place)
- [ ] All three frontend unit tests pass: hydrate empty store, hydrate empty array, hydrate skips existing
- [ ] TypeScript compilation succeeds with no errors

## Implementation Notes
- The `ArtifactRef` type is defined in `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/types/artifacts.ts` with fields: `id`, `mime_type`, `size_bytes`, `filename`, `sha256`, `namespace`, `source`. The backend `GET /artifacts` endpoint returns objects matching this shape (minus the `scope` field which is excluded server-side).
- The `fetchWithErrorHandling` function returns a `Result` type -- unwrap with `result.ok` / `result.data` / `result.error`. This is the same pattern used by `listTasks` and `getArtifactMeta`.
- The `BASE_URL` constant is already defined in `api.ts` and used by all other API functions.
- In the artifacts store, `artifacts` is a Svelte 5 `$state` variable (`let artifacts = $state<Map<string, ArtifactRef>>(new Map())`). Reassigning `artifacts = newMap` triggers reactivity. Mutating the existing Map in place would NOT trigger reactivity.
- The `createMockRef` helper in tests creates `ArtifactRef` objects directly (not from SSE events), matching what the `hydrate()` method receives from the API response.
- The existing `createMockEvent` helper creates `ArtifactStoredEvent` objects, which is what `addArtifact()` receives from SSE. The `ArtifactRef` shape differs slightly (SSE events have `artifact_id` while `ArtifactRef` has `id`).
- Check whether `ArtifactRef` is already in the type import at line 3 of the test file. Currently line 3 is `import type { ArtifactStoredEvent } from '$lib/types';`. If `ArtifactRef` is not imported, add it: `import type { ArtifactRef, ArtifactStoredEvent } from '$lib/types';`.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm test -- --run tests/unit/stores/artifacts.test.ts
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npx tsc --noEmit 2>&1 | head -30
```
