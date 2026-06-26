# Phase 008: Playground frontend (Svelte) — `artifact_stored` handler + fetch-by-id + dedupe

## Objective
Make the Svelte playground render store-backed UI components end-to-end. KEEP the existing inline `artifact_chunk`
-> `ui_component` rendering (`inline`/`both` rely on it; the committed `dist/` keeps working). ADD an
`artifact_stored` handler that, for the `penguiflow_ui_component` namespace, fetches the JSON by `artifact_id`,
converts it to the same payload shape the inline path uses, and renders it in the same slot — deduping strictly on
the opaque store `artifact_id` (decision 7). Rebuild `dist/`.

## Tasks
1. Add a JSON-fetch-by-id helper + payload conversion into the inline `ArtifactChunkPayload`/`ComponentArtifact`
   shape, so both paths render through one code path.
2. Add an `artifact_stored` handler keyed on `source.namespace === "penguiflow_ui_component"` that calls the helper
   and feeds `interactionsStore.addArtifactChunk(...)`.
3. Add dedupe state keyed on the opaque store `artifact_id`, surviving both inline-first and `artifact_stored`-first
   arrival orders.
4. Keep the inline path intact; update affected unit tests; rebuild `dist/` (vite).

## Detailed Steps

### Step 1: JSON fetch + payload conversion (`api.ts`, new helper)
- Today `api.ts` (`:162-192`) exposes artifact download only as a blob-triggered browser download, and the current
  `artifact_stored` handlers (`chat-stream.ts:422-428`, `event-stream.ts:66-72`) only register a downloadable
  artifact. Add a helper that does `GET /artifacts/{id}` (already exists), parses the JSON, and converts it into the
  same `ArtifactChunkPayload`/`ComponentArtifact` shape the inline path feeds to
  `interactionsStore.addArtifactChunk(...)` (`interactions.svelte.ts:27-55`). The MCP-app path in `chat-stream.ts`
  is prior art for fetch-then-render.

### Step 2: `artifact_stored` handler (`chat-stream.ts`, `event-stream.ts`, `session-stream.ts`)
- When an `ArtifactStoredEvent` has `source.namespace === "penguiflow_ui_component"`, call the Step 1 helper with the
  event's `artifact_id`, then render in the same slot via `addArtifactChunk(...)`. Use the `message_id`/
  `default_message_id` the backend now injects (Phase 007) for placement (`Message.svelte:31-37` filters component
  artifacts by `message_id`).

### Step 3: Dedupe on opaque `artifact_id` (decision 7)
- Track rendered `artifact_id`s. In `both` mode the inline `artifact_chunk` now carries the same opaque `artifact_id`
  in its `meta` (Phase 003). Skip an `artifact_stored` whose id was already rendered inline, and skip an inline chunk
  whose id already arrived via `artifact_stored`. Key STRICTLY on the store `artifact_id` — never on the component's
  `id`/`component_id`. The dedupe state must survive either arrival order.

### Step 4: Keep inline path + tests + dist rebuild
- Do NOT remove the inline `artifact_chunk` -> `ui_component` rendering in `chat-stream.ts`/`event-stream.ts`/
  `session-stream.ts`.
- Update affected unit tests under `penguiflow/cli/playground_ui/tests/`.
- Rebuild `dist/` with vite so the committed bundle reflects the new handler.
- Use the Svelte MCP server for any Svelte component changes, and re-run its autofixer/check after edits.

## Required Code
```text
No single copy-pasteable block — this phase spans several TypeScript/Svelte files under
penguiflow/cli/playground_ui/src/. The implementer must follow the existing inline-path code as the template:
- api.ts                         : add fetch-by-id JSON helper + payload conversion to ArtifactChunkPayload
- chat-stream.ts                 : add artifact_stored ui_component branch; keep inline branch; dedupe by artifact_id
- event-stream.ts                : same artifact_stored branch + dedupe
- session-stream.ts              : same artifact_stored branch + dedupe
- interactions.svelte.ts         : reuse addArtifactChunk(...) for both paths; hold dedupe set keyed on artifact_id
- (tests) playground_ui/tests/*  : cover artifact_stored render + dedupe in both arrival orders
Then: rebuild dist/ via vite.
```

