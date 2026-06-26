# Phase 006: `list_artifacts` — index-wins dedup (ui_component only) + self-describing store entries

## Objective
Make `list_artifacts` surface store-backed UI components richly and dedupe them correctly, WITHOUT regressing
binaries. Flip the dedup to "index-wins" ONLY for `ui_component` index entries (keyed on the opaque `artifact_id`);
binaries keep today's "store-wins" in every mode (Finding 1). Render store-only UI components from
`source.component_data` (no byte fetch). Widen the store-pass gate so a `kind="ui_component"` filter runs the store
pass. Inert and byte-identical in `inline` mode.

## Tasks
1. Replace the unconditional "store wins" dedup (`nodes.py:587-591`) with a `kind`-branched version: `ui_component`
   index entry -> skip the store ref; binary index entry -> keep store-wins.
2. For a store ref NOT already in the index, read `ref.source.get("component_data")`; if present, build a rich
   `ui_component` entry; otherwise fall back to today's mime-based `binary` entry.
3. Widen the store-pass gate (`nodes.py:581`) so `kind == "ui_component"` also runs the store pass, then filter each
   derived entry by the requested kind.
4. Factor a single helper `component_fields(component_data)` shared by the store pass, with
   `_binary_component_name`/`_binary_summary` as the non-component fallback.

## Detailed Steps

### Step 1: `kind`-scoped index-wins dedup
- In the store-pass loop, when `ref.id` matches an existing index item's `artifact_id`, branch on THAT item's
  `kind`:
  - index item is `kind == "ui_component"` -> SKIP appending the store ref; keep the richer index entry (new
    behavior).
  - otherwise (binary) -> keep today's store-wins: drop the index entry, append the store entry.
- Safe because the index record's `artifact_id` was set to the exact id the store returned for that write.

### Step 2: Self-describing store entries from `source.component_data`
- For a store ref NOT in the index, read `cd = ref.source.get("component_data")`:
  - if `cd` is present, build a `ui_component` entry from `component_fields(cd)` (no byte fetch, no parsing).
  - else fall back to today's `binary` entry (`_binary_component_name(ref.mime_type)` / `_binary_summary(ref)`).

