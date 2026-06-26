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
