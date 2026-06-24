# Unify UI-component artifacts into the ArtifactStore

## Context

PenguiFlow today has **two distinct things both called "artifact":**

1. **Planner `ArtifactRegistry`** (`penguiflow/planner/artifact_registry.py`) — an in-run, per-execution
   index. `build_*`/`render_*` rich-output tools register `ui_component` records here and the actual
   component payload lives **inline** in `self._payloads`. The short `artifact_N` refs the LLM uses come
   from here.
2. **`ArtifactStore`** (`penguiflow/artifacts.py`) — a persistent, scoped content backend
   (`put_bytes`/`put_text`, returns a compact `ArtifactRef`). General-purpose: it stores text/JSON, not
   just binary.

This split means two "artifact" concepts and two ref types. The goal is to make **every `ui_component`
payload persist to the `ArtifactStore`** and be referenced through it, while keeping the in-run registry
as a thin **index/cache** (short refs + LLM-facing metadata). Outcome: one artifact home, one external ref
concept, persistence/scoping/size-offload for UI payloads, and reliable resolution across HITL pause/resume.

The codebase is already half-way there, which keeps this change small:
- `list_artifacts` already merges registry **and** store results ("persistent store wins" dedup).
- `resolve_ref_async` already hydrates store-backed JSON via `_maybe_hydrate_stored_payload`, which expects
  exactly the stub shape `{"artifact": {"id": ...}}` we will write.
- `_register_component_payload` is the **single shared choke point** for both `build_*` and `render_*`.

### Design decisions (agreed)
- Registry stays as in-run **index/cache**; the store is the backing home. First resolve fetches from the
  store, then caches into `_payloads` (one-time async cost per ref per run).
- **Single source of truth = the full payload JSON in the store.** Keep `put_text` `meta` minimal (the
  `penguiflow_ui_component` namespace is the marker); do **not** copy display fields into `ArtifactRef.source`.
  The in-run record keeps `component`/`title`/`summary` exactly as it extracts them today, so `list_artifacts`
  needs no hydration — that is the pre-existing index, **not** new duplication. The only new persisted field
  is `record.artifact_id` (a single id). **No registry-side stub.**
- **Artifact ID is opaque** (store implementation detail). Always use the returned `ArtifactRef.id`; never
  construct/predict it. `namespace="penguiflow_ui_component"` is only a grouping hint.
- **Retention = option (a):** default store configured with no expiry/eviction. **No changes to
  `artifacts.py`.** (Accepted tradeoff: the in-memory default is **process-RAM only** — an `OrderedDict` of
  `bytes` on the store instance — so it grows unbounded for the process lifetime and is **not durable across
  a restart**. The unification gives uniformity + a swappable backend; true durability still requires
  configuring a real `ArtifactStore`.)
- **Default applies unconditionally (option A):** in-memory is the effective default even when an agent sets
  `artifact_store_enabled=False`; only an explicitly-passed `NoOpArtifactStore()` opts out.
- The visible-UI delivery path is unchanged: `render_*` still streams props inline via `artifact_chunk`.
  The store is durability/backing, not the frontend contract.

## Changes

### 0. (Phase 0 — isolated, pure rename, zero behavior change) Rename the registry
Rename the in-run planner artifact registry to **`InRunArtifactIndex`** so it no longer collides with
`ArtifactStore`. It's an internal symbol (not exported from `penguiflow/__init__.py`), so no public-API
break. Land this as its own commit *before* the logic changes below.

Rename map (across the ~17 files that reference it — `penguiflow/planner/*`, `penguiflow/rich_output/nodes.py`,
`tests/*`, live docs):
- class `ArtifactRegistry` → `InRunArtifactIndex`
- module `penguiflow/planner/artifact_registry.py` → `penguiflow/planner/artifact_index.py`
- function `get_artifact_registry` → `get_artifact_index`
- planner attribute `_artifact_registry` → `_artifact_index`
- local `registry` variables that hold this object → `index` (cosmetic; do for clarity)