### Step 3: Widen the store-pass gate
- Change the gate (`nodes.py:581`) from `if kind is None or kind == "binary":` to also run for
  `kind == "ui_component"`. After building each derived entry, filter by the requested `kind` (so a
  `binary`-filtered list doesn't pick up a `ui_component` store entry and vice versa).

### Step 4: Factor `component_fields`
- Add a helper `component_fields(component_data) -> {kind, component, title, summary, renderable}` used by the store
  pass. Per decision 3 (minimal descriptor): it yields NO `source_tool` and NO `created_step`; store-only entries
  carry `source_tool=None`/`created_step=None`. Do NOT add those fields.
- Per decision 4 (silent builds listed cross-run): the store pass surfaces persisted `build_*`
  (`emit_visible=False`) components alongside `render_*` ones. Add NO visibility filter and persist NO `emit_visible`
  flag; treat every store-backed `ui_component` entry the same.

## Required Code

```python
# Target file: penguiflow/rich_output/nodes.py  (helper near _binary_component_name / _binary_summary)
def component_fields(component_data: Mapping[str, Any]) -> dict[str, Any]:
    """Rich fields for a store-only ui_component entry, derived from ArtifactRef.source['component_data'].

    Per decision 3 (minimal descriptor): no source_tool, no created_step.
    """
    return {
        "kind": component_data.get("kind", "ui_component"),
        "component": component_data.get("component"),
        "title": component_data.get("title"),
        "summary": component_data.get("summary"),
        "renderable": True,
    }
```

```python
# Target file: penguiflow/rich_output/nodes.py  (rewrite Step 2 store pass, nodes.py:580-610)
    # -- Step 2: Query persistent ArtifactStore (appended after registry) --
    if kind is None or kind == "binary" or kind == "ui_component":
        scoped = getattr(ctx, "artifacts", None)
        if scoped is not None:
            try:
                refs = await scoped.list()
                index_by_id = {
                    item.get("artifact_id"): item
                    for item in items
                    if item.get("artifact_id")
                }
                for ref in refs:
                    existing = index_by_id.get(ref.id)
                    if existing is not None:
                        # ui_component: index wins (richer entry) -> skip the store ref.
                        # binary: store wins (today's behavior) -> drop the index entry, append store entry.
                        if existing.get("kind") == "ui_component":
                            continue
                        items = [it for it in items if it.get("artifact_id") != ref.id]

                    source_tool = ref.source.get("tool")
                    component_data = ref.source.get("component_data")
                    if component_data:
                        fields = component_fields(component_data)
                        entry = {
                            "ref": ref.id,
                            "source_tool": None,        # decision 3: store-only entries have no source_tool
                            "title": fields["title"] or ref.filename,
                            "artifact_id": ref.id,
                            "mime_type": ref.mime_type,
                            "size_bytes": ref.size_bytes,
                            "created_step": None,       # decision 3
                            "metadata": component_data.get("metadata", {}) or {},
                            **fields,
                        }
                    else:
                        entry = {
                            "ref": ref.id,
                            "kind": "binary",
                            "source_tool": source_tool,
                            "component": _binary_component_name(ref.mime_type),
                            "title": ref.filename,
                            "summary": _binary_summary(ref),
                            "artifact_id": ref.id,
                            "mime_type": ref.mime_type,
                            "size_bytes": ref.size_bytes,
                            "created_step": None,
                            "renderable": True,
                            "metadata": {},
                        }

                    # Filter derived entry by the requested kind and source_tool.
                    if kind is not None and entry["kind"] != kind:
                        continue
                    if args.source_tool and entry["source_tool"] != args.source_tool:
                        continue
                    items.append(entry)
            except Exception as e:
                logger.debug("Failed to list persistent artifacts: %s", e, exc_info=True)
```

## Exit Criteria (Success)
- [ ] `list_artifacts` dedup: a UI component present in BOTH index and store lists ONCE (index entry wins, keyed by
      `artifact_id`).
- [ ] Binary dedup unchanged (regression guard, Finding 1): a binary present in both index and store still resolves
      STORE-WINS in every mode, with `created_step`/`source_tool`/`metadata` byte-identical to pre-change.
- [ ] Resume / cross-run: a store-only UI component lists as a RICH `ui_component` entry from `component_data`
      (correct `kind`/`component`/`title`/`summary`), NOT a `binary` entry.
- [ ] A `kind="ui_component"` filter on resume surfaces store-backed UI components (the widened gate); a
      `kind="binary"` filter does not pick them up.
- [ ] Store-only entries carry `source_tool=None` and `created_step=None` (decision 3); a
      `list_artifacts(source_tool=...)` filter does NOT match store-only UI components.
- [ ] Persisted silent `build_*` (`emit_visible=False`) components ARE surfaced cross-run (decision 4) — no
      visibility filter, no `emit_visible` flag.
- [ ] `inline` mode: UI-component index entries have `artifact_id=None`, never match a store ref, so the flip is a
      no-op; `list_artifacts` output is byte-identical to pre-change.
- [ ] The store-pass entry construction goes through the single `component_fields` helper so the two passes can't
      drift.
- [ ] Backwards-compat: the existing test suite passes unchanged on the default (`inline`) path; do NOT rewrite the
      existing inline `list_artifacts` test to a new contract.

## Implementation Notes
- Depends on Phase 002 (store writes carry `component_data` in `ref.source` and set `record.artifact_id` on the
  index entry).
- The flip MUST be scoped to `ui_component` (Finding 1): binaries traverse BOTH registry and store today in every
  mode (the proxy calls `register_binary_artifact` on every `put_*`), and the synthesized store entry's
  `created_step`/`source_tool`/`metadata` differ from the registry binary record's. A GLOBAL flip would change
  surfaced binary entries even in `inline`, breaking the byte-identical guarantee.
- Reading `component_data` from `source` avoids fetching bytes — `source` is returned by `list()` for every artifact
  without downloading.
- Keep `_binary_component_name`/`_binary_summary` as the non-component fallback so true binaries are unchanged.

## Verification Commands
```bash
# dedup (ui once), binary dedup unchanged (regression), store-only rich rendering, ui_component filter
uv run pytest tests/ -k "rich_output or artifact or list_artifacts" -q

uv run ruff check . && uv run mypy
```
