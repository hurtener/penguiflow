# Persist UI-component artifacts to the ArtifactStore — opt-in, non-breaking

## Context

PenguiFlow has **two things both called "artifact":**

1. **In-run planner registry** (`penguiflow/planner/artifact_registry.py`, `ArtifactRegistry`) — a per-execution
   index. `build_*`/`render_*` rich-output tools register `ui_component` records here; the actual component
   payload lives **inline** in `self._payloads`. The short `artifact_N` refs the LLM uses come from here.
2. **`ArtifactStore`** (`penguiflow/artifacts.py`) — a persistent, scoped content backend (`put_bytes`/`put_text`,
   returns a compact `ArtifactRef`). General-purpose: stores text/JSON, not just binary.

The goal is to let **`ui_component` payloads persist to the `ArtifactStore`** so they can be delivered to the
frontend by **id** (frontend fetches the full payload from the store) and reliably resolved across HITL
pause/resume and across runs in a session. The in-run registry stays a thin **index/cache** over the same
store-backed artifacts.

### Hard requirement: this must be NON-BREAKING

An earlier revision of this plan made the unification mandatory (delete inline delivery, require a store, flip
the default store, rewrite `list_artifacts`). That is a **breaking** change to a wire/streaming contract plus a
default-behavior flip. **This revision makes the entire feature opt-in.** The default code path is byte-for-byte
what it is today; every new behavior sits behind one new planner flag, `ui_component_delivery`, which defaults
to `"inline"` (today's behavior). Net effect: a **minor** version bump (3.10.x → 3.11.0), additive, no migration
forced on anyone.

### The new flag: `ui_component_delivery` (tri-state)

`Literal["inline", "both", "artifact"]`, default `"inline"`.

| mode | inline `artifact_chunk` | persist to store | `artifact_stored` event |
|------|:-:|:-:|:-:|
| **`inline`** (default) | **yes (today)** | no | no |
| `both` | yes (legacy frontends keep working) | yes | yes (render_*) |
| `artifact` | no (suppressed) | yes | yes (render_*) |

- `inline` = exactly today. No store writes for UI components, no new events, NoOp-by-default preserved.
- `both` = migration runway: old frontends keep rendering from `artifact_chunk`; new frontends can switch to
  `artifact_stored` + fetch-by-id. A new additive event is non-breaking (consumers ignore unknown events).
- `artifact` = id-based delivery only.

"Visible" (`render_*`, `emit_visible=True`) vs "silent intermediate" (`build_*`, `emit_visible=False`) is
unchanged: `build_*` never emits `artifact_chunk` today (`nodes.py:475` guards on `emit_visible`) and never
fires `artifact_stored` (persists silently so `render_*` can compose it); only `render_*` is delivered.

### Verified facts this design rests on

- `react_init.py:444-455` — tier-3 store fallback is `NoOpArtifactStore()`; `_artifact_store` is therefore never
  `None`. Planner-flag convention is `*_enabled`/string kwargs (`pause_enabled` L70, `auto_seq_enabled` L100,
  etc.).
- `nodes.py:475-483` / `526-546` — `_emit_component_artifact` emits the inline `"ui"` `artifact_chunk`, called
  only when `emit_visible`. **Currently the only UI-component delivery mechanism.**
- `nodes.py:493-523` — `_register_component_payload` is **sync** today; single call site at `:462`.
- `nodes.py:554-617` — `list_artifacts`: Step 1 registry pass (`:562-578`, already lists UI components from the
  index), Step 2 store pass (`:580-610`) gated `kind is None or kind == "binary"`, dedup at `:587-591`
  ("persistent store wins"), store entry hardcodes `"kind": "binary"` (`:597`) with a mime-derived component
  name (`_binary_component_name`, `artifact_registry.py:638-645` → image/embed/markdown).
- `artifacts.py:652` — `put_bytes`/`put_text`'s `meta=` argument becomes `ArtifactRef.source` **verbatim**.
- `artifacts.py:725-730` — `InMemoryArtifactStore.list()` returns `ArtifactRef`s only; **never reads the stored
  bytes**. `source` is the cheap, always-returned channel; bytes are fetched on demand.
- `artifact_registry.py:16` — `ArtifactKind = Literal["ui_component", "binary", "tool_artifact"]`.
- `artifact_registry.py:184-217` — `register_tool_artifact(tool_name, field_name, payload, *, step_index, ...)`.
  The `"ui"` passed at `nodes.py:507` is the **`field_name`**, not the kind; the kind is computed at `:201`
  (`"ui_component" if component else "tool_artifact"`). Records expose `to_public()` (`:36-50`) with
  `artifact_id`, `mime_type`, `size_bytes` (all `None` for UI components today).
- `artifact_handling.py:22-138` — `_EventEmittingArtifactStoreProxy`: `put_text`/`put_bytes` call the store then
  `_emit_artifact_stored_event`, which **always** (1) `register_binary_artifact` (+ `write_snapshot`) and (2)
  emits `artifact_stored`. `_resolve_scope` stamps tenant/user/session/trace on every write.
- `planner_context.py:45-51,82-90` — `ctx._artifacts` = the proxy (plumbing); `ctx.artifacts` = `ScopedArtifacts`
  (porcelain). Proxy wraps `planner._artifact_store` + `planner._artifact_registry`.

### Terminology
- **Porcelain** = `ScopedArtifacts` (`ctx.artifacts.upload/download/list`), agent/tool-author facing. **Untouched.**
- **Plumbing** = the proxy (`ctx._artifacts.put_text/put_bytes/...`). First-party rich-output writes go here so
  scope is stamped and events are gated.

## Changes

### 1. New planner flag `ui_component_delivery`

`penguiflow/planner/react_init.py`.
- Add kwarg `ui_component_delivery: str = "inline"` (validate against `{"inline","both","artifact"}`), store as
  `planner._ui_component_delivery`. Follows the existing kwarg convention (`multi_action_*`, `auto_seq_enabled`).
- **Do NOT change the tier-3 default store.** Keep `NoOpArtifactStore()` (`:444-455`) for `inline`. There is **no
  unconditional InMemory default** and **no default flip** — that was the breaking part of the old plan.
- **Store required only in store-backed modes — raise at init (option b).** After the 3-tier store resolution, if
  `ui_component_delivery in {"both","artifact"}` and `planner._artifact_store is None or isinstance(...,
  NoOpArtifactStore)`, raise a clear `ValueError` telling the caller to pass an `artifact_store=` (e.g.
  `InMemoryArtifactStore()`). Fails fast at construction, never mid-stream. Because the raise is at init,
  `_register_component_payload` can assume a real store in store-backed mode (no `isinstance` check needed there).
  - *Optional refinement:* only raise when rich-output tools are actually present in the catalog (delivery is
    moot if rich output is disabled). Default to the simple form (raise whenever delivery is store-backed and no
    store); document it.

### 2. Persist UI payloads to the store (opt-in modes only) — `nodes.py`

#### 2a. `_register_component_payload` (`nodes.py:493-523`)
- Make it `async def`; update the call site (`nodes.py:462`) to `await _register_component_payload(...)` (caller
  is already async) and pass `ui_component_delivery` (read from `ctx._planner`).
- Keep the existing index registration unchanged (`register_tool_artifact(source_tool, "ui", payload, ...)` still
  runs, still populates `_payloads[ref]` — the in-run hot cache for within-run composition, no store reads during
  a live run).
- **Add a store write only when `delivery in {"both","artifact"}`**, through the **plumbing** (`ctx._artifacts`,
  so `_resolve_scope` stamps tenant/user/session/trace and the event is gated):
  ```python
  ref = await ctx._artifacts.put_text(
      json.dumps(payload),                       # FULL payload incl. props -> stored bytes (source of truth)
      mime_type="application/json",
      namespace="penguiflow_ui_component",
      meta={"component_data": {                   # LIGHT descriptor (known at write time) -> ref.source
          "kind": "ui_component",
          "component": component,
          "title": title,
          "summary": summary,
          "metadata": dict(meta),
      }},
      emit=emit_visible,                          # render_* -> artifact_stored fires; build_* -> silent
      register=False,                             # we own the index entry (Path A); proxy must NOT auto-register a binary
  )
  record.artifact_id = ref.id                     # opaque id, returned by the store
  record.mime_type = ref.mime_type                # so index-wins dedup (change 4) loses nothing
  record.size_bytes = ref.size_bytes
  ```
  - **`props` stays in the bytes, never in `source`.** `source` is returned by `list()` for every artifact at
    once without downloading bytes; putting the heavy `props` (table rows / chart series, KBs–MBs) there would
    make every `list_artifacts` call drag back every component's full dataset. `component_data` carries only the
    light, known-at-write-time fields. (This closes the old plan's one "open assumption.")
  - `record.artifact_id` is the only new persisted index field; `to_snapshot` already carries it.

#### 2b. Proxy: orthogonal `emit` / `register` flags — `artifact_handling.py:63-138`
Today `_emit_artifact_stored_event` conflates two concerns and assumes every write is a binary worth indexing.
Split them:
- Add two keyword flags to **both** `put_text` and `put_bytes`, each defaulting to `True` so **every existing
  caller (MCP/tool binaries, resources, web fetch, …) is unchanged**:
  - `emit: bool = True` → gate the `artifact_stored` event.
  - `register: bool = True` → gate the `register_binary_artifact` index bookkeeping (+ its `write_snapshot`).
- Rewrite `_emit_artifact_stored_event` (or inline two gated blocks) so each concern is independent — **no
  `namespace` comparison anywhere**:
  ```python
  ref = await self._store.put_text(..., scope=resolved_scope, ...)
  if register and self._registry is not None:
      self._registry.register_binary_artifact(ref, ...)
      ...write_snapshot...
  if emit:
      self._emit_event(PlannerEvent("artifact_stored", ...))
  return ref
  ```
- Rich-output passes `register=False` (it owns its index entry via `register_tool_artifact`) and
  `emit=emit_visible`. The phantom `kind="binary"` record is **never created** (not created-then-suppressed);
  `register_binary_artifact` is untouched and still correct for real binaries.

#### 2c. Keep inline delivery — gate it by mode (`nodes.py:475-483`)
- **Do NOT delete `_emit_component_artifact`** (`:526-546`). Change the guard from `if emit_visible:` to
  `if emit_visible and delivery in {"inline","both"}:`. So `inline` and `both` still emit the `"ui"`
  `artifact_chunk` exactly as today; `artifact` suppresses it.
- A `render_*` call outside an active run keeps today's inline behavior in `inline` mode (`:419-420`). In
  store-backed modes the planner-init raise already guaranteed a store, and a render outside a run is the same
  edge as today — keep inline as the fallback rather than introducing a new mid-stream raise.

### 3. Resolve UI components from the store on miss (store-backed modes) — `artifact_registry.py`
`resolve_ref_async` (server-side composition + resume; e.g. `render_*` referencing a `build_*` `artifact_ref`,
or rehydration after `from_snapshot`).
- In-run: `_payloads[ref]` hit → return as today (no store round-trip).
- Miss with `record.artifact_id` set (only happens in store-backed modes): construct an **ephemeral**
  `{"artifact": {"id": record.artifact_id}}` pointer in memory and hand it to the existing
  `_maybe_hydrate_stored_payload`, then cache the fetched full payload back into `_payloads[ref]`. The pointer is
  never stored; the id inside it is the opaque `ArtifactRef.id` we persisted.
- Purely additive: in `inline` mode `artifact_id` is `None`, so this branch is never taken and resume uses
  today's `_payloads`/`_payload_from_trajectory`/snapshot path unchanged.
- *(Confirm the exact helper/line range during implementation; it has not been re-read in this revision.)*

### 4. `list_artifacts` — index-wins dedup + self-describing store entries — `nodes.py:554-617`
Two changes, both inert in `inline` mode (no UI components are written to the store, so the store pass never sees
them and output is byte-identical to today):
- **Flip the dedup to index-wins, keyed on opaque `artifact_id`** (`:587-591`). Today it's "persistent store
  wins" (it deletes the index entry and appends the store entry → would downgrade a UI component to
  `kind="binary"`). Instead: if `ref.id` is already present among the index items' `artifact_id`s, **skip the
  store ref** and keep the richer index entry. Safe because the index record's `artifact_id` was set to the exact
  id the store returned for that write — matching ids mean the same stored object.
- **Render store-only UI components richly from `source`.** For a store ref **not** already in the index, read
  `ref.source.get("component_data")`. If present, build a rich `ui_component` entry directly from it
  (`kind`/`component`/`title`/`summary` — no byte fetch, no parsing, no inference). Otherwise fall back to today's
  mime-based `binary` entry for true binaries. This path matters only for resume / cross-run session reads (index
  empty for that id).
- Widen the store-pass gate (`:581`) so a `kind="ui_component"` filter also runs the store pass, then filter each
  derived entry by the requested kind. (Today the gate is `kind is None or kind == "binary"`, which would hide
  store-backed UI components from a `ui_component`-filtered list on resume.)
- Factor a single helper `component_fields(component_data) -> {kind, component, title, summary, renderable}` used
  by the store pass, with `_binary_component_name`/`_binary_summary` as the non-component fallback, so the two
  passes can't drift.

### 5. Docs — additive, not a rewrite
`docs/planner/rich-output.md` (+ `rich-output-extensions.md`, `rich-output-skills.md`),
`docs/tools/artifacts-guide.md`.
- **Add** a section documenting `ui_component_delivery` (default `inline`), the three modes, the id-based delivery
  contract (`artifact_stored` carries an opaque id; frontend fetches by id), the `penguiflow_ui_component`
  namespace + `component_data` descriptor, opaque store IDs (never predicted), and that store-backed modes
  **require** an `ArtifactStore` (raise at init).
- **Do not** rewrite "build_* are not persistence APIs" / "rich output does not replace binary artifacts" as
  false — they remain **true in the default (`inline`) mode**. Frame store-backing as opt-in.

### 6. Playground backend — `penguiflow/cli/playground.py`
- No forced default flip: with `inline` default + NoOp default store, the playground stays
  **artifacts-off-by-default** exactly as today. Setting `ui_component_delivery` to a store-backed mode (with a
  configured store) opts in.
- The `artifact_stored` SSE event and the artifact GET endpoints already exist (the rails). Verify they pass the
  `penguiflow_ui_component` namespace through and serve the stored JSON by id for UI components. No happy-path
  change expected beyond surfacing the new flag.
- The `artifact_chunk` SSE path (`:416`) stays for `inline`/`both`.

### 7. Playground frontend (Svelte) — `penguiflow/cli/playground_ui/src/` — additive
- **Keep** the existing inline `artifact_chunk` → `ui_component` rendering (`chat-stream.ts`, `event-stream.ts`,
  `session-stream.ts`). Do **not** remove it; `inline`/`both` rely on it. The committed `dist/` keeps working.
- **Add** an `artifact_stored` handler: when an `ArtifactStoredEvent` has
  `source.namespace === "penguiflow_ui_component"`, fetch the JSON by `artifact_id` (`api.ts` `GET /artifacts/{id}`
  already exists; the MCP-app path in `chat-stream.ts` is prior art) and render it in the same slot. Guard against
  double-render in `both` mode (dedupe by `artifact_id` against any inline-rendered component).
- Only needed to make store-backed modes usable end-to-end; not required for the core non-breaking change. Uses
  the Svelte MCP server; rebuild `dist/` (vite). Update affected unit tests.

### (Optional, deferred) Registry rename
Renaming `ArtifactRegistry` → `InRunArtifactIndex` (to avoid collision with `ArtifactStore`) is **out of scope**
for this change. It is internal-only (not exported from `penguiflow/__init__.py`), touches ~17 files, and is
orthogonal to the feature. It can land separately as a pure rename if desired; if done, the persisted snapshot
key `"artifact_registry"` must stay literal so prior trajectories resume.

## What was removed from the earlier (breaking) plan
- ❌ Deleting `_emit_component_artifact` / removing the inline `artifact_chunk` → **kept**, gated by mode.
- ❌ Unconditional `InMemoryArtifactStore` default + default flip → **removed**; NoOp default preserved for
  `inline`.
- ❌ "Store required, raise on NoOp" applied to all rich output → **scoped** to store-backed modes, enforced at
  init.
- ❌ AG-UI adapter regression (`agui_adapter/penguiflow.py:562` maps `artifact_chunk`) → **none**: `inline`/`both`
  still emit `artifact_chunk`, so AG-UI is unaffected.
- ❌ `list_artifacts` "persistent store wins → generic binary" for UI components → replaced by **index-wins**
  dedup; store entries render richly from `source` only on resume/cross-run.

## Verification
- **Backwards-compat is the headline test:** the entire existing suite must pass **unchanged** with the default
  (`inline`). Do **not** rewrite the existing inline-`artifact_chunk` / NoOp-graceful tests to a new contract.
- `uv run pytest tests/ -k "rich_output or artifact"`.
- New tests (store-backed modes):
  - `build_table` (`delivery="artifact"`) → store roundtrip: full payload incl. `props` in the stored **bytes**;
    `component_data` (incl. `kind`, no `props`) in `ArtifactRef.source`; `record.artifact_id == ref.id`;
    `mime_type`/`size_bytes` copied onto the index record; **no** phantom `kind="binary"` index record; write is
    **scoped**; `emit=False` so **no** `artifact_stored`.
  - `render_report` referencing a `build_*` `artifact_ref`: child hydrated from the store, composed/validated
    server-side; `emit=True` so one `artifact_stored` fires carrying the opaque id.
  - `delivery="inline"` (default): unchanged — `artifact_chunk` emitted, **nothing** written to the store, no
    `artifact_stored`; `list_artifacts` output identical to pre-change.
  - `delivery="both"`: both `artifact_chunk` **and** `artifact_stored` fire for `render_*`.
  - proxy `emit`/`register` independence: `register=False` writes no binary index record (event still fires when
    `emit=True`); `emit=False` emits no event (binary record still created when `register=True`); both default
    `True` (existing binary callers unchanged).
  - pause/resume + cross-run: `snapshot()` → `from_snapshot()` → `resolve_ref_async` rehydrates the UI component
    from the store via the opaque id; `list_artifacts` (empty index) lists it as a **rich `ui_component`** from
    `component_data`, not a binary.
  - `list_artifacts` dedup: same component present in index + store lists **once** (index entry wins, by
    `artifact_id`).
  - planner init: `ui_component_delivery="artifact"` with no/NoOp store raises a clear `ValueError`;
    `ui_component_delivery="inline"` with NoOp does not.
- `uv run ruff check . && uv run mypy`.
- Manual: scaffold `react`/`analyst` template, set delivery to a store-backed mode + an `InMemoryArtifactStore`,
  `build_table` then `render_report`; confirm the artifact persists (opaque id in `list_artifacts`) and the
  frontend receives `artifact_stored` for the `render_*` and fetches by id. Confirm default (`inline`) still
  renders inline with no store writes.
- Coverage gate: `uv run pytest --cov=penguiflow --cov-fail-under=84.5`.

## Template wiring (`penguiflow/templates/new/react/`)
Matches the existing pattern (`config.py.jinja` field → env in `from_env` → `ReactPlanner(...)` kwarg in
`planner.py.jinja`):
- `config.py.jinja`: add `ui_component_delivery: str = "inline"`; in `from_env`,
  `ui_component_delivery=os.getenv("UI_COMPONENT_DELIVERY", "inline")`. Add to `.env.example`.
- `planner.py.jinja` `build_planner`: pass `ui_component_delivery=config.ui_component_delivery` into
  `ReactPlanner(...)` (alongside `multi_action_*`).

## Critical files
- `penguiflow/planner/react_init.py` — new `ui_component_delivery` kwarg; init-time raise for store-backed modes;
  **NoOp default unchanged**.
- `penguiflow/rich_output/nodes.py` — async `_register_component_payload` with mode-gated store write through
  `ctx._artifacts.put_text(..., emit=emit_visible, register=False)`; **keep** `_emit_component_artifact`, gate its
  call by mode; index-wins dedup + self-describing store entries in `list_artifacts`.
- `penguiflow/planner/artifact_handling.py` — proxy `emit`/`register` flags (default `True`); no `namespace`
  comparison.
- `penguiflow/planner/artifact_registry.py` — `resolve_ref_async` hydrates UI components from the stored opaque id
  on miss (store-backed modes only).
- `docs/planner/rich-output.md` (+ siblings), `docs/tools/artifacts-guide.md` — additive: document the flag and
  the opt-in store-backed model.
- `penguiflow/cli/playground.py` — verify `artifact_stored` + GET endpoints serve UI components by id; no default
  flip.
- `penguiflow/cli/playground_ui/src/` (Svelte) — **add** `artifact_stored` + fetch-by-id; **keep** the inline
  path; rebuild `dist/`.
- `penguiflow/templates/new/react/src/__package_name__/{config,planner}.py.jinja` (+ `.env.example`) — surface the
  flag.
- `tests/` + `penguiflow/cli/playground_ui/tests/` — existing suite green on `inline`; new tests for store-backed
  modes, proxy flags, dedup, resume, init raise.