**Do NOT rename — backward-compat contract:** the persisted snapshot key string `"artifact_registry"` in
`write_snapshot` (`metadata["artifact_registry"] = ...`) and its reads
`trajectory.metadata.get("artifact_registry")` (react_runtime.py L1000/L1062) stay **literally
`"artifact_registry"`** so trajectories persisted before the rename still resume.

Leave historical RFC docs under `docs/RFC/Done/` and `docs/RFC/ToDo/` as-is (archival records); update only
live docs (`docs/planner/rich-output.md`, `docs/tools/artifacts-guide.md`).

> The sections below reference the module/class by their **current** names (`artifact_registry.py`,
> `ArtifactRegistry`) to locate today's code; after Phase 0 they are `artifact_index.py` /
> `InRunArtifactIndex`.

### 1. Default artifact store → in-memory, no-expiry
`penguiflow/planner/react_init.py` (~L444-455, the 3-tier resolution).
- Replace the tier-3 `NoOpArtifactStore()` fallback with
  `InMemoryArtifactStore(ArtifactRetentionConfig(ttl_seconds=0, cleanup_strategy="none"))`.
- Keep tier-1 (explicit `artifact_store=`) and tier-2 (`discover_artifact_store`) precedence unchanged.
- **Decision (option A): the new default applies *unconditionally*.** The in-memory store becomes the
  effective default everywhere; the templates' `artifact_store_enabled=False` path (`_build_artifact_store`
  returns `None` → tier-3) now yields this InMemory store, **not** NoOp. There is no NoOp-by-default anymore.
  A caller who genuinely wants zero storage must pass an explicit `NoOpArtifactStore()` (tier-1); rich output
  still works for them via the write-through inline fallback (change #2). No template change is required.
- `InMemoryArtifactStore` already implements the full `ArtifactStore` protocol
  (`put_bytes`/`put_text`/`get`/`get_ref`/`delete`/`list`/`exists`), so no new store code is needed. The only
  non-protocol method any caller uses, `get_with_session_check`, is invoked behind a `hasattr` guard in the
  playground (L2163/L2217) and gracefully falls back when absent.

### 2. Write-through UI payloads to the store
`penguiflow/rich_output/nodes.py` — `_register_component_payload` (L493-523), the shared path for
`build_*` and `render_*`. **No registry-side stub — no field duplication.**
- The **store holds the full payload as the single source of truth**: when a usable store is present
  (`getattr(ctx, "artifacts", None)`, not a NoOp), JSON-serialize `{id, component, props, title, summary,
  metadata}` and `put_text(json, namespace="penguiflow_ui_component")`. Keep `meta` **minimal** — the
  namespace is the marker; do **not** copy display fields into `ArtifactRef.source`.
- Use the returned opaque `ref.id`; set `record.artifact_id = ref.id` (existing field, carried by
  `to_snapshot`). **This single id is the only new persisted data.**
- Leave the rest of registration unchanged: the in-run record still extracts `component`/`title`/
  `summary` from the payload (today's behavior — the index, not new duplication), and `_payloads[ref]` still
  holds the **full payload** as the in-run hot cache, so within-run composition stays a pure in-memory
  lookup (no store reads during the run).
- Fallback (robustness): wrap the `put` in try/except and treat **either no store or a *raising* store** as
  "not usable" → today's inline-only behavior (`artifact_id` unset). Required because the playground's
  `_DisabledArtifactStore.put_text` *raises* rather than no-ops (playground.py L1193-1200).

### 3. Resolve UI components from the store (incl. after resume)
`penguiflow/planner/artifact_registry.py` — `resolve_ref_async` (L367-401).
- In-run: `_payloads[ref]` hit → return as today (no store round-trip).
- Miss (e.g. after `from_snapshot`, `_payloads` empty) with `record.artifact_id` set: construct an
  **ephemeral** `{"artifact": {"id": record.artifact_id}}` pointer in memory, hand it to the existing
  `_maybe_hydrate_stored_payload`, and cache the fetched full payload back into `_payloads[ref]`. The pointer
  is never stored — it is only the call shape that helper expects.
- Keep `_payload_from_trajectory` as the no-store fallback.
- `build_*`/`render_*` already resolve children through `resolve_artifact_refs_async`
  (nodes.py L425-431) — no change there.

### 4. `list_artifacts` dedup
`penguiflow/rich_output/nodes.py` (L578-613).
- Records now carry `artifact_id == store id`. Align the registry/store dedup key to `artifact_id` so a UI
  component built this run isn't listed twice (the "persistent store wins" branch already exists).

### 5. Docs
`docs/planner/rich-output.md` (+ `rich-output-extensions.md`, `rich-output-skills.md`).
- Rewrite the boundary statements — "build_* are not persistence APIs" and "rich output does not replace
  binary artifacts/resources" no longer hold. Document the unified model: one `ArtifactStore` home,
  registry as in-run index/cache, opaque store IDs, `penguiflow_ui_component` namespace, no-TTL default
  store.

### 6. Playground interaction (expected behavior flip — verify, minimal code)
`penguiflow/cli/playground.py`.
- The playground gates "artifacts enabled" on the planner having a **non-`NoOp`** store
  (`_discover_artifact_store`, L1035/L1041). Change #1 flips the default from `NoOp` →
  `InMemoryArtifactStore`, so the playground moves from **artifacts-off-by-default** to
  **artifacts-on-by-default**. This is intended (the playground exists to exercise rich output), but is a
  behavior change to call out; with `ttl_seconds=0` its in-memory store grows for the process lifetime.
- No new playground code is required for the happy path: write-through goes to the planner's store; UI
  components surface via the existing artifact list/get endpoints (L2119-2239) and `artifact_stored` /
  `artifact_chunk` SSE events (L416-443); inline `artifact_chunk` delivery is unchanged.
- Only hard requirement: the write-through robustness in change #2 (tolerate a *raising*
  `_DisabledArtifactStore`).
- Reuse prior art in `docs/RFC/ToDo/issue-74/003-playground-hydration/` for any session-scoped hydration
  details.

## Verification
- `uv run pytest tests/ -k "rich_output or artifact"` — new + existing.
- New tests:
  - `build_table` → store roundtrip: full `props` offloaded to the store, `record.artifact_id` set, no
    inline duplication.
  - `render_grid`/`render_report` referencing a `build_*` `artifact_ref`: child hydrated from store,
    composed, emitted inline.
  - pause/resume: `snapshot()` → `from_snapshot()` → `resolve_ref_async` rehydrates the UI component from
    the store.
  - explicit `NoOpArtifactStore` / no-store: inline fallback still works.
  - default planner exposes an `InMemoryArtifactStore` with `ttl_seconds=0`.
- `uv run ruff check . && uv run mypy`.
- Manual: scaffold an `analyst` template (or `penguiflow dev`), have the planner `build_table` then
  `render_report` referencing it; confirm the artifact persists (store-backed id in `list_artifacts`) and
  the frontend still receives an inline `artifact_chunk`.
- Playground: confirm artifacts now show as enabled by default, the artifact list/get endpoints return the
  UI components, and `_DisabledArtifactStore` (explicitly-disabled agent) still falls back to inline without
  error.
- Coverage gate: `uv run pytest --cov=penguiflow --cov-fail-under=84.5`.

## Critical files
- Phase 0 (rename): `penguiflow/planner/artifact_registry.py` → `artifact_index.py`, plus the ~17 files that
  reference `ArtifactRegistry`/`get_artifact_registry`/`_artifact_registry` (`penguiflow/planner/*`,
  `penguiflow/rich_output/nodes.py`, `tests/*`, live docs). Snapshot key `"artifact_registry"` unchanged.
- `penguiflow/planner/react_init.py` — default in-memory, no-expiry store.
- `penguiflow/rich_output/nodes.py` — write-through in `_register_component_payload`; `list_artifacts` dedup.
- `penguiflow/planner/artifact_index.py` (renamed) — `resolve_ref_async` hydrates `ui_component` from store id.
- `docs/planner/rich-output.md` — updated boundaries / unified model.
- `penguiflow/cli/playground.py` — verify the artifacts-on-by-default flip; no happy-path code change.
- `tests/` — roundtrip, composition, resume, no-store fallback.
