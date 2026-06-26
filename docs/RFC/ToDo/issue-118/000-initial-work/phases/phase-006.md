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

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-06-26

### Summary of Changes
- `penguiflow/rich_output/nodes.py`:
  - Added the `component_fields(component_data) -> dict[str, Any]` helper (placed right after
    `_summarise_component`). Returns exactly `{kind, component, title, summary, renderable}` per the Required Code,
    with `renderable=True` and no `source_tool`/`created_step` (decision 3).
  - Rewrote the `list_artifacts` Step 2 store pass:
    - Widened the gate to `if kind is None or kind == "binary" or kind == "ui_component":` (Step 3).
    - Replaced the unconditional "store-wins" dedup with a `kind`-branched, index-keyed dedup (Step 1): the store
      ref is matched against the in-run index entries by their opaque `artifact_id`. If the matching index entry is
      `kind == "ui_component"`, the store ref is SKIPPED (index wins, richer entry retained). Otherwise (binary) the
      index entry is dropped and the store entry is appended (today's store-wins, unchanged).
    - For a store ref NOT in the index, reads `ref.source.get("component_data")` (Step 2). If present, builds a rich
      `ui_component` entry via `component_fields(...)` with `source_tool=None`/`created_step=None` and
      `metadata = component_data.get("metadata", {}) or {}`. Otherwise falls back to today's mime-based `binary`
      entry via `_binary_component_name`/`_binary_summary`.
    - After building each derived entry, filters it by the requested `kind` (`entry["kind"] != kind`) AND by
      `args.source_tool` (`entry["source_tool"] != args.source_tool`).
- `tests/test_rich_output_nodes.py`:
  - Fixed `test_list_artifacts_deduplication_persistent_store_wins` so its setup reflects a genuine binary-vs-binary
    store-wins scenario (the actual regression guard the phase wants). See Deviations below.
  - Added five new tests covering the new behaviors: `test_list_artifacts_ui_component_dedup_index_wins`,
    `test_list_artifacts_store_only_ui_component_is_rich`,
    `test_list_artifacts_ui_component_kind_filter_surfaces_store`,
    `test_list_artifacts_binary_kind_filter_excludes_store_ui_component`,
    `test_list_artifacts_store_only_ui_component_ignores_source_tool_filter`.

### Key Considerations
- **`ArtifactSummary` has `extra="forbid"`** (`penguiflow/rich_output/tools.py`). Every key in the entry dict must be
  an exact `ArtifactSummary` field. The Required Code's `**fields` spread is safe because `component_fields` returns
  only valid fields (`kind`, `component`, `title`, `summary`, `renderable`).
- **`title` ordering fix.** The Required Code put `"title": fields["title"] or ref.filename` BEFORE `**fields`, which
  means `**fields` would override `title` with the raw `fields["title"]` (possibly `None`), losing the
  `or ref.filename` fallback. I moved the explicit `"title": fields["title"] or ref.filename` to AFTER `**fields` so
  the fallback actually wins. This is a behavior-preserving fix of an ordering bug in the Required Code; the resulting
  fields/values are otherwise identical to the spec. In practice `component_data` always carries a non-None `title`
  (Phase 002 always sets it), so this only matters for malformed/legacy descriptors, but it makes the intent correct.
- **Index dedup keyed on the index item's `kind`** (not the store ref's): this is the crux of Finding 1. A
  `ui_component` index record only ever gets `record.artifact_id` set when a real `component_data`-bearing store write
  happened (Phase 002), so matching by `artifact_id` and branching on the index `kind` is safe and precise.
- **Inline mode no-op:** in `inline` delivery, UI-component index records have `artifact_id=None` (Phase 002 only
  copies the store id in `both`/`artifact` modes). The `index_by_id` comprehension filters out entries without an
  `artifact_id`, so inline UI entries never match a store ref, and no `component_data` store write exists. The flip is
  therefore inert and `list_artifacts` output is byte-identical to pre-change on the default path (full suite green).
- **`_binary_component_name`/`_binary_summary`** remain the non-component fallback (imported from
  `artifact_registry`), so true binaries are unchanged.

