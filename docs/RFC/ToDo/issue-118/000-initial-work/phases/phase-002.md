# Phase 002: Persist UI payloads to the store (opt-in modes only)

## Objective
Make `_register_component_payload` async and add a mode-gated store write so UI-component payloads persist to the
`ArtifactStore` in `both`/`artifact` modes. The full payload (incl. `props`) goes into the stored bytes (source of
truth for hydration); a light `component_data` descriptor goes into `ArtifactRef.source` (cheap for `list()`). The
opaque store id is copied onto the index record and a MANDATORY post-mutation snapshot persists it for resume.
`inline` mode is untouched — no store write, no mutation, today's single pre-mutation snapshot stays correct.

## Tasks
1. Make `_register_component_payload` `async def`; `await` it at the call site (`nodes.py:462`) and pass
   `ui_component_delivery` (read from `ctx._planner`).
2. Keep the existing `register_tool_artifact(...)` index registration unchanged (the in-run hot cache).
3. Add a store write through the plumbing proxy (`ctx._artifacts.put_text(...)`) only when
   `delivery in {"both","artifact"}`, with `emit=emit_visible`, `register=False`, `json.dumps(payload, default=str)`,
   namespace `"penguiflow_ui_component"`, and the light `component_data` descriptor in `meta`.
4. Copy `ref.id`/`ref.mime_type`/`ref.size_bytes` onto the index record and write a post-mutation snapshot.
5. Cast `ctx._artifacts` to the proxy type at the single call site so mypy accepts `emit`/`register`.

## Detailed Steps

### Step 1: Async signature + call site
- Change `def _register_component_payload(...)` to `async def _register_component_payload(...)`
  (`nodes.py:493-523`).
- Add an `emit_visible: bool` parameter (needed for `emit=emit_visible`) and a `delivery: str` parameter (read from
  the planner at the call site).
- At the call site (`nodes.py:461-472`), `await` the call and pass `emit_visible=emit_visible` and
  `delivery=<planner delivery>`. Read delivery as
  `getattr(getattr(ctx, "_planner", None), "_ui_component_delivery", "inline")`.

### Step 2: Keep index registration unchanged
- Leave the `register_tool_artifact(source_tool, "ui", payload, step_index=step_index)` call exactly as today; it
  still populates the in-run `_payloads[ref]` hot cache. `payload` is the SAME dict
  (`{"id","component","props","title","summary","metadata"}`).

### Step 3: Mode-gated store write (plumbing)
- After the index registration, when `delivery in {"both","artifact"}`, write through the proxy. Use a `cast` to the
  proxy type so mypy accepts `emit`/`register` (the static type of `ctx._artifacts` is the public protocol).
- Pass `json.dumps(payload, default=str)` (parity with inline emission — see notes), `mime_type="application/json"`,
  `namespace="penguiflow_ui_component"`, the light `component_data` descriptor in `meta`, `emit=emit_visible`,
  `register=False`.

### Step 4: Copy ids + MANDATORY post-mutation snapshot
- Set `record.artifact_id = ref.id`, `record.mime_type = ref.mime_type`, `record.size_bytes = ref.size_bytes`.
- Re-write the snapshot AFTER these mutations (move the existing `write_snapshot` below the mutation, or add a second
  one). Resume correctness depends on `record.artifact_id` being in the persisted snapshot.

## Required Code

```python
# Target file: penguiflow/rich_output/nodes.py  (call site ~nodes.py:461-473)
    artifact_ref: str | None = None
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
            delivery=getattr(getattr(ctx, "_planner", None), "_ui_component_delivery", "inline"),
        )
        artifact_ref = record.ref
```