## Exit Criteria (Success)
- [ ] An `artifact_stored` event with `source.namespace === "penguiflow_ui_component"` triggers a `GET /artifacts/{id}`,
      and the fetched JSON renders as a `ui_component` in the same slot the inline path uses.
- [ ] The fetch-by-id result is converted to the same `ArtifactChunkPayload`/`ComponentArtifact` shape and rendered
      via `interactionsStore.addArtifactChunk(...)` (one render code path for both inline and store-backed).
- [ ] Dedupe (decision 7): in `both` mode a component delivered via BOTH `artifact_chunk` and `artifact_stored`
      renders ONCE, keyed strictly on the opaque store `artifact_id`, regardless of arrival order.
- [ ] Placement: the rendered component attaches to the correct message via the backend-supplied
      `message_id`/`default_message_id` (Phase 007), not via a frontend-side active-message guess.
- [ ] The inline `artifact_chunk` -> `ui_component` path is unchanged and still works (`inline`/`both` frontends
      unaffected).
- [ ] `dist/` is rebuilt and committed; affected unit tests pass.
- [ ] Backwards-compat: existing frontend behavior on `inline` is unchanged; existing playground_ui tests stay green
      for the inline path.

## Implementation Notes
- Depends on Phase 007 (backend injects `message_id`/`default_message_id` on `penguiflow_ui_component`
  `artifact_stored` frames, and serves UI-component JSON by id).
- This is more than "add a handler" — the three sub-tasks (JSON fetch + conversion; message placement via the
  backend frames; dedupe state on opaque id) must all land.
- In scope per decision 1 (full end-to-end) — required so store-backed modes are usable end-to-end in the playground.
- Non-breaking guarantee holds: the inline path is kept, so `inline`/`both` frontends are unaffected.
- Use the Svelte MCP server (official docs + autofixer) for Svelte component edits; call its check tool again after
  fixes to confirm.

