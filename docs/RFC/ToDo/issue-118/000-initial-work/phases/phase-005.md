# Phase 005: Cross-run store-only resolution (namespace-gated)

## Objective
Make store-only UI components genuinely composable across separate `planner.run()` calls in the same session
(decision 4). When `resolve_ref_async` is handed a bare opaque store id (the ref `list_artifacts` surfaced for a
store-only entry) and there is no in-run record, fall back to the store — BUT gate the fallback to artifacts this
feature actually wrote, via a scope-checked `get_metadata().namespace == "penguiflow_ui_component"` check (Finding 3).
Inert in `inline`/NoOp; typos still raise `Unknown artifact_ref`.

## Tasks
1. Replace the bare `if record is None: return None` early-return (`artifact_registry.py:381-383`) with a
   namespace-gated store-fallback.
2. Use `get_metadata(ref)` (if available) to confirm the artifact's namespace is `"penguiflow_ui_component"` before
   hydrating; otherwise return `None` (caller raises).
3. Hydrate via the existing `_maybe_hydrate_stored_payload({"artifact": {"id": ref}}, ...)` and convert with
   `_component_payload_from_tool_payload`.

## Detailed Steps

### Step 1: Replace the early return
- Replace (`artifact_registry.py:381-383`):
  ```python
  record = self._records_by_ref.get(ref)
  if record is None:
      return None
  ```
  with the namespace-gated store-fallback (see Required Code). The record lookup is still tried FIRST, so every
  in-run/resumed `artifact_N` resolves through the unchanged record-based path; only a true miss falls through.

### Step 2: Namespace gate
- `get_meta = getattr(artifact_store, "get_metadata", None)`; call it only if callable. If the returned ref is
  `None` or its `namespace` is not `"penguiflow_ui_component"`, `return None` (the caller —
  `resolve_artifact_refs_async`, `artifact_registry.py:474-481` — raises `RuntimeError(f"Unknown artifact_ref '{ref}'")`).
- `get_metadata` is itself scope-checked (`artifacts.py:329-336`); raw/NoOp stores lack it, so `meta_ref is None`
  and the gate is inert in `inline`.