```python
# Target file: penguiflow/rich_output/nodes.py  (replace _register_component_payload, nodes.py:493-523)
async def _register_component_payload(
    ctx: ToolContext,
    *,
    registry: Any,
    source_tool: str,
    component: str,
    props: Mapping[str, Any],
    component_id: str | None,
    title: str | None,
    meta: Mapping[str, Any],
    summary: str,
    emit_visible: bool,
    delivery: str,
) -> Any:
    trajectory = getattr(ctx, "_trajectory", None)
    step_index = len(getattr(trajectory, "steps", []) or [])
    payload = {
        "id": component_id,
        "component": component,
        "props": dict(props),
        "title": title,
        "summary": summary,
        "metadata": dict(meta),
    }
    record = registry.register_tool_artifact(
        source_tool,
        "ui",
        payload,
        step_index=step_index,
    )

    if delivery in {"both", "artifact"}:
        # Plumbing write: scope is stamped by the proxy; event gated by emit; we own the index entry (register=False).
        # cast() because ctx._artifacts is statically the public ArtifactStore protocol (no emit/register params),
        # but the concrete runtime object is _EventEmittingArtifactStoreProxy. Do NOT widen the public protocol.
        proxy = cast("_EventEmittingArtifactStoreProxy", ctx._artifacts)
        ref = await proxy.put_text(
            json.dumps(payload, default=str),     # FULL payload incl. props -> stored bytes (source of truth)
            mime_type="application/json",
            namespace="penguiflow_ui_component",
            meta={"component_data": {             # LIGHT descriptor (decision 3) -> ref.source
                "kind": "ui_component",
                "component": component,
                "title": title,
                "summary": summary,
                "metadata": dict(meta),
            }},
            emit=emit_visible,                    # render_* -> artifact_stored fires; build_* -> silent
            register=False,                       # proxy must NOT auto-register a phantom binary
        )
        record.artifact_id = ref.id
        record.mime_type = ref.mime_type
        record.size_bytes = ref.size_bytes

    # MANDATORY: snapshot AFTER the mutation so record.artifact_id persists for resume (Phase 004 depends on it).
    metadata_state = getattr(trajectory, "metadata", None)
    if isinstance(metadata_state, dict):
        registry.write_snapshot(metadata_state)
    return record
```

```python
# Target file: penguiflow/rich_output/nodes.py  (imports at top of file)
import json
from typing import cast
# and a TYPE_CHECKING import for the cast string target:
#   from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy
```

## Exit Criteria (Success)
- [ ] `_register_component_payload` is `async def` and the call site `await`s it.
- [ ] `delivery="artifact"`, `build_table`: the FULL payload (incl. `props`) is in the stored bytes;
      `component_data` (incl. `kind`, NO `props`) is in `ArtifactRef.source`; `record.artifact_id == ref.id`;
      `mime_type`/`size_bytes` are copied onto the index record.
- [ ] No phantom `kind="binary"` index record is created (the store write passes `register=False`).
- [ ] The store write is scoped (tenant/user/session/trace stamped by the proxy).
- [ ] `build_*` (silent, `emit_visible=False`) writes with `emit=False` so NO `artifact_stored` event fires.
- [ ] `props` never appears in `ArtifactRef.source` (only in the bytes).
- [ ] The snapshot taken AFTER the store write carries `record.artifact_id` (not `None`).
- [ ] `delivery="inline"`: NO store write, no mutation, no second snapshot path; the single pre-mutation snapshot is
      unchanged and `record.artifact_id is None`.
- [ ] `json.dumps(payload, default=str)` is used (not bare `json.dumps`) so `datetime`/`Decimal`/custom objects that
      inline mode tolerates do not raise.
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path.
- [ ] `mypy` passes (the cast resolves the proxy-vs-protocol typing).

## Implementation Notes
- Depends on Phase 000 (the `_ui_component_delivery` flag) and Phase 001 (proxy `emit`/`register` flags).
- `props` deliberately stays in the bytes, never in `source`: `list()` returns `source` for every artifact without
  downloading bytes, so heavy `props` in `source` would make every `list_artifacts` drag back every dataset.
- The store id (`record.artifact_id`) is the only new persisted index field; `to_snapshot` already carries it.
- Snapshot ordering is MANDATORY: the proxy's own `write_snapshot` is skipped because `register=False`, so the only
  re-persist of the mutated record is the post-mutation `write_snapshot` here. A pre-mutation-only snapshot would
  leave `artifact_id=None` in the snapshot and silently break Phase 004 resume hydration.
- JSON parity: inline emission normalizes with a `default=str` fallback (`artifact_handling.py:310-322`); using bare
  `json.dumps` here would make store-backed modes fail where inline succeeds.
- Per decision 6: inside any planner run `registry` is never `None` and `delivery` is only `both`/`artifact` when a
  planner exists, so the store write always runs in those modes. The `registry is None` branch is direct/test
  invocation, where `delivery` reads its `"inline"` default — nothing dropped.

## Verification Commands
```bash
# Store roundtrip: full payload incl. props in bytes; component_data (no props) in source; ids copied; no binary record; no event for build_*
uv run pytest tests/ -k "rich_output or artifact" -q

uv run ruff check . && uv run mypy
```
