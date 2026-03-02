# Phase 002: Frontend TypeScript types, stores, services, tests, and build

## Objective
Propagate the `namespace` field through the frontend TypeScript layer: update the `ArtifactRef` interface, the Svelte artifacts store, the SSE session-stream parser, add frontend test coverage, and rebuild the `dist/` assets so the checked-in bundle reflects the changes.

## Tasks
1. Add `namespace: string | null` to the `ArtifactRef` TypeScript interface.
2. Update `addArtifact()` in the artifacts Svelte store to derive `namespace` from the event source.
3. Update `toArtifactRef()` in `session-stream.ts` to populate `namespace` using the existing `getString()` helper.
4. Update existing frontend test and add a new test for namespace derivation.
5. Run `npm run build` to regenerate `dist/` assets.

## Detailed Steps

### Step 1: Add `namespace` to `ArtifactRef` interface
- Open `penguiflow/cli/playground_ui/src/lib/types/artifacts.ts`.
- In the `ArtifactRef` interface, add the field `namespace: string | null;` with JSDoc comment `/** Namespace for artifact grouping (e.g., tool name) */`, placed **after the `sha256` field (line 15) and before the `source` field (line 17)**. This mirrors the Python field ordering (after `scope`/`sha256`, before `source`).
- Do **not** add `namespace` to the `ArtifactStoredEvent` interface. Namespace is derived from `source.namespace` in the event, not stored as a top-level field on the event.

### Step 2: Update `addArtifact()` in the artifacts store
- Open `penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts`.
- In the `addArtifact(event: ArtifactStoredEvent)` method (~line 52), update the `ArtifactRef` object literal to include `namespace`.
- Derive the value inline: `namespace: typeof event.source?.namespace === 'string' ? event.source.namespace : null`.
- Place it after `sha256: null,` and before `source: event.source`.
- Do **not** import `getString` from `session-stream.ts` -- that function is file-private and not exported.

### Step 3: Update `toArtifactRef()` in `session-stream.ts`
- Open `penguiflow/cli/playground_ui/src/lib/services/session-stream.ts`.
- In the `toArtifactRef(stored: ArtifactStoredEvent)` function (~line 298), add `namespace` to the returned object literal.
- Derive the value using the existing file-local `getString()` helper (line 228): `namespace: getString(stored.source?.namespace) ?? null`.
- Place it after `sha256: null,` and before `source: stored.source ?? {}`.
- No changes are needed in `toArtifactStoredEvent()` (~line 275) since namespace stays inside `source`.

### Step 4: Update frontend tests
- Open `penguiflow/cli/playground_ui/tests/unit/stores/artifacts.test.ts`.
- In the `'stores correct artifact properties'` test (~line 44), after the existing `expect(artifact?.sha256).toBeNull();` assertion (line 61), add: `expect(artifact?.namespace).toBeNull();`. The mock event's `source` is `{ tool: 'screenshot_tool', view_id: 'view-1' }` which has no `namespace` key, so the result should be `null`.
- Add a new test inside the `addArtifact` describe block (after the `'can add multiple distinct artifacts'` test, ~line 91) that verifies namespace derivation from `source.namespace`:
  - Create a mock event with `source: { tool: 'test_tool', namespace: 'my_ns' }`.
  - Call `artifactsStore.addArtifact(event)`.
  - Assert the resulting artifact has `namespace === 'my_ns'`.