### Step 3: Hydrate + convert
- On a passing gate, `hydrated = await _maybe_hydrate_stored_payload({"artifact": {"id": ref}}, artifact_store=...)`.
- If `hydrated is None`, `return None` (genuine miss -> caller raises).
- Otherwise `return _component_payload_from_tool_payload(hydrated)` (may be `None` if the bytes aren't a component).

## Required Code

```python
# Target file: penguiflow/planner/artifact_registry.py  (replace artifact_registry.py:381-383 in resolve_ref_async)
        record = self._records_by_ref.get(ref)
        if record is None:
            # Cross-run (record ABSENT): the ref the LLM was handed by list_artifacts IS the opaque store id.
            # GATE (Finding 3): only resolve refs we wrote under the penguiflow_ui_component namespace, so a
            # same-session non-UI JSON artifact shaped like {"component","props"} cannot be silently rendered.
            # get_metadata is itself scope-checked (tenant/user/session).
            get_meta = getattr(artifact_store, "get_metadata", None)
            meta_ref = await get_meta(ref) if callable(get_meta) else None
            if meta_ref is None or getattr(meta_ref, "namespace", None) != "penguiflow_ui_component":
                return None   # not ours / out of scope -> caller raises "Unknown artifact_ref"
            hydrated = await _maybe_hydrate_stored_payload({"artifact": {"id": ref}}, artifact_store=artifact_store)
            if hydrated is None:
                return None   # genuine miss -> caller raises "Unknown artifact_ref"
            return _component_payload_from_tool_payload(hydrated)   # may be None if bytes aren't a component
        # ... existing record-based path unchanged below (kind=="binary" fast path, then Phase 004 hydration) ...
```

## Exit Criteria (Success)
- [ ] Cross-run composition: with a FRESH registry (no snapshot restore) but the SAME session-scoped store, taking
      the store id surfaced by `list_artifacts` and resolving it as an `artifact_ref` returns a valid component
      payload (NOT `Unknown artifact_ref`). E.g. a new run's `render_report` references a prior run's `build_table`
      store id.
- [ ] Scope boundary: the same id under a DIFFERENT session scope resolves to `None`/raises (refused by
      `ScopedArtifacts.download` `_check_scope`).
- [ ] Namespace gate (Finding 3): a same-session JSON artifact shaped like `{"component","props"}` but written under
      a DIFFERENT namespace does NOT resolve as a UI component — `resolve_ref_async` returns `None` / caller raises,
      even though the bytes parse as a component.
- [ ] A genuinely bogus/hallucinated id still raises `Unknown artifact_ref` (loud failure, no silent wrong answer).
- [ ] `inline`/NoOp: the store wraps `NoOpArtifactStore`, `get_metadata` is absent -> `meta_ref is None` ->
      `return None`; no store-id refs are produced in inline mode anyway, so the fallback is inert.
- [ ] The record lookup (`_records_by_ref`) is still tried first; every in-run/resumed `artifact_N` resolves through
      the unchanged record-based path.
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path.

## Implementation Notes
- Depends on Phase 002 (store writes carry the `penguiflow_ui_component` namespace + the full payload bytes) and
  Phase 004 (the record-present hydration path; this phase is the record-ABSENT sibling).
- No new identifier is minted: store-only entries already expose `ref = ref.id` from `list_artifacts`
  (`nodes.py:596`). The fragile per-run `artifact_N` counter (`_next_ref`) is never reused cross-run.
- Lookup order is safe: a store id and an `artifact_N` never contend because the record lookup wins whenever a
  record exists.
- The record-present resume path (Phase 004) needs no namespace gate: a record with `artifact_id` set is one we
  created as `kind="ui_component"`, and binary records resolve via `_binary_component_payload` before that branch.
- Cross-run hydration is scope-checked (consequence of `.download`): resolves within the same tenant/user/SESSION and
  refuses across sessions — the intended "reusable building blocks within a session" boundary. Document it as such
  (Phase 010).
- Optional (NOT required): after a successful cross-run hydration, register an ephemeral record keyed by the store id
  to skip the round-trip on repeat references. Pure cache; omit unless profiling shows it matters.

## Verification Commands
```bash
# cross-run composition + scope boundary + namespace gate + bogus-id-raises
uv run pytest tests/ -k "rich_output or artifact or cross_run or namespace" -q

uv run ruff check . && uv run mypy
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes
- `penguiflow/planner/artifact_registry.py` — `ArtifactRegistry.resolve_ref_async`: replaced the bare
  `if record is None: return None` early-return with the namespace-gated cross-run store-fallback (Required Code,
  verbatim except for ruff E262 comment-spacing normalization — `#` instead of `#  `). The record lookup
  (`self._records_by_ref.get(ref)`) is still attempted first, so every in-run/resumed `artifact_N` continues to
  resolve through the unchanged record-based path (binary fast-path + Phase 004 hydration). Only a genuine record
  miss now falls through to the new branch.

### Key Considerations
- **Surgical edit.** The change is confined to the single record-ABSENT branch in `resolve_ref_async`. No other code
  paths, signatures, exports, or the synchronous `resolve_ref` were touched.
- **`resolve_ref` (sync) intentionally left unchanged.** The phase scopes the fallback to `resolve_ref_async` only —
  cross-run hydration requires an `await get_metadata(...)` / `await download(...)` round-trip, which the sync method
  cannot perform. The sync method keeps its original `if record is None: return None`, so a store-id ref passed to the
  sync path still raises `Unknown artifact_ref` (the async resolver is the one wired through
  `resolve_artifact_refs_async`, which is what receives a store id from `list_artifacts`).
- **Gate value confirmed against Phase 002.** The literal `"penguiflow_ui_component"` matches exactly the `namespace=`
  argument used by `_register_component_payload` in `penguiflow/rich_output/nodes.py:543` (the only writer of this
  namespace in the package — verified via grep). A drift between the two would silently break cross-run resolution, so
  this is the single load-bearing string.
- **`get_metadata` is the gate, not just metadata.** Verified in `penguiflow/artifacts.py:329-336` that
  `ScopedArtifacts.get_metadata` is itself `_check_scope`-protected: it returns `None` for refs outside the facade's
  tenant/user/session scope. This means the scope boundary AND the namespace gate are both enforced by the single
  `meta_ref is None or meta_ref.namespace != "penguiflow_ui_component"` check — a cross-session id yields
  `meta_ref is None` and is refused before any byte download.
- **`getattr(meta_ref, "namespace", None)` defensive access.** `ArtifactRef.namespace` is a declared
  `str | None` field (`artifacts.py:83`), so a plain `.namespace` would suffice for the real type, but `getattr`
  with a default keeps the gate robust against duck-typed/mock metadata objects and mirrors the Required Code exactly.

### Assumptions
- The store passed as `artifact_store` to `resolve_ref_async` is a `ScopedArtifacts`-like facade (already scope-bound
  to the current tenant/user/session) when running through the planner's `resolve_artifact_refs_async` plumbing. This
  is consistent with how Phase 004's resume hydration already uses the same `artifact_store` argument and
  `_maybe_hydrate_stored_payload`.
- Raw / NoOp stores (the `inline` path) do not expose a callable `get_metadata`; therefore
  `callable(get_meta)` is `False`, `meta_ref` is `None`, and the branch returns `None` — keeping the fallback inert in
  `inline` exactly as the exit criteria require. (No store-id refs are produced in `inline` mode in the first place,
  so this branch is doubly unreachable there.)
- `_maybe_hydrate_stored_payload({"artifact": {"id": ref}}, ...)` returns the parsed JSON component payload (full
  payload incl. `props`, written by Phase 002 as the store source-of-truth), and
  `_component_payload_from_tool_payload` converts it to the `{"component", "props"}` render shape. Both helpers are
  pre-existing and unchanged.

### Deviations from Plan
- None functionally. The only textual difference from the Required Code block is comment formatting: the Required Code
  used `#   not ours...` / `#   genuine miss...` / `#   may be None...` with multiple spaces after `#`, which trips
  ruff's E262 (inline-comment style). I normalized these to a single space after `#`. Logic, control flow, and the
  gate literal are identical to the spec.
- The optional ephemeral-record cache (Implementation Notes bullet in the phase, "NOT required") was intentionally
  omitted, as instructed. Each cross-run reference performs a fresh `get_metadata` + `download`; this is a pure
  round-trip cost, not a correctness issue, and can be added later if profiling warrants it.

### Potential Risks & Reviewer Attention Points
- **Lookup-order safety.** A store id and an `artifact_N` never contend: the record lookup wins whenever a record
  exists, so the new branch is reached only on a true record miss. Worth a reviewer's quick confirmation that no code
  path expects `resolve_ref_async` to return `None` for a store-id ref that this branch will now resolve (i.e. callers
  that previously relied on the miss to raise `Unknown artifact_ref` for legitimately-ours refs). I found none — the
  sole caller, `resolve_artifact_refs_async`, treats `None` as "raise" and a dict as "render", which is the intended
  new behavior.
- **Namespace is the only forgery defense.** A same-session JSON artifact whose bytes happen to parse as
  `{"component","props"}` but was written under a different namespace will be refused (gate returns `None`). This is
  Finding 3's whole point; reviewers should confirm no legitimate UI component is ever written under a different
  namespace (only `nodes.py:543` writes `penguiflow_ui_component`, so this holds today).
- **No new identifier minted.** Confirmed the `_next_ref` / `_counter` per-run counter is untouched and never reused
  cross-run; the cross-run ref is the opaque store id surfaced verbatim by `list_artifacts` (`nodes.py` list path).

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/artifact_registry.py` (modified)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-118/000-initial-work/phases/phase-005.md` (this notes section appended)

### Verification Results
- `uv run pytest tests/ -k "rich_output or artifact or cross_run or namespace" -q` — all pass (113 selected).
- `uv run pytest tests/ -k "planner or rich_output or artifact or resolve or resume"` — 547 passed, 1 skipped,
  0 failed (backwards-compat on the default `inline` path confirmed).
- `uv run ruff check .` — All checks passed.
- `uv run mypy` — Success: no issues found in 228 source files.
