# Phase 003: Gate inline delivery by mode + thread store id (`both`)

## Objective
Keep the inline `artifact_chunk` delivery but gate it by mode: `inline` and `both` still emit it exactly as today;
`artifact` suppresses it. In `both` mode, thread the just-written store id (`record.artifact_id`) into the inline
chunk's `meta` so the inline `artifact_chunk` and the `artifact_stored` event share one opaque `artifact_id` for
frontend dedup (decision 7). `_emit_component_artifact` is NOT deleted.

## Tasks
1. Change the emit guard from `if emit_visible:` to `if emit_visible and delivery in {"inline","both"}:`.
2. In `both` mode, set `meta["artifact_id"] = record.artifact_id` before calling `_emit_component_artifact`.
3. Pass the (possibly augmented) `meta` and the `delivery` value through to the emit call.
4. Do NOT add any "render outside a run" special-casing or mid-stream raise (decision 6).

## Detailed Steps

### Step 1: Read delivery for the emit guard
- The `delivery` value is already computed at the `_register_component_payload` call site (Phase 002). Reuse the
  same value (read once from `getattr(getattr(ctx, "_planner", None), "_ui_component_delivery", "inline")`) for the
  emit guard. Store it in a local (e.g. `delivery`) before the `if registry is not None:` block so both the register
  call and the emit guard can use it.

### Step 2: Thread the store id in `both` mode
- When `delivery == "both"` and `record` exists with a non-`None` `record.artifact_id`, set
  `meta["artifact_id"] = record.artifact_id` BEFORE the `_emit_component_artifact(...)` call. `_register_component_payload`
  (Phase 002) ran first, so `record.artifact_id` is already set.
- `_emit_component_artifact` already forwards `meta=dict(meta)` to `ctx.emit_artifact` (`nodes.py:545`), so the
  threaded `artifact_id` flows into the inline chunk's `meta` with no signature change to `_emit_component_artifact`.

### Step 3: Gate the emit call
- Replace `if emit_visible:` (`nodes.py:475`) with `if emit_visible and delivery in {"inline","both"}:`.

## Required Code

```python
# Target file: penguiflow/rich_output/nodes.py
# Compute delivery once before the register/emit blocks (around nodes.py:460):
    delivery = getattr(getattr(ctx, "_planner", None), "_ui_component_delivery", "inline")

    artifact_ref: str | None = None
    record = None
    if registry is not None:
        record = await _register_component_payload(
            ctx,
            registry=registry,
            source_tool=source_tool,
            component=component,
            props=resolved_props,
            component_id=component_id,
            title=title,
            meta=meta,
            summary=summary,
            emit_visible=emit_visible,
            delivery=delivery,
        )
        artifact_ref = record.ref

    if emit_visible and delivery in {"inline", "both"}:
        if delivery == "both" and record is not None and record.artifact_id:
            # decision 7: share the opaque store id across both channels for frontend dedup.
            meta = {**meta, "artifact_id": record.artifact_id}
        await _emit_component_artifact(
            ctx,
            component=component,
            props=resolved_props,
            component_id=component_id,
            title=title,
            meta=meta,
        )
```

## Exit Criteria (Success)
- [ ] `delivery="inline"`: `_emit_component_artifact` still fires for `render_*` (`emit_visible=True`) exactly as
      today; the inline chunk `meta` carries NO `artifact_id`.
- [ ] `delivery="both"`: both the inline `artifact_chunk` AND `artifact_stored` fire for `render_*`, and the inline
      chunk's `meta["artifact_id"]` equals the `artifact_stored` event's `extra["artifact_id"]` (same opaque id).
- [ ] `delivery="artifact"`: the inline `artifact_chunk` is suppressed (`_emit_component_artifact` not called);
      `artifact_stored` still fires for `render_*` via the Phase 002 store write.
- [ ] `build_*` (`emit_visible=False`) emits NO inline chunk in any mode (the `emit_visible` half of the guard).
- [ ] `_emit_component_artifact` still exists and is unchanged in signature.
- [ ] No mid-stream raise and no inline fallback added for `artifact` mode (decision 6).
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path; AG-UI adapter is
      unaffected (`inline`/`both` still emit `artifact_chunk`).

## Implementation Notes
- Depends on Phase 000 (the flag) and Phase 002 (`record.artifact_id` is set before this emit runs).
- Copy-on-write `meta` (`meta = {**meta, "artifact_id": ...}`) avoids mutating the dict that may be referenced
  elsewhere; only the inline emit path sees the threaded id.
- Per decision 7, dedup is strictly on the opaque store `artifact_id`, never on the component's `id`/`component_id`.
- The `registry is None` branches (`nodes.py:419-423`) are reached only by direct/test invocation with no `_planner`;
  there `delivery` reads `"inline"` and inline emission is correct — no special-casing required.
- AG-UI (`agui_adapter/penguiflow.py:562` maps `artifact_chunk`) is intentionally unaffected because `inline`/`both`
  still emit the chunk.

## Verification Commands
```bash
# both-mode shared-id + artifact-mode suppression + inline unchanged
uv run pytest tests/ -k "rich_output or artifact" -q

uv run ruff check . && uv run mypy
```
