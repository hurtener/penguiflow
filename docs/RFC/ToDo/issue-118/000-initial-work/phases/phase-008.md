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