### Assumptions
- `ref.source` is the dict produced from the store write's `meta` (confirmed: `InMemoryArtifactStore.put_text/put_bytes`
  do `source = dict(meta or {})`), so `ref.source["component_data"]` is exactly the LIGHT descriptor Phase 002 wrote.
  `scoped.list()` returns full `ArtifactRef`s including `source` without fetching bytes.
- A truthy `component_data` (`if component_data:`) is the correct presence test. Phase 002 always writes a non-empty
  dict, so an empty/missing descriptor correctly falls through to the binary branch.
- For store-only UI components, `metadata` should surface the persisted `component_data["metadata"]` descriptor
  (defaulting to `{}`). The Required Code does this; binaries keep `metadata={}` as before.
- The five exit-criteria behaviors are expected to be exercised by tests; since the pre-existing suite only had a
  binary-oriented store-wins test (with an incorrect premise — see Deviations), I added explicit coverage for each new
  behavior rather than leaving them untested.

### Deviations from Plan
- **Reworked `test_list_artifacts_deduplication_persistent_store_wins` instead of leaving it unchanged.** The phase
  says "do NOT rewrite the existing *inline* `list_artifacts` test to a new contract." This test is a *persistent
  store* dedup test (not the inline path), and its pre-existing setup was self-contradictory under the new contract:
  it registered a `ui_component` index record (via `register_tool_artifact` on `{"type":"echarts",...}`, which infers
  a component and yields `kind="ui_component"`) and then manually pointed `record.artifact_id` at a *binary* PNG store
  ref — a pairing that cannot occur in production (Phase 002 only sets `record.artifact_id` for real
  `component_data`-bearing writes). Under the new index-wins-for-`ui_component` rule, that scenario now correctly
  keeps the `ui_component` index entry, so the old `assert kind == "binary"` no longer holds. I updated the test's
  setup to register the SAME binary via `register_binary_artifact` (producing a genuine `kind="binary"` index record
  sharing the store id), which is the real "binary store-wins" regression guard the test name and docstring describe.
  The assertion (`len == 1` and `kind == "binary"`) is preserved. This is the faithful interpretation of the phase's
  binary regression-guard exit criterion.
- **Moved the `title` fallback after `**fields`** (see Key Considerations). Behavior-equivalent to the spec's intent,
  fixes an ordering bug.

### Potential Risks & Reviewer Attention Points
- **Finding-1 scope.** Confirm the dedup flip stays scoped to `ui_component`: binaries that appear in both the index
  and the store still resolve store-wins in every mode (covered by the reworked
  `test_list_artifacts_deduplication_persistent_store_wins`). A global flip would have changed surfaced binary entries
  even in `inline`, breaking the byte-identical guarantee.
- **`metadata` for store-only UI entries** now carries the persisted descriptor metadata
  (`component_data["metadata"]`), whereas binary store entries carry `{}`. This matches the Required Code; reviewers
  should confirm downstream consumers tolerate a populated `metadata` on store-only UI entries.
- **`source_tool` filter semantics.** Store-only UI components have `source_tool=None`, so a
  `list_artifacts(source_tool=...)` filter intentionally does NOT match them (decision 3). Covered by
  `test_list_artifacts_store_only_ui_component_ignores_source_tool_filter`.
- **Silent `build_*` components** (`emit_visible=False`) are surfaced cross-run with no visibility filter and no
  `emit_visible` flag (decision 4) — the store pass treats every `component_data`-bearing ref the same. No code path
  inspects an `emit_visible` flag.
- The pre-existing `test_list_artifacts_ui_component_kind_skips_persistent_store` (a plain binary upload + a
  `kind="ui_component"` filter) still passes: the widened gate now runs the store pass, but the derived binary entry
  is filtered out by `entry["kind"] != "ui_component"`, so the result is still empty.

### Verification Results
- `uv run pytest tests/ -k "rich_output or artifact or list_artifacts" -q` -> all pass (323 selected).
- `tests/test_rich_output_nodes.py` -> 48 pass.
- `uv run ruff check .` -> All checks passed.
- `uv run mypy` -> Success: no issues found in 228 source files.
- Full suite `uv run pytest` -> 2858 passed, 7 skipped.

### Files Modified
- `penguiflow/rich_output/nodes.py` (added `component_fields`; rewrote `list_artifacts` Step 2 store pass).
- `tests/test_rich_output_nodes.py` (reworked one store-wins test; added five new behavior tests).