## Verification Commands
```bash
# Frontend unit tests (run from the playground_ui dir; adjust to the project's npm/pnpm setup)
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm test

# Rebuild the committed bundle
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run build

# Backend suite still green
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/ -k "playground or rich_output or artifact" -q
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes

- **`src/lib/services/api.ts`**
  - Added `fetchArtifactJson(artifactId, sessionId?)`: `GET /artifacts/{id}` (id URL-encoded), parses the
    `application/json` body, returns the parsed record or `null` (logs and swallows errors via the existing
    `fetchWithErrorHandling`). Sends `X-Session-ID` only when a session id is supplied (so access control works
    for the SSE/AG-UI/session paths, and unscoped fetches still work for backward-compat).
  - Added `storedComponentToArtifactChunk(stored, artifactId)`: converts the stored UI-component JSON
    (`{ id, component, props, title, summary, metadata }`) into the same `ArtifactChunkPayload` shape the inline
    path feeds to `addArtifactChunk` (`{ artifact_type: 'ui_component', chunk: { id, component, props, title }, meta }`).
    It reuses `stored.metadata` as the chunk `meta` and **always** stamps `meta.artifact_id = artifactId` (the opaque
    store id) so dedupe keys strictly on the store id. Returns `null` when the payload has no `component`.
  - Added a tiny private `asPlainRecord` helper (record coercion) to keep `storedComponentToArtifactChunk` under the
    lint complexity threshold.
- **`src/lib/stores/features/interactions.svelte.ts`**
  - Added a single-point dedupe set `renderedArtifactIds: Set<string>` inside `createInteractionsStore`.
    `addArtifactChunk` now reads `payload.meta.artifact_id` (the opaque store id) and skips the insert if that id was
    already rendered, recording it otherwise. Inline-only (`inline` mode) chunks carry no `meta.artifact_id` and are
    therefore never deduped — existing behavior preserved exactly. `clear()` also clears the set.
  - This is the **single dedupe point** for decision 7: because both the inline (`both`-mode) chunk and the
    store-backed (`artifact`/`both`) frame funnel through `addArtifactChunk` and both carry the same
    `meta.artifact_id`, dedupe survives either arrival order automatically.
- **`src/lib/services/chat-stream.ts`** (SSE + AG-UI)
  - Imported `fetchArtifactJson` / `storedComponentToArtifactChunk`.
  - Added a `sessionId` instance field set in `start()` (SSE), `startAgui()`, and `resumeAgui()`, and cleared in
    `close()`, used to scope the store-backed JSON fetch.
  - `handleArtifactStored` (SSE) and the AG-UI `CUSTOM` `artifact_stored` branch now: if the event's
    `source.namespace === "penguiflow_ui_component"`, fetch the JSON by id and render via `addArtifactChunk`
    (placement via backend `message_id`/`default_message_id`, falling back to the active agent message);
    otherwise keep the existing download-only `artifactsStore.addArtifact` path for binary/MCP artifacts.
  - Added a shared `renderStoredUiComponent(artifactId, messageId?)` private method + an
    `isUiComponentArtifactStored(stored)` module helper.
  - The inline `artifact_chunk` -> `ui_component` branches are unchanged.
- **`src/lib/services/event-stream.ts`**
  - Same `artifact_stored` ui_component branch (fetch + render) + `renderStoredUiComponent` method +
    `isUiComponentArtifactStored` helper. Uses the `sessionId` already in `_connect` scope and reads the
    backend `message_id`/`default_message_id` for placement.
- **`src/lib/services/session-stream.ts`**
  - Proactive `RESULT` updates: store-backed UI-component artifacts (namespace `penguiflow_ui_component`) carried in
    `content.artifacts` are now fetched by id and rendered under the proactive agent message (via
    `renderStoredUiComponent`) instead of being added as download-only entries; they are also excluded from the
    message's downloadable `artifacts` refs. Binary artifacts keep the existing download path. The inline
    `content.ui_components` rendering is unchanged (dedupe collapses any overlap with the store-backed frames).
- **Tests (added/updated under `tests/`)**
  - `tests/unit/services/api.test.ts`: `fetchArtifactJson` (header on/off, id encoding, error paths) and
    `storedComponentToArtifactChunk` (shape conversion, strict `meta.artifact_id` override, defaults, null-on-no-component).
  - `tests/unit/stores/interactions.test.ts` (new file): dedupe keyed strictly on store `artifact_id`, both arrival
    orders, distinct ids render separately, inline-only chunks NOT deduped, `clear()` resets the set.
  - `tests/unit/services/chat-stream-sse.test.ts`: store-backed render (artifact mode) with backend-supplied
    placement, plus dedupe in BOTH arrival orders (inline-first->stored, stored-first->inline) rendering once.
  - `tests/unit/services/chat-stream-agui.test.ts`: store-backed render from the `CUSTOM` `artifact_stored` event.
  - `tests/unit/services/event-stream.test.ts`: store-backed render (fetch + placement) and binary-artifact
    download path unchanged (no fetch). Fixed the local FakeEventSource `_emit` to only fire `onmessage` for the
    default `message` event (matching the browser), so a single named frame isn't double-processed.
  - `tests/unit/services/session-stream.test.ts`: proactive RESULT with a `penguiflow_ui_component` artifact is
    fetched + rendered as a component (not a download), while the binary artifact stays downloadable.
- **`dist/`**: rebuilt with `npm run build` (vite). New `index.html` entry chunk (`index-CHCoV4W0.js`) contains the
  `penguiflow_ui_component` handler. Many hashed asset filenames changed (vite content-hashing); this is expected.

### Key Considerations

- **One render code path, one dedupe point.** Rather than scatter dedupe across the three stream services, all
  store-backed frames are converted to the inline `ArtifactChunkPayload` shape and pushed through
  `interactionsStore.addArtifactChunk`, which owns the dedupe. This guarantees decision 7 holds regardless of which
  frame arrives first and keeps each service handler thin.
- **Strict keying on the opaque store id.** Conversion always stamps `meta.artifact_id = <store id>` and the store
  dedupes only on that value, never on the component's `id`/`component_id`. In `both` mode the backend already stamps
  the same opaque id onto the inline chunk's `meta.artifact_id` (Phase 003, `rich_output/nodes.py:~500`), so the two
  channels collapse to one render.
- **Placement via backend frames (Phase 007), not a frontend guess.** The handlers read the backend-injected
  `message_id`/`default_message_id` first; the active-agent-message fallback only applies when the backend did not
  inject one (e.g. non-`penguiflow_ui_component` frames never reach this branch). For SSE/AG-UI this matches the
  inline path; for proactive results it attaches to the freshly-created proactive agent message.
- **Backward compatibility.** Binary/MCP `artifact_stored` events are untouched (still added to `artifactsStore` for
  download). Inline `artifact_chunk` -> `ui_component` rendering is untouched. `inline`-mode chunks have no store id
  so they are never deduped. All 413 pre-existing frontend tests stay green.
- **Plain `Set` vs `SvelteSet`.** The Svelte MCP autofixer reported `issues: []` (no correctness problems) but
  advised `SvelteSet`. I deliberately kept a plain `Set`: `renderedArtifactIds` is internal gating state never read in
  any template/`$derived`/`$effect`, so reactivity is unnecessary and would be wasteful. This matches the established
  codebase convention (e.g. `events.svelte.ts` uses a plain `new Set<string>()` for the same kind of non-reactive
  bookkeeping). This is the only autofixer suggestion not applied, and it is advisory only.

### Assumptions

- **Stored JSON shape.** `GET /artifacts/{id}` for a UI component returns the bytes written by
  `rich_output/nodes.py::_register_component_payload`: `{ id, component, props, title, summary, metadata }`, where
  `metadata` is the component `meta` dict (which contains `artifact_id` in `both` mode). The converter is defensive
  (coerces missing/wrong-typed fields), so minor shape drift degrades gracefully rather than throwing.
- **Namespace gate.** Store-backed UI components are identified by `source.namespace === "penguiflow_ui_component"`,
  matching the backend (`rich_output/nodes.py:557` and the playground emit gate `playground.py:~451`).
- **Session id is optional for the fetch.** The backend route allows unscoped access for backward-compat and
  validates when a session id is present; I pass the session id when the manager has one. For `event-stream` the
  trace-follow `sessionId` is used; for `session-stream` the `update.session_id` is used.
- **Proactive store-backed UI components arrive in `content.artifacts`** (with the `penguiflow_ui_component`
  namespace), since that is the only place session-stream sees stored artifacts; inline ones still arrive in
  `content.ui_components`. If a component is delivered via both in `both` mode, the store dedupe collapses them.

### Deviations from Plan

- **session-stream.ts**: the phase text says "same `artifact_stored` branch + dedupe", but session-stream does not
  process raw `artifact_stored` SSE frames — it processes proactive `state_update` RESULT updates whose
  `content.artifacts` may include store-backed UI components. I implemented the equivalent behavior there (fetch +
  render store-backed UI components, exclude them from downloadable refs), which is the faithful adaptation of the
  intent to that stream's actual shape.
- Added a small `asPlainRecord` helper in `api.ts` purely to keep the converter under the repo's lint complexity
  threshold (no behavior change). Correction (per subphase 008.2, re-measured authoritatively with
  `npx eslint <file> -f json | jq '.[0].warningCount'` on the pristine HEAD checkout vs the working tree): this
  phase added **+1** net-new advisory eslint warning in `session-stream.ts` (HEAD baseline **10** -> working tree
  **11**) and **0** net-new in `interactions.svelte.ts` (HEAD baseline **7** -> working tree **7**) — all warn-only
  rules: `complexity`, `max-depth`, `explicit-function-return-type`, `max-lines-per-function`. `eslint .` still
  exits 0 (these are warnings, not errors), so the gate is not failed by them. (Both 008's original "net new
  warnings: 0" claim and 008.1's "2 -> 12 / 2 -> 8 / +10 / +6" correction were inaccurate; the +10/+6 figures
  came from counting with `grep -c warning`, which also matches the trailing eslint summary line. The true
  net-new is +1 / 0.)
- Otherwise none.

### Potential Risks & Reviewer Attention Points

- **Async render ordering.** The store-backed render is async (fetch then `addArtifactChunk`). If, in `both` mode, the
  inline chunk and the `artifact_stored` frame arrive close together, the inline (synchronous) path will usually win
  and the later `artifact_stored` will be skipped by dedupe. If the `artifact_stored` is processed first, its async
  fetch resolves and renders, and the later inline chunk is skipped. Either way exactly one render results; only the
  *placement message id* differs by which path won (inline uses the active agent message; store-backed uses the
  backend frame id — which for SSE/AG-UI is the same active message). Worth a quick reviewer sanity check.
- **dist/ churn.** The vite rebuild rewrote ~135 hashed asset files. This is normal content-hashing, but the diff is
  large; the load-bearing change is the new `index-*.js` entry chunk referenced by `dist/index.html`.
- **Pre-existing `svelte-check` errors.** The pre-phase baseline (HEAD) is **14** `svelte-check` errors, all in
  pre-existing test files (`tests/unit/services/event-stream.test.ts`, `tests/unit/renderers/McpApp.test.ts`) from
  `noUncheckedIndexedAccess` on array indexing in tests. Correction (per subphase 008.1): the new Phase 008 test
  blocks originally added **2 net-new** such errors (working-tree count was 16, not 14, as the original note
  claimed). Those two `mockInstances[0]` accesses in `event-stream.test.ts` are now guarded with
  `const es = mockInstances[0]!;`, restoring the count to the **14** baseline (net-new = 0). The modified source
  files remain error-free under `svelte-check`, and `eslint` reports 0 errors.
- **Access control on unscoped fetch.** When no session id is available, the JSON fetch is unscoped (the backend
  allows it for backward-compat). This mirrors the existing `downloadArtifact` behavior and is not a regression.

### Files Modified

- `penguiflow/cli/playground_ui/src/lib/services/api.ts` (modified)
- `penguiflow/cli/playground_ui/src/lib/stores/features/interactions.svelte.ts` (modified)
- `penguiflow/cli/playground_ui/src/lib/services/chat-stream.ts` (modified)
- `penguiflow/cli/playground_ui/src/lib/services/event-stream.ts` (modified)
- `penguiflow/cli/playground_ui/src/lib/services/session-stream.ts` (modified)
- `penguiflow/cli/playground_ui/tests/unit/services/api.test.ts` (modified)
- `penguiflow/cli/playground_ui/tests/unit/services/chat-stream-sse.test.ts` (modified)
- `penguiflow/cli/playground_ui/tests/unit/services/chat-stream-agui.test.ts` (modified)
- `penguiflow/cli/playground_ui/tests/unit/services/event-stream.test.ts` (modified)
- `penguiflow/cli/playground_ui/tests/unit/services/session-stream.test.ts` (modified)
- `penguiflow/cli/playground_ui/tests/unit/stores/interactions.test.ts` (created)
- `penguiflow/cli/playground_ui/dist/**` (rebuilt via `npm run build`; new `index.html` entry + hashed assets)

### Verification Results

- `npm test` (playground_ui): **435 passed** (40 files); was 413 before -> +22 new tests, 0 failures.
- `npm run build` (vite): **success** (`dist/` rebuilt; new entry chunk contains the handler).
- `npx eslint <modified source files>`: **0 errors** (warn-only advisory rules; correction per subphase 008.2,
  measured via `npx eslint <file> -f json | jq '.[0].warningCount'` on HEAD vs working tree: net-new advisory
  warnings are `session-stream.ts` **10 -> 11** (net-new **+1**) and `interactions.svelte.ts` **7 -> 7**
  (net-new **0**) — `eslint .` still exits 0 since these are warnings, not errors).
- Svelte MCP autofixer on `interactions.svelte.ts`: **`issues: []`** (only the advisory `SvelteSet` suggestion,
  intentionally not applied — see Key Considerations).
- Backend: `uv run pytest tests/ -k "playground or rich_output or artifact"` -> **487 passed**, 0 failed.
- `uv run ruff check .` -> **All checks passed**; `uv run mypy` -> **Success: no issues found in 228 source files**
  (no Python changes; run for safety).