### Step 5: Rebuild `dist/` assets
- From the `penguiflow/cli/playground_ui/` directory, run `npm run build` to regenerate the checked-in `dist/` files.
- The regenerated files should be committed alongside the source changes.

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/types/artifacts.ts
// The full updated ArtifactRef interface:
export interface ArtifactRef {
  /** Unique artifact identifier */
  id: string;
  /** MIME type of the artifact (e.g., "application/pdf", "image/png") */
  mime_type: string | null;
  /** Size of the artifact in bytes */
  size_bytes: number | null;
  /** Original filename of the artifact */
  filename: string | null;
  /** SHA256 hash of the artifact content */
  sha256: string | null;
  /** Namespace for artifact grouping (e.g., tool name) */
  namespace: string | null;
  /** Source metadata (tool name, parameters, etc.) */
  source: Record<string, unknown>;
}
```

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/stores/domain/artifacts.svelte.ts
// In addArtifact(), the ref object literal becomes:
      const ref: ArtifactRef = {
        id: event.artifact_id,
        mime_type: event.mime_type,
        size_bytes: event.size_bytes,
        filename: event.filename,
        sha256: null,
        namespace: typeof event.source?.namespace === 'string' ? event.source.namespace : null,
        source: event.source
      };
```

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/session-stream.ts
// In toArtifactRef(), the returned object becomes:
function toArtifactRef(stored: ArtifactStoredEvent): ArtifactRef {
  return {
    id: stored.artifact_id,
    mime_type: stored.mime_type ?? null,
    size_bytes: stored.size_bytes ?? null,
    filename: stored.filename ?? null,
    sha256: null,
    namespace: getString(stored.source?.namespace) ?? null,
    source: stored.source ?? {}
  };
}
```

```typescript
// Target file: penguiflow/cli/playground_ui/tests/unit/stores/artifacts.test.ts
// Change 1: In 'stores correct artifact properties' test, add after `expect(artifact?.sha256).toBeNull();`:
      expect(artifact?.namespace).toBeNull();

// Change 2: Add new test inside the `addArtifact` describe block:
    it('derives namespace from source.namespace', () => {
      const event = createMockEvent({
        artifact_id: 'ns-artifact',
        source: { tool: 'test_tool', namespace: 'my_ns' }
      });

      artifactsStore.addArtifact(event);
      const artifact = artifactsStore.get('ns-artifact');

      expect(artifact).toBeDefined();
      expect(artifact?.namespace).toBe('my_ns');
    });
```

## Exit Criteria (Success)
- [ ] `ArtifactRef` interface in `artifacts.ts` has `namespace: string | null` after `sha256` and before `source`.
- [ ] `ArtifactStoredEvent` interface is unchanged (no `namespace` field added).
- [ ] `addArtifact()` in `artifacts.svelte.ts` derives `namespace` from `event.source?.namespace` and includes it in the ref.
- [ ] `toArtifactRef()` in `session-stream.ts` derives `namespace` using `getString(stored.source?.namespace) ?? null`.
- [ ] `toArtifactStoredEvent()` in `session-stream.ts` is unchanged.
- [ ] Existing test `'stores correct artifact properties'` asserts `namespace` is `null` when source has no namespace.
- [ ] New test `'derives namespace from source.namespace'` passes and asserts `namespace === 'my_ns'`.
- [ ] All frontend tests pass: `npm test` from `penguiflow/cli/playground_ui/`.
- [ ] `dist/` assets are regenerated via `npm run build`.

## Implementation Notes
- This phase depends on Phase 000. The Python model must have the `namespace` field so that SSE events carry it in `source.namespace`.
- The `getString()` helper in `session-stream.ts` (line 228) is file-private. It is used in `toArtifactRef()` because it is in the same file. The artifacts store (`artifacts.svelte.ts`) cannot import it, so it uses an inline `typeof` check instead.
- The `ArtifactStoredEvent` interface does NOT get a `namespace` field. Namespace lives inside `source.namespace` in the SSE payload. The conversion functions (`addArtifact`, `toArtifactRef`) extract it when creating `ArtifactRef` objects.
- After modifying source files, `npm run build` must be run from `penguiflow/cli/playground_ui/` to regenerate the `dist/` directory which is checked into git.
- The `ArtifactsCard.test.ts` file does NOT need changes. It asserts on DOM elements, not `ArtifactRef` shape.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm test
```
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run build
```
```bash
# Verify the namespace field appears in the built JS bundle
grep -r "namespace" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/dist/assets/*.js | head -5
```
