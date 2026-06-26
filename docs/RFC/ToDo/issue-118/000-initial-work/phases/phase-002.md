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

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes
All changes are confined to `penguiflow/rich_output/nodes.py`:

- **Imports.** Added `TYPE_CHECKING` and `cast` to the `typing` import line, and a `TYPE_CHECKING`-guarded
  `from penguiflow.planner.artifact_handling import _EventEmittingArtifactStoreProxy` import (used only as the
  `cast` string target, so no runtime import cycle is introduced). `json` was already imported at the top.
- **Call site (in `_process_component_payload`).** Changed the call to `_register_component_payload(...)` to
  `await _register_component_payload(...)` and added two new keyword args:
  - `emit_visible=emit_visible` (already in scope in the caller).
  - `delivery=getattr(getattr(ctx, "_planner", None), "_ui_component_delivery", "inline")` — reads the Phase 000
    planner flag, defaulting to `"inline"` when there is no `_planner` (direct/test invocation).
- **`_register_component_payload`.** Converted from `def` to `async def`; added `emit_visible: bool` and
  `delivery: str` keyword params. The dict previously built inline for `register_tool_artifact` is now hoisted to a
  named `payload` local so the exact same dict (`{"id","component","props","title","summary","metadata"}`) is reused
  for both the index registration and the store write (single source of truth). Added a mode-gated store write
  (`delivery in {"both","artifact"}`) through `cast("_EventEmittingArtifactStoreProxy", ctx._artifacts).put_text(...)`
  with `json.dumps(payload, default=str)`, `mime_type="application/json"`, `namespace="penguiflow_ui_component"`, the
  light `component_data` descriptor in `meta`, `emit=emit_visible`, `register=False`. After the write, copies
  `ref.id`/`ref.mime_type`/`ref.size_bytes` onto the record. The existing `write_snapshot(metadata_state)` call was
  **moved below** the mutation so the persisted snapshot carries `record.artifact_id` (mandatory for resume).

### Key Considerations
- **Single snapshot, correctly ordered.** The plan allows either "move the existing `write_snapshot` below the
  mutation" or "add a second `write_snapshot`". I chose to **move** the single existing call below the mutation
  rather than add a second one. Rationale: the store write passes `register=False`, so the proxy never writes its
  own snapshot; there is no competing snapshot to preserve. In `inline` mode the mutation block is skipped, so the
  (now relocated) single snapshot fires exactly once and is byte-identical in effect to today's pre-mutation
  snapshot (nothing between `register_tool_artifact` and the snapshot mutates the record in `inline` mode). In
  store-backed modes it fires once, after `artifact_id` is set. This avoids a redundant double snapshot write while
  satisfying the "snapshot must carry `artifact_id`" requirement. This matches the exact code given in the phase
  file's "Required Code" block (which also uses a single, post-mutation snapshot).
- **`cast` over protocol widening.** Per the plan's mandatory typing decision, the public `ArtifactStore` protocol is
  left untouched; the proxy-only `emit`/`register` kwargs are reached via `cast(...)` at the single call site. mypy
  is satisfied and no third-party `ArtifactStore` implementer is forced to grow the new kwargs.
- **`json.dumps(payload, default=str)`.** Used the `default=str` fallback (not bare `json.dumps`) so store-backed
  modes tolerate the same `datetime`/`Decimal`/custom values that inline emission tolerates, per the JSON-parity note.
- **`props` only in bytes.** The full `payload` (incl. `props`) is the stored bytes; only the light `component_data`
  descriptor (`kind`/`component`/`title`/`summary`/`metadata`, no `props`) goes into `meta` → `ArtifactRef.source`.

### Assumptions
- **`ctx._artifacts` is always the proxy at runtime in store-backed modes.** Confirmed via
  `planner_context.py:_PlannerContext._artifacts`, which returns the `_EventEmittingArtifactStoreProxy`. The `cast`
  is sound because store-backed `delivery` only occurs inside a planner run (decision 6), where `ctx` is a
  `_PlannerContext`. In direct/test invocation `delivery` reads its `"inline"` default and the store-write branch is
  never entered, so the `cast` is never exercised against a non-proxy object.
