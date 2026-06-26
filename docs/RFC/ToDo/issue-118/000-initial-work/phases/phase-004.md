# Phase 004: Resolve UI components from the store on miss (resume path)

## Objective
Add a record-present hydration branch to `resolve_ref_async` so that, after `from_snapshot` on resume, a UI
component whose payload is no longer in the in-run `_payloads` cache can be rehydrated from the store via the
persisted `record.artifact_id`. Purely additive: in `inline` mode `artifact_id` is `None`, so the branch never
fires and resume uses today's path unchanged.

## Tasks
1. In `resolve_ref_async` (`artifact_registry.py:367-401`), after `payload = _payload_from_trajectory(...)` and
   BEFORE the existing `if payload is None: return None` guard, add a branch that synthesizes an ephemeral
   `{"artifact": {"id": record.artifact_id}}` pointer when `payload is None and record.artifact_id`.
2. Let the existing `_maybe_hydrate_stored_payload(...)` fetch and decode the full payload from the store.
3. Cache the hydrated payload back into `_payloads[ref]`.

## Detailed Steps

### Step 1: Insert the ephemeral-pointer branch
- Locate the block (`artifact_registry.py:387-391`):
  ```python
  payload = self._payloads.get(ref)
  if payload is None and trajectory is not None:
      payload = _payload_from_trajectory(trajectory, record)
  if payload is None:
      return None
  ```
- Between the `_payload_from_trajectory` assignment and the `if payload is None: return None` guard, insert:
  ```python
  if payload is None and record.artifact_id:
      payload = {"artifact": {"id": record.artifact_id}}   # ephemeral pointer for hydration
  ```

### Step 2: Confirm hydration handles the stub
- The existing `_maybe_hydrate_stored_payload(payload, artifact_store=...)` (`artifact_registry.py:521-549`) already
  handles a `{"artifact": {"id": ...}}` stub: it tries `getattr(artifact_store, "get", None)` then falls back to
  `getattr(artifact_store, "download", None)`. The object passed as `artifact_store` is `ctx.artifacts`
  (`nodes.py:430`), a `ScopedArtifacts`, which has only `.download` — so hydration runs through `.download`, which
  scope-checks (tenant/user/session, excluding `trace_id`), decodes UTF-8, and `json.loads` the bytes. No code change
  is needed in `_maybe_hydrate_stored_payload`; this phase only feeds it the stub.

### Step 3: Caching is already present
- The existing lines after `_maybe_hydrate_stored_payload` already cache the hydrated payload:
  `payload = hydrated; self._payloads[ref] = payload`. No change needed.

## Required Code

```python
# Target file: penguiflow/planner/artifact_registry.py  (inside resolve_ref_async, ~artifact_registry.py:387-391)
        payload = self._payloads.get(ref)
        if payload is None and trajectory is not None:
            payload = _payload_from_trajectory(trajectory, record)
        if payload is None and record.artifact_id:
            payload = {"artifact": {"id": record.artifact_id}}   # ephemeral pointer; hydrated from the store below
        if payload is None:
            return None

        hydrated = await _maybe_hydrate_stored_payload(payload, artifact_store=artifact_store)
        if hydrated is not None:
            payload = hydrated
            self._payloads[ref] = payload
```

## Exit Criteria (Success)
- [ ] After `snapshot()` -> `from_snapshot()`, `resolve_ref_async(ref, artifact_store=ctx.artifacts)` rehydrates the
      UI component from the store via `record.artifact_id` and returns a valid component payload.
- [ ] The stored bytes (full tool-payload dict with `component`+`props`) feed cleanly into
      `_component_payload_from_tool_payload`, producing a component payload with the original `component` and `props`.
- [ ] The new branch is inserted BEFORE the existing `if payload is None: return None` guard (so it can supply a
      payload before the early return).
- [ ] `inline` mode: `record.artifact_id is None`, so the branch is never taken; resume uses the unchanged
      `_payloads`/`_payload_from_trajectory`/snapshot path.
- [ ] Hydration is scope-checked (runs through `ScopedArtifacts.download` -> `_check_scope`).
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path.

## Implementation Notes
- Depends on Phase 002 — the persisted `record.artifact_id` (post-mutation snapshot) is what this branch reads. The
  resume test must assert the snapshot taken after the store write actually carries `artifact_id` (guards Phase 002's
  post-mutation `write_snapshot`); a snapshot with `artifact_id=None` makes this rehydration silently fail.
- This phase covers RESUME (record present after `from_snapshot`). Fresh cross-run reads (record ABSENT) are Phase
  005.
- The ephemeral pointer is never stored; the id inside it is the opaque `ArtifactRef.id` persisted in Phase 002.
- This branch lives AFTER the binary fast-path (`record.kind == "binary"` -> `_binary_component_payload`,
  `artifact_registry.py:384-385`), so binaries are unaffected.

