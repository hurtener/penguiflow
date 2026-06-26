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
forced on anyone. (Version finalization + CHANGELOG are **out of scope for this implementation** — see decision 5;
`pyproject.toml` already reads `3.11.0.dev1` on this branch and stays untouched.)

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

### Decisions resolved during plan verification (2026-06-25)
These were the open choices; an implementing agent must follow the resolved form, not re-litigate:
1. **Scope = full end-to-end.** All changes **1–7** are in scope for this implementation, including the Svelte
   playground frontend (`artifact_stored` handler + fetch-by-id + `dist/` rebuild, change 7) and the playground
   backend verification (change 6). Store-backed delivery must be usable end-to-end in the playground when done.
2. **Init raise = always (simple form).** In store-backed modes (`both`/`artifact`) with a NoOp/None store, raise
   the `ValueError` at planner init **unconditionally** — do **not** gate it on whether rich-output tools are
   present in the catalog.
3. **`component_data` descriptor = minimal.** Persist only `kind`/`component`/`title`/`summary`/`metadata` (the
   plan-as-written shape). Do **not** add `source_tool`/`created_step`. Accepted tradeoff: on resume / cross-run,
   store-only `list_artifacts` entries have `source_tool=None` and `created_step=None`, and
   `list_artifacts(source_tool=...)` will not match store-only UI components. (`source_tool` remains recoverable
   from `component_data.metadata.source_tool` if a future change needs it.)
4. **Silent `build_*` components are listed cross-run.** In store-backed modes, silently-built (`emit_visible=False`)
   components persist to the store and **intentionally surface** in resumed / cross-run `list_artifacts` as reusable
   building blocks. Do **not** add a visibility filter — no extra metadata, no `emit_visible` flag in
   `component_data`. **Cross-run composition is supported (see change 3a):** "reusable" means genuinely
   *resolvable* as an `artifact_ref` in a fresh run within the same session — `resolve_ref_async` falls back to the
   store, treating the store id as the ref. This composition is **scope-checked** (same tenant/user/session;
   refuses across sessions), because hydration goes through `ScopedArtifacts.download`.
5. **Versioning / CHANGELOG = out of scope.** Do **not** finalize the version or add a CHANGELOG entry as part of
   this implementation. `pyproject.toml` already carries `3.11.0.dev1` on this branch; **leave it untouched** and do
   **not** edit `CHANGELOG.md`. The release process owns version finalization and changelog separately. (The
   "3.11.0 minor bump" in Context describes the *eventual* release shape, not work to do here.)
6. **`registry is None` is unreachable in store-backed modes — no "render outside a run" special-casing.** Grounded
   in code: `react_init.py:457` sets `planner._artifact_registry = ArtifactRegistry()` **unconditionally** and
   `react.py:342` types it non-optional, so inside any planner run `get_artifact_registry(ctx)` is **never** `None`.
   `ui_component_delivery` lives **only** on the planner, so `delivery` can be `both`/`artifact` **only** when a
   planner (hence a registry) exists → change 2a always persists in those modes. The `registry is None` branches
   (`nodes.py:419-423`) fire **only** for direct/test invocation with a bare `ToolContext` that has no `_planner`;
   there `delivery` reads its `"inline"` default, so inline emission is correct and nothing is dropped. Keep change
   2c's guard exactly as `if emit_visible and delivery in {"inline","both"}:` — there is **no** store-backed
   render-outside-run case to handle and **no** new mid-stream raise.