- **`register_tool_artifact` returns a mutable `ArtifactRecord`** with assignable `artifact_id`/`mime_type`/
  `size_bytes` fields. Confirmed in `artifact_registry.py` (`ArtifactRecord` dataclass, lines ~20-65).
- **`ArtifactRef` exposes `.id`, `.mime_type`, `.size_bytes`.** Confirmed in `artifacts.py` (lines 58-71).
- **`InMemoryArtifactStore.put_text` records `namespace` and `scope` on the returned `ArtifactRef`, and `meta`
  becomes `ref.source` verbatim.** Confirmed by the ad-hoc verification harness (see below): `ref.namespace ==
  "penguiflow_ui_component"`, `ref.source["component_data"]` round-trips, `ref.scope.session_id == "s1"`.

### Deviations from Plan
None. The implementation matches the phase file's "Required Code" block exactly, adapted only to the current
line numbers (the call site and function had drifted by ~1 line vs. the plan's references). Minor cosmetic
formatting of the `meta={...}` literal was applied to satisfy ruff line-length (120) — semantically identical.

### Potential Risks & Reviewer Attention Points
- **Snapshot relocation is the load-bearing correctness change.** The single `write_snapshot` now runs *after* the
  record mutation. If a future edit reintroduces an early snapshot or skips the relocated one, resume hydration
  (Phase 004) would see `artifact_id=None`. A pause/resume test asserting the snapshot carries `artifact_id` (called
  out in the broader plan's verification list, but **not** part of this phase's scope) should guard this in a later
  phase.
- **Downstream phases not yet implemented.** This phase only adds the store *write* and id-copy. The read/hydration
  side (`resolve_ref_async` store fallback — plan changes 3/3a) and `list_artifacts` index-wins dedup (plan change 4)
  are *not* in this phase. Consequently, in `artifact` mode a freshly-built component is persisted and its id is on
  the record, but cross-run resolution and rich store-only `list_artifacts` rendering still behave as pre-change
  until their phases land. This is expected per the phase decomposition; nothing in this phase regresses the
  `inline` default.
- **`emit=emit_visible` for `both` mode.** This phase fires `artifact_stored` for `render_*` in both `both` and
  `artifact` modes. The `both`-mode inline-chunk id-threading (plan change 2c / decision 7) and the inline-vs-store
  emit gating (`if emit_visible and delivery in {"inline","both"}:`) are **not** part of this phase — they belong to
  a later phase. `_emit_component_artifact`'s guard is still `if emit_visible:` (unchanged here).

### Verification Results
- `uv run pytest tests/ -k "rich_output or artifact" -q` → **311 passed, 2542 deselected**.
- `uv run pytest tests/test_rich_output_nodes.py tests/test_rich_output_tools.py` → **63 passed**.
- Full suite `uv run pytest tests/` → **2846 passed, 7 skipped** (backwards-compat headline: existing `inline`
  suite green, unchanged).
- `uv run ruff check .` → **All checks passed!**
- `uv run mypy` → **Success: no issues found in 228 source files** (the `cast` resolves the proxy-vs-protocol typing).
- Ad-hoc behavioral harness (temporary, since this phase ships no new tests) asserted every store-backed exit
  criterion and was then removed:
  - `delivery="artifact"`, `build_table`: FULL payload incl. `props` in stored bytes; `component_data` (incl.
    `kind`, NO `props`) in `ArtifactRef.source`; `record.artifact_id == ref.id`; `mime_type=="application/json"` and
    `size_bytes` copied; no phantom `kind="binary"` index record; write scoped (`scope.session_id=="s1"`);
    `emit=False` so **no** `artifact_stored` event for silent `build_*`.
  - `delivery="artifact"`, `render_table`: exactly **one** `artifact_stored` event (`emit=True`).
  - `delivery="inline"`, `build_table`: **nothing** written to the store, `record.artifact_id is None`, no event.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/rich_output/nodes.py`