## Verification Commands
```bash
# pause/resume rehydration; assert snapshot carries artifact_id
uv run pytest tests/ -k "rich_output or artifact or resume or snapshot" -q

uv run ruff check . && uv run mypy
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes
- `penguiflow/planner/artifact_registry.py` — Inside `resolve_ref_async`, inserted the ephemeral-pointer
  hydration branch BETWEEN the `_payload_from_trajectory` assignment and the existing
  `if payload is None: return None` guard:
  ```python
  if payload is None and record.artifact_id:
      # Resume path: the in-run payload cache was lost across from_snapshot, but
      # the persisted record carries an artifact_id. Synthesize an ephemeral
      # pointer so the full payload can be rehydrated from the store below.
      payload = {"artifact": {"id": record.artifact_id}}
  ```
  No other changes were needed: `_maybe_hydrate_stored_payload` already accepts the `{"artifact": {"id": ...}}`
  stub (falls back to `.download` when `.get` is absent), and the caching lines
  (`payload = hydrated; self._payloads[ref] = payload`) were already present.
- `tests/test_artifact_registry.py` — Added two regression tests (and a small `_ScopedStoreStub` exposing only
  `download`, matching `ScopedArtifacts`):
  - `test_resolve_ref_async_rehydrates_ui_component_from_store_after_snapshot` — exercises the full resume path:
    register a UI component, stamp `record.artifact_id` (as Phase 002 does post-store-write) BEFORE
    `snapshot()`, assert the snapshot carries `artifact_id` (guards Phase 002's post-mutation `write_snapshot`),
    `from_snapshot()` into a fresh registry (no `_payloads`), then `resolve_ref_async(..., artifact_store=stub)`
    rehydrates via `record.artifact_id`, returns the original `component`+`props`, runs through the store's
    `download`, and caches the hydrated payload back into `_payloads`.
  - `test_resolve_ref_async_inline_mode_never_hits_store` — `inline` mode (`artifact_id is None`): the branch is
    never taken, `resolve_ref_async` early-returns `None`, and the store is never touched.

### Key Considerations
- The branch is placed exactly as the phase prescribes — after the binary fast-path and after
  `_payload_from_trajectory`, but before the `if payload is None: return None` early return — so it can supply a
  payload before the early return without disturbing the existing fast paths.
- Verified the production wiring: `resolve_artifact_refs_async` (in `artifact_registry.py`) forwards
  `artifact_store` to `resolve_ref_async`, and the sole production caller, `rich_output/nodes.py:428-433`, passes
  `artifact_store=getattr(ctx, "artifacts", None)` — i.e. `ScopedArtifacts`, which only has `.download`. So
  `_maybe_hydrate_stored_payload` deterministically takes the `.download` path, which scope-checks
  (tenant/user/session, excluding `trace_id`), decodes UTF-8, and `json.loads` the bytes. The new test's stub
  intentionally exposes only `download` to mirror that exact resolution order.
- Verified the stored-bytes shape against Phase 002's `_register_component_payload`
  (`rich_output/nodes.py:520-556`): the stored bytes are `json.dumps(payload)` where `payload` is the full
  tool-payload dict (`component` + `props` + `id`/`title`/`summary`/`metadata`). On hydration this round-trips back
  through `_component_payload_from_tool_payload`, which extracts `component` + `props` — matching the exit criteria.
- Confirmed `from_snapshot` rebuilds `_records`/`_records_by_ref` but never repopulates `_payloads`, so on resume
  `_payloads.get(ref)` is `None` and the new branch fires whenever `record.artifact_id` is present. The test
  asserts `ref not in restored._payloads` to make that precondition explicit.

### Assumptions
- The opaque `record.artifact_id` persisted by Phase 002 is the `ArtifactRef.id` that the store's `download`/`get`
  accepts as its lookup key (the test constructs the stub keyed on that same id). This matches Phase 002's
  `record.artifact_id = ref.id` and the existing `_maybe_hydrate_stored_payload` which calls
  `get_fn(str(ref_dict["id"]))`.
- The new branch was authored as a strict no-op for `inline` (where `artifact_id is None`), per the plan's
  backwards-compat headline. The full suite passing unchanged on default confirms this.
- The phase Tasks/Required-Code section is strictly the code change. The phase Exit Criteria, however, describe
  observable resume behavior and explicitly call for the snapshot-carries-`artifact_id` guard, so I added the two
  focused unit tests above to demonstrate the criteria directly. No dedicated testing phase owns this resume
  rehydration test (phases 005-010 cover cross-run fallback, list_artifacts dedup, playground BE/FE, templates, and
  docs), so adding it here keeps the criteria verifiable without stepping on later phases.

### Deviations from Plan
- None to the production code (the inserted branch matches the Required Code verbatim, with an added explanatory
  comment).
- Additive only: I added two unit tests + a stub helper in `tests/test_artifact_registry.py` that were not
  literally enumerated in the phase's Tasks list, to satisfy the Exit Criteria (resume rehydration + the
  Phase-002 `artifact_id`-survives-snapshot guard). These are new tests; no existing tests were modified.

### Potential Risks & Reviewer Attention Points
- The branch keys solely on `record.artifact_id` being truthy. If a future change ever set `artifact_id` on a
  record whose payload is NOT a JSON-encoded tool-payload in the store, hydration would `download` and then either
  fail to `json.loads` (returns `None`, falls through to the existing `return None`) or return a dict that
  `_component_payload_from_tool_payload` can't interpret (also `None`). Both fail safe — no exception is raised
  mid-resolve. Within issue-118's scope, only Phase 002's UI-component store writes set `artifact_id` for
  non-binary records, so this is consistent.
- Binary records are unaffected: they return earlier via the `record.kind == "binary"` fast-path, before this
  branch.
- The two new tests use `restored._payloads` (a private attribute) to assert cache state. This is intentional and
  mirrors the existing test style in this module; it does couple the test to the internal cache name, which a
  reviewer may want to note.
- This phase covers RESUME only (record present after `from_snapshot`). Fresh cross-run reads with the record
  ABSENT are explicitly Phase 005 and are out of scope here.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/planner/artifact_registry.py` (modified)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_artifact_registry.py` (modified — 2 new tests + stub)

### Verification Results
- `uv run pytest tests/ -k "rich_output or artifact or resume or snapshot" -q` → **335 passed**, 2520 deselected.
- `uv run pytest tests/test_artifact_registry.py -q` → **4 passed** (2 existing + 2 new).
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy` → **Success: no issues found in 228 source files.**
- Full suite `uv run pytest` → **2848 passed, 7 skipped** (backwards-compat headline holds).