7. **`both`-mode dedupe key = the opaque store id, threaded into the inline chunk.** `ArtifactRef` exposes `.id`
   (`artifacts.py:65`), surfaced as `artifact_id` throughout the planner (`record.artifact_id = ref.id`; the
   `artifact_stored` event's `extra["artifact_id"]`). In `both` mode `_register_component_payload` (`nodes.py:461`)
   sets `record.artifact_id = ref.id` **before** `_emit_component_artifact` (`nodes.py:475`) runs, so the store id is
   already available at inline-emit time. Thread it into the inline `artifact_chunk`'s `meta` so **both** channels
   carry the same `artifact_id`; the frontend dedupes strictly on that opaque id (never on the component's
   `id`/`component_id`). In `inline` mode there is no store write, so no id is threaded and nothing changes.

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

`penguiflow/planner/react_init.py` **and** `penguiflow/planner/react.py`.
- Add kwarg `ui_component_delivery: str = "inline"` (validate against `{"inline","both","artifact"}`), store as
  `planner._ui_component_delivery`. Follows the existing kwarg convention (`multi_action_*`, `auto_seq_enabled`).
- **Thread it through the public constructor — `react_init.py` alone is NOT enough.** `ReactPlanner.__init__`
  (`react.py:417-471`) has a **closed signature**: it lists every kwarg explicitly, copies each into
  `self._init_kwargs` (`react.py:484-536`), and forwards each into `_init_react_planner(...)` (`react.py:537-589`).
  If only `react_init.py` (i.e. `_init_react_planner`) gains the kwarg, `ReactPlanner(ui_component_delivery=...)`
  raises `TypeError: unexpected keyword argument`. So the flag must be added in **all four** places:
  1. `ReactPlanner.__init__` signature (`react.py:417-471`), defaulting to `"inline"`.
  2. The `self._init_kwargs` dict (`react.py:484-536`) — **required** so `fork()` (`react.py:608`, used for
     background tasks) reconstructs forked planners with the same delivery mode instead of silently reverting to
     `"inline"`.
  3. The `_init_react_planner(self, ..., ui_component_delivery=ui_component_delivery)` call (`react.py:537-589`).
  4. `_init_react_planner`'s signature in `react_init.py`, where validation + the init-time raise live.
- **Do NOT change the tier-3 default store.** Keep `NoOpArtifactStore()` (`:444-455`) for `inline`. There is **no
  unconditional InMemory default** and **no default flip** — that was the breaking part of the old plan.
- **Store required only in store-backed modes — raise at init (option b).** After the 3-tier store resolution, if
  `ui_component_delivery in {"both","artifact"}` and `planner._artifact_store is None or isinstance(...,
  NoOpArtifactStore)`, raise a clear `ValueError` telling the caller to pass an `artifact_store=` (e.g.
  `InMemoryArtifactStore()`). Fails fast at construction, never mid-stream. Because the raise is at init,
  `_register_component_payload` can assume a real store in store-backed mode (no `isinstance` check needed there).
  - **Resolved (decision 2): use the simple form — always raise** whenever delivery is store-backed and the
    resolved store is NoOp/None. Do **not** gate on catalog contents (no "only if rich-output tools present"
    refinement). Document the raise.

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
      json.dumps(payload, default=str),          # `payload` = the SAME dict handed to register_tool_artifact:
                                                 # {"id","component","props","title","summary","metadata"}.
                                                 # FULL payload incl. props -> stored bytes (source of truth);
                                                 # hydrates back via _component_payload_from_tool_payload (change 3).
                                                 # `default=str` MIRRORS inline emission (see note below).
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
  # MUST re-snapshot AFTER the mutation above (see "Snapshot ordering" below).
  metadata_state = getattr(trajectory, "metadata", None)
  if isinstance(metadata_state, dict):
      registry.write_snapshot(metadata_state)
  ```
  - **`props` stays in the bytes, never in `source`.** `source` is returned by `list()` for every artifact at
    once without downloading bytes; putting the heavy `props` (table rows / chart series, KBs–MBs) there would
    make every `list_artifacts` call drag back every component's full dataset. `component_data` carries only the
    light, known-at-write-time fields. (This closes the old plan's one "open assumption.")
  - `record.artifact_id` is the only new persisted index field; `to_snapshot` already carries it.
  - **Snapshot ordering (MANDATORY — resume correctness depends on it).** The current `_register_component_payload`
    calls `registry.write_snapshot(metadata_state)` at `nodes.py:520-522`, **immediately after**
    `register_tool_artifact` and **before** the new store write above. Because the store write passes
    `register=False`, the proxy's own `write_snapshot` (`artifact_handling.py:123-124`) is **skipped**, so nothing
    else re-persists the record. If the only snapshot is the pre-mutation one, `record.artifact_id` is `None` in the
    persisted snapshot → after `from_snapshot` on resume, change 3's hydration branch
    (`if payload is None and record.artifact_id:`) is never taken and the UI component **cannot** be rehydrated.
    Therefore: in store-backed modes, the snapshot MUST be (re)written **after** `record.artifact_id`/`mime_type`/
    `size_bytes` are set — either move the existing `write_snapshot` call below the mutation, or add the second
    `write_snapshot` shown above. The existing single pre-mutation snapshot stays correct for `inline` mode (no
    store write, no mutation), so only the store-backed path needs the post-mutation write.
  - **JSON serialization parity with inline (decision: mirror inline, do not diverge).** Inline emission normalizes
    payloads before sending (`artifact_handling.py:310-322` `_normalise_artifact_value` with a `default=str`
    fallback; rich-output size estimation also falls back to `str(payload)` at `rich_output/validate.py:108-113`).
    A bare `json.dumps(payload)` would raise on values inline mode tolerates (e.g. `datetime`, `Decimal`, custom
    objects), making store-backed modes **fail where inline succeeds**. Use `json.dumps(payload, default=str)` (as
    above) so both channels accept the same payloads. If stricter normalization is later desired, reuse the same
    `_normalise_artifact_value` helper for both registry and store writes rather than introducing a second behavior.

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

**Typing decision (MANDATORY — the mypy gate will fail otherwise).** The call site in change 2a writes through
`ctx._artifacts`, which is statically typed as the **public `ArtifactStore` protocol** (`context.py:85-87`), but
`ArtifactStore.put_text`/`put_bytes` (`artifacts.py:157-166`) have **no** `emit`/`register` params. The concrete
runtime object is `_EventEmittingArtifactStoreProxy`, but mypy checks against the protocol, so
`ctx._artifacts.put_text(..., emit=..., register=...)` is a type error under the current gate. **Resolution: keep
the public `ArtifactStore` protocol unchanged and cast at the single internal call site** to the proxy type, e.g.
`cast("_EventEmittingArtifactStoreProxy", ctx._artifacts).put_text(...)` (or a narrow private
`_EventGatedArtifactStore` Protocol that adds the two kwargs, used only for the cast). Do **not** widen the public
`ArtifactStore` protocol — that would force every third-party `ArtifactStore` implementation to grow `emit`/
`register`, re-introducing a breaking change. The cast is the narrowest fix and keeps the public surface stable.

#### 2c. Keep inline delivery — gate it by mode (`nodes.py:475-483`)
- **Do NOT delete `_emit_component_artifact`** (`:526-546`). Change the guard from `if emit_visible:` to
  `if emit_visible and delivery in {"inline","both"}:`. So `inline` and `both` still emit the `"ui"`
  `artifact_chunk` exactly as today; `artifact` suppresses it.
- **`both` mode — thread the store id into the inline chunk (decision 7).** When `delivery == "both"`, include the
  just-written `record.artifact_id` (= `ArtifactRef.id`) in the inline chunk's `meta` (e.g. `meta["artifact_id"]`)
  before calling `_emit_component_artifact`. `_register_component_payload` (`:461`) runs before this emit (`:475`),
  so `record.artifact_id` is already set. This gives the inline `artifact_chunk` and the `artifact_stored` event a
  **shared** opaque `artifact_id` so the frontend can dedupe (change 7). In `inline` mode there is no store write,
  so no id is threaded.
- **No "render outside a run" special-casing (decision 6).** Inside a planner run `registry` is always set
  (`react_init.py:457` sets `_artifact_registry` unconditionally; `react.py:342` types it non-optional), and
  `delivery` is only ever `both`/`artifact` when a planner exists — so change 2a always persists in those modes. The
  `registry is None` branches (`:419-423`) are reached only by direct/test invocation with no `_planner`, where
  `delivery` reads its `"inline"` default and inline emission is correct. Do **not** add a mid-stream raise or an
  inline fallback for `artifact` mode; the dropped-component scenario is unreachable.

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
- **Confirmed during verification** (`artifact_registry.py:367-401`): the new branch slots in *before* the
  existing `if payload is None: return None` guard (~`:391`). Concretely, after
  `payload = _payload_from_trajectory(...)` and before that guard, add:
  ```python
  if payload is None and record.artifact_id:
      payload = {"artifact": {"id": record.artifact_id}}   # ephemeral pointer for hydration
  ```
  The existing `_maybe_hydrate_stored_payload(payload, artifact_store=...)` (`:521-549`) already handles a
  `{"artifact": {"id": ...}}` stub. **Verified call path (not the plan's paraphrase):** it tries
  `getattr(artifact_store, "get", None)` first, then falls back to `getattr(artifact_store, "download", None)`
  (`:534-538`). The object passed as `artifact_store` is `ctx.artifacts` (`nodes.py:430`), which is a
  `ScopedArtifacts` (`artifacts.py:235`). **`ScopedArtifacts` has no `.get`** — only `.download` (`:320-327`) —
  so hydration runs through `.download`, which **scope-checks** (`_check_scope`, tenant/user/session, *excluding*
  `trace_id`, `:270-281`) before returning bytes. It then decodes UTF-8 and `json.loads` the bytes. So the
  **stored bytes must be the full tool-payload dict** — exactly the dict passed to `register_tool_artifact` in
  change 2a (`{"id","component","props","title","summary","metadata"}`) — so the hydrated result feeds cleanly into
  `_component_payload_from_tool_payload` (`:552-581`), which reads `.component` and `.props`.

#### 3a. Cross-run store-only resolution (RESOLVED — was an open decision)
Change 3 above (the `record.artifact_id` branch) covers **resume**: `from_snapshot` rebuilds the record, so
`resolve_ref_async` finds it in `_records_by_ref`, and — *provided change 2a's post-mutation snapshot lands so
`artifact_id` is persisted* — hydrates from the store. It does **not** cover a **fresh cross-run** read (a separate
`planner.run()` in the same session). There the registry is empty for that id, so `list_artifacts` surfaces the
store-only entry with the bare opaque **store id** as its `ref` (change 4 / `nodes.py:596`). Feeding that id back
as an `artifact_ref` is, *as the code stands today*, a hard failure — verified line-by-line:
- `resolve_artifact_refs_async` (`:474-481`) calls `resolve_ref_async(ref, ..., artifact_store=...)` and, on a
  `None` result, **raises** `RuntimeError(f"Unknown artifact_ref '{ref}'")`.
- `resolve_ref_async` (`:381-383`) does `record = self._records_by_ref.get(ref); if record is None: return None`
  **before any store logic** — so a store-id ref (no record) returns `None`, and the caller raises. The
  change-3 `record.artifact_id` branch lives *after* this early return and never executes for a store-id ref.

**Decision: make store-only components genuinely composable cross-run** (satisfies decision 4 literally), via a
small extension to `resolve_ref_async` — the ref *is* the store id, so no fresh-ref minting is needed, but a
**cheap scoped metadata check gates it to this feature's namespace** (see the "Namespace-gated" claim below;
this closes Finding 3 of the adversarial review). Replace the bare `if record is None: return None` (`:381-383`)
with a namespace-gated store-fallback:
```python
record = self._records_by_ref.get(ref)
if record is None:
    # Cross-run: the ref the LLM was handed by list_artifacts IS the opaque store id.
    # GATE (Finding 3): only resolve refs we wrote under the penguiflow_ui_component namespace.
    # Without this, ANY same-session JSON artifact shaped like {"component","props"} would
    # become silently renderable. get_metadata is scope-checked (tenant/user/session).
    get_meta = getattr(artifact_store, "get_metadata", None)
    meta_ref = await get_meta(ref) if callable(get_meta) else None
    if meta_ref is None or getattr(meta_ref, "namespace", None) != "penguiflow_ui_component":
        return None                                   # not ours / out of scope → caller raises "Unknown artifact_ref"
    hydrated = await _maybe_hydrate_stored_payload({"artifact": {"id": ref}}, artifact_store=artifact_store)
    if hydrated is None:
        return None                                   # genuine miss → caller raises "Unknown artifact_ref"
    return _component_payload_from_tool_payload(hydrated)   # may be None if the bytes aren't a component
# ... existing record-based path unchanged below ...
```
Why this is correct and non-breaking — each claim verified:
- **No new identifier.** Store-only entries already expose `ref = ref.id` (the store id) from `list_artifacts`
  (`nodes.py:596`); this branch resolves exactly that. The fragile per-run `artifact_N` is never reused
  cross-run (it's a per-run counter, `_next_ref` `:403-406`, and reusing it would collide with the new run's own
  `artifact_N`s).
- **Lookup order is safe.** `_records_by_ref` is tried first, so every in-run/resumed `artifact_N` resolves
  through the unchanged path; only a true miss falls through to the store. A store id and an `artifact_N` never
  contend because the record lookup wins whenever a record exists.
- **Inline/NoOp is inert.** In `inline` mode `artifact_store` wraps `NoOpArtifactStore`, so `.download` →
  `get_ref` → `None` → `_maybe_hydrate_stored_payload` returns `None` → `resolve_ref_async` returns `None`
  exactly as today. No store-id refs are ever produced in inline mode anyway (the store pass is empty).
- **Typos still fail loudly.** An unknown/hallucinated ref → `.download` → `get_ref` returns `None` → `None`
  → caller raises `Unknown artifact_ref`. No silent wrong answer, same as today.
- **Namespace-gated (Finding 3 — defense in depth).** `_maybe_hydrate_stored_payload` only downloads + JSON-decodes
  (`artifact_registry.py:521-549`) and `_component_payload_from_tool_payload` accepts **any** JSON with
  `component`+`props` (`:552-557`). Without a gate, a same-session *non-UI* JSON artifact of that shape (e.g. a tool
  that happened to store component-shaped JSON) would resolve as a UI component. The
  `get_metadata().namespace == "penguiflow_ui_component"` check restricts cross-run resolution to artifacts this
  feature actually wrote. `get_metadata` is itself scope-checked (`artifacts.py:329-336`), so the gate adds no new
  scope hole. Raw/NoOp stores lack `get_metadata` → `meta_ref is None` → `return None`, so the gate is inert in
  `inline` exactly like the rest of the fallback. (Change 3's *record-present* resume path needs no such gate: a
  record with `artifact_id` set is one we created as `kind="ui_component"`, and binary records resolve via
  `_binary_component_payload` before that branch — `artifact_registry.py:385`.)
- **Cross-run hydration is scope-checked** (consequence of using `.download`, see 3 above): it resolves within
  the same tenant/user/**session** and correctly refuses across sessions. This is the intended boundary —
  "reusable building blocks within a session," not a global artifact lake. Document it as such.

(Optional refinement, not required: after a successful cross-run hydration, register an ephemeral record keyed by
the store id so repeat references skip the round-trip. Pure cache; omit unless profiling shows it matters.)

### 4. `list_artifacts` — index-wins dedup (UI components only) + self-describing store entries — `nodes.py:554-617`
Both changes are inert in `inline` mode **and byte-identical for binaries in every mode** (see the scoping note
below — this is load-bearing for the non-breaking guarantee):
- **Flip the dedup to index-wins ONLY for `ui_component` index entries, keyed on opaque `artifact_id`** (`:587-591`).
  Today it's *unconditional* "persistent store wins" (delete the index entry, append the store entry → would
  downgrade a UI component to `kind="binary"`). Change it to: when `ref.id` matches an existing index item's
  `artifact_id`, branch on **that item's `kind`**:
  - index item is `kind == "ui_component"` → **skip the store ref**, keep the richer index entry (the new behavior).
  - otherwise (binary) → **keep today's store-wins behavior** (drop the index entry, append the store entry).

  Safe because the index record's `artifact_id` was set to the exact id the store returned for that write —
  matching ids mean the same stored object.
  - **Why the flip MUST be scoped to `ui_component` (Finding 1 of the adversarial review — do NOT make it global).**
    Binary artifacts traverse **both** the registry and the store **today, in every mode including `inline`**: the
    `_EventEmittingArtifactStoreProxy` calls `register_binary_artifact` on *every* `put_*`
    (`artifact_handling.py:116-124`), so an MCP / web-fetch / tool binary has both a registry record and a store
    ref with the same id. A **global** index-wins flip would therefore change the surfaced entry for binaries even
    in `inline` mode: the registry binary record carries real `created_step` / `source_tool` / `metadata`
    (`artifact_registry.py:326-339`) while the store-synthesized entry hardcodes
    `created_step=None` / `source_tool=ref.source.get("tool")` / `metadata={}` (`nodes.py:595-608`). Those fields
    differ, so a global flip would break the "`inline` output is byte-identical to today" guarantee and the
    `inline` verification test. Scoping the flip to `ui_component` makes it a genuine no-op for `inline`: there,
    UI-component index entries have `artifact_id=None` (no store write), so they never match a store ref, and the
    flip only ever fires in store-backed modes where a UI component legitimately lives in both the index and the
    store.
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
  passes can't drift. **Per decision 3 (minimal descriptor):** this helper deliberately yields no `source_tool`
  and no `created_step`; store-only entries carry `source_tool=None`/`created_step=None`, and a
  `list_artifacts(source_tool=...)` filter will not match store-only UI components on resume. Do not add those
  fields.
- **Per decision 4 (silent builds listed cross-run):** the store pass surfaces persisted `build_*`
  (`emit_visible=False`) components alongside `render_*` ones — they are reusable building blocks. Add **no**
  visibility filter and persist **no** `emit_visible` flag; treat every store-backed `ui_component` entry the same.

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
- No forced default flip: with the `inline` default, the planner writes **no UI components to the store** exactly as
  today, and fires no `artifact_stored` for UI components. Setting `ui_component_delivery` to a store-backed mode
  (with a configured store) opts in.
  - **Wording precision (do not over-claim "artifacts-off-by-default").** The playground is **not** globally
    artifacts-off: `load_agent()` already defaults to `InMemoryStateStore()` (`playground.py:773-775`), which exposes
    a real `PlaygroundArtifactStore`, and `_discover_artifact_store()` falls back to it when planner artifact storage
    is NoOp/unavailable (`playground.py:1023-1044`). So artifact **endpoints** and binary-artifact storage are live
    today regardless of this flag. The accurate claim is narrow: in `inline` mode the planner's **rich-output UI
    writes** stay off — not that the playground has no artifact storage.
- The `artifact_stored` SSE event and the artifact GET endpoints already exist (the rails). Verify they pass the
  `penguiflow_ui_component` namespace through and serve the stored JSON by id for UI components. No happy-path
  change expected beyond surfacing the new flag.
- The `artifact_chunk` SSE path (`:416`) stays for `inline`/`both`.

### 7. Playground frontend (Svelte) — `penguiflow/cli/playground_ui/src/` — additive
- **Keep** the existing inline `artifact_chunk` → `ui_component` rendering (`chat-stream.ts`, `event-stream.ts`,
  `session-stream.ts`). Do **not** remove it; `inline`/`both` rely on it. The committed `dist/` keeps working.
- **Add** an `artifact_stored` handler: when an `ArtifactStoredEvent` has
  `source.namespace === "penguiflow_ui_component"`, fetch the JSON by `artifact_id` (`api.ts` `GET /artifacts/{id}`
  already exists; the MCP-app path in `chat-stream.ts` is prior art) and render it in the same slot. **Dedupe in
  `both` mode (decision 7):** the inline `artifact_chunk` now carries the same opaque `artifact_id` in its `meta`
  (change 2c), so track rendered `artifact_id`s and skip an `artifact_stored` whose id was already rendered inline
  (and skip an inline chunk whose id already arrived via `artifact_stored`). Key **strictly** on the store
  `artifact_id` — never on the component's `id`/`component_id`.
- **This is more than "add a handler" — three concrete sub-tasks the implementer must not skip:**
  1. **JSON fetch + payload conversion.** Today `api.ts` (`:162-192`) only exposes artifact download as a
     **blob-triggered browser download**, and the current `artifact_stored` handlers (`chat-stream.ts:422-428`,
     `event-stream.ts:66-72`) only register a *downloadable* artifact. A new helper must fetch the JSON by id and
     convert it into the same `ArtifactChunkPayload`/`ComponentArtifact` shape the inline path feeds to
     `interactionsStore.addArtifactChunk(...)` (`interactions.svelte.ts:27-55`), so both paths render through one
     code path.
  2. **Message placement.** Inline component rendering is keyed to a message: `Message.svelte:31-37` filters
     component artifacts by `message_id`. But the live `/chat/stream` backend only injects `default_message_id`
     into **stream chunks**, **not** `artifact_stored` frames (`playground.py:386-414` vs `:430-443`). So an
     `artifact`-only component arrives with **no `message_id`** and has no slot to render in. **Pick one and state
     it:** (a) backend — include `message_id`/`default_message_id` on `artifact_stored` frames for the
     `penguiflow_ui_component` namespace (mirror the stream-chunk path at `playground.py:1622-1628`); or (b)
     frontend — attach the fetched component to the active `agentMsgId`. Option (a) is cleaner (the backend already
     knows the active message id) and is the recommended default.
  3. **Dedupe state** keyed on the opaque store `artifact_id` (per decision 7 above), surviving across the inline
     and `artifact_stored` arrival order.
- **In scope (decision 1: full end-to-end).** Required so store-backed modes are usable end-to-end in the
  playground. Uses the Svelte MCP server; rebuild `dist/` (vite). Update affected unit tests. (The non-breaking
  guarantee still holds: the inline path is kept, so `inline`/`both` frontends are unaffected.)

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
  dedup **for `ui_component` entries only** (binaries keep today's store-wins, so `inline` stays byte-identical —
  Finding 1); store entries render richly from `source` only on resume/cross-run.

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
  - `delivery="both"`: both `artifact_chunk` **and** `artifact_stored` fire for `render_*`, and the inline
    `artifact_chunk` `meta` carries the **same** `artifact_id` as the `artifact_stored` event (decision 7) so the
    frontend can dedupe on the opaque store id.
  - proxy `emit`/`register` independence: `register=False` writes no binary index record (event still fires when
    `emit=True`); `emit=False` emits no event (binary record still created when `register=True`); both default
    `True` (existing binary callers unchanged).
  - pause/resume (record present): `snapshot()` → `from_snapshot()` → `resolve_ref_async` rehydrates the UI
    component from the store via `record.artifact_id`; `list_artifacts` (empty index) lists it as a **rich
    `ui_component`** from `component_data`, not a binary. **Assert the snapshot taken after the store write actually
    carries `artifact_id`** (guards the post-mutation `write_snapshot`, change 2a) — a snapshot with
    `artifact_id=None` would make resume rehydration silently fail.
  - cross-run composition (record ABSENT — change 3a): with a **fresh registry** (no snapshot restore) but the
    **same session-scoped store**, take the store id surfaced by `list_artifacts` and resolve it as an
    `artifact_ref` (e.g. a new run's `render_report` referencing a prior run's `build_table` store id).
    `resolve_ref_async` must hydrate it via the store-fallback and return a valid component payload — **not**
    raise `Unknown artifact_ref`. Also assert the **scope boundary**: the same id under a *different* session
    scope resolves to `None`/raises (refused by `ScopedArtifacts.download`'s `_check_scope`). And a genuine
    bogus id still raises `Unknown artifact_ref`.
  - cross-run **namespace gate** (Finding 3): a same-session JSON artifact shaped like `{"component","props"}` but
    written under a **different namespace** (not `penguiflow_ui_component`) must **not** resolve as a UI component
    — `resolve_ref_async` returns `None` / the caller raises `Unknown artifact_ref`, even though the bytes parse as
    a component. Guards the `get_metadata().namespace` check.
  - `list_artifacts` dedup: same component present in index + store lists **once** (index entry wins, by
    `artifact_id`).
  - `list_artifacts` binary dedup unchanged (**regression guard for the scoped flip, Finding 1**): a binary
    artifact present in **both** index and store (e.g. an MCP tool output written through the proxy) still resolves
    **store-wins** in every mode, with `created_step` / `source_tool` / `metadata` byte-identical to pre-change.
    Proves the index-wins flip is scoped to `ui_component` and did not regress `inline`.
  - planner init: `ui_component_delivery="artifact"` with no/NoOp store raises a clear `ValueError`;
    `ui_component_delivery="inline"` with NoOp does not.
- `uv run ruff check . && uv run mypy`.
- Manual: scaffold `react`/`analyst` template, set delivery to a store-backed mode + an `InMemoryArtifactStore`,
  `build_table` then `render_report`; confirm the artifact persists (opaque id in `list_artifacts`) and the
  frontend receives `artifact_stored` for the `render_*` and fetches by id. Confirm default (`inline`) still
  renders inline with no store writes.
- Coverage gate: `uv run pytest --cov=penguiflow --cov-fail-under=84.5`.

## Template wiring
Matches the existing pattern (`config.py.jinja` field → env in `from_env` → `ReactPlanner(...)` kwarg in
`planner.py.jinja`). Because the flag defaults to `"inline"`, **un-wired templates keep working unchanged** — this
section only decides *which scaffolded projects expose the knob*, not correctness.

**Scope decision (resolve the react-vs-analyst inconsistency).** The earlier draft named only
`penguiflow/templates/new/react/`, but `penguiflow new` loads every template under `penguiflow.templates.new`
(`cli/new.py:123-131`) and several already wire a rich-output planner — `minimal`, `parallel`, `analyst`,
`wayfinder`, `rag_server` — and this plan's own Verification step scaffolds **`react`/`analyst`**. Surface the flag
consistently across **all `new/*` templates that construct a `ReactPlanner` with rich-output tools**, not just
`react`, so the knob isn't silently missing from `analyst` etc. For each:
- `config.py.jinja`: add `ui_component_delivery: str = "inline"`; in `from_env`,
  `ui_component_delivery=os.getenv("UI_COMPONENT_DELIVERY", "inline")`. Add to `.env.example`.
- `planner.py.jinja` (or `orchestrator.py.jinja` for `minimal`) `build_planner`: pass
  `ui_component_delivery=config.ui_component_delivery` into `ReactPlanner(...)` (alongside `multi_action_*`).

**Spec-driven generation** uses a *separate* template root: `penguiflow/cli/generate.py:822-835` renders
`penguiflow/cli/templates/planner.py.jinja` (which constructs `ReactPlanner(...)` at `:293-318`), **not**
`templates/new/*`. Decide explicitly: either surface the flag there too (same `config`/env/kwarg pattern) or state
that spec-driven projects are out of scope for this change. Recommended: include it, so the feature is consistent
across both generation surfaces. If excluded, say so here so it's a deliberate omission rather than an oversight.

## Critical files
- `penguiflow/planner/react.py` — thread `ui_component_delivery` through `ReactPlanner.__init__` signature,
  `_init_kwargs` (for `fork()`), and the `_init_react_planner(...)` call (change 1). **Without this the public
  constructor raises `TypeError`.**
- `penguiflow/planner/react_init.py` — new `ui_component_delivery` param on `_init_react_planner`; validation +
  init-time raise for store-backed modes; **NoOp default unchanged**.
- `penguiflow/rich_output/nodes.py` — async `_register_component_payload` with mode-gated store write through
  `ctx._artifacts.put_text(..., emit=emit_visible, register=False)` (cast to the proxy type for mypy), `json.dumps(
  payload, default=str)`, and a **post-mutation `write_snapshot`** so `record.artifact_id` persists for resume;
  **keep** `_emit_component_artifact`, gate its call by mode; **`ui_component`-scoped** index-wins dedup (binaries
  keep store-wins — Finding 1) + self-describing store entries in `list_artifacts`.
- `penguiflow/planner/artifact_handling.py` — proxy `emit`/`register` flags (default `True`); no `namespace`
  comparison. (Static type of the call site is the public `ArtifactStore` protocol — resolve via cast, see change 2b.)
- `penguiflow/planner/artifact_registry.py` — `resolve_ref_async`: (1) record-present hydration via
  `record.artifact_id` for resume (change 3), and (2) **record-absent store-fallback** that resolves a bare store
  id as the ref for cross-run composition (change 3a), **gated to the `penguiflow_ui_component` namespace via
  `get_metadata`** (Finding 3). Both reuse `_maybe_hydrate_stored_payload` → scope-checked
  `ScopedArtifacts.download`. Inert in `inline`/NoOp.
- `docs/planner/rich-output.md` (+ siblings), `docs/tools/artifacts-guide.md` — additive: document the flag and
  the opt-in store-backed model.
- `penguiflow/cli/playground.py` — verify `artifact_stored` + GET endpoints serve UI components by id; no default
  flip. **Decide message placement:** include `message_id` on `penguiflow_ui_component` `artifact_stored` frames
  (recommended) or handle placement frontend-side (change 7, sub-task 2).
- `penguiflow/cli/playground_ui/src/` (Svelte) — **add** `artifact_stored` + fetch-by-id (JSON fetch helper +
  payload conversion + dedupe on opaque `artifact_id` + message placement); **keep** the inline path; rebuild
  `dist/`.
- `penguiflow/templates/new/*/src/__package_name__/{config,planner}.py.jinja` (+ `.env.example`) — surface the flag
  across all rich-output templates (not just `react`); decide whether `penguiflow/cli/templates/` spec-driven
  generation is in scope (change "Template wiring").
- `tests/` + `penguiflow/cli/playground_ui/tests/` — existing suite green on `inline`; new tests for store-backed
  modes, proxy flags, dedup, resume (incl. **`artifact_id` survives snapshot/restore**), init raise.
