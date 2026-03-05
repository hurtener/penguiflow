# Plan: Integrate persistent ArtifactStore into `list_artifacts` tool

## Context

The `list_artifacts` tool in `penguiflow/rich_output/nodes.py` only queries the in-run `ArtifactRegistry` (tracked during a planner ReAct loop). It never checks the persistent `ArtifactStore` (accessible via `ctx.artifacts`). This means binary artifacts stored via `ctx.artifacts.upload()` or `put_bytes()`/`put_text()` that weren't explicitly registered into the in-run registry are invisible to the `list_artifacts` tool — the LLM cannot discover or reference them.

This is the same class of bug as issue #83: the persistent artifact store is silently ignored in a code path that should use it as a fallback.

## Audit result

Audited all 109 `@tool` functions across the codebase. **`list_artifacts` is the only one with this issue.** Other artifact-interacting tools are correct:
- `render_component` (same file) — uses both `get_artifact_registry(ctx)` AND `ctx._artifacts`
- `web_fetch` (`common_tools/web/fetch.py`) — uses `ctx.artifacts` (public facade)
- `web_context` (`common_tools/web/specs.py`) — uses `ctx.artifacts` (public facade)

## Fix 1: `render_component` — switch from `ctx._artifacts` to `ctx.artifacts`

**Files:**
- `penguiflow/rich_output/nodes.py` — `render_component` (line 106)
- `penguiflow/planner/artifact_registry.py` — `_maybe_hydrate_stored_payload` (line 534)

`render_component` currently passes `ctx._artifacts` (the raw `ArtifactStore`) to `resolve_artifact_refs_async`. Other artifact-interacting tools (`web_fetch`, `web_context`) use the public `ctx.artifacts` facade (`ScopedArtifacts`). For consistency and proper scope checking, switch `render_component` to use `ctx.artifacts`.

**Problem:** `_maybe_hydrate_stored_payload` calls `.get()` on the artifact store, but `ScopedArtifacts` exposes `.download()` instead (same semantics: `bytes | None`).

**Change in `artifact_registry.py` line 534:**
```python
# Before:
get_fn = getattr(artifact_store, "get", None)
if not callable(get_fn):
    return None

# After:
get_fn = getattr(artifact_store, "get", None)
if not callable(get_fn):
    get_fn = getattr(artifact_store, "download", None)
if not callable(get_fn):
    return None
```

**Change in `nodes.py` line 106:**
```python
# Before:
artifact_store=getattr(ctx, "_artifacts", None),

# After:
artifact_store=getattr(ctx, "artifacts", None),
```

## Fix 2: `list_artifacts` — merge persistent ArtifactStore results

**File:** `penguiflow/rich_output/nodes.py` — `list_artifacts` function (line 176)

After the existing in-run registry query (line 202), add a fallback that queries the persistent `ArtifactStore` via the public `ctx.artifacts` facade (`ScopedArtifacts`). This facade already handles scoping correctly — its `list()` method uses `_read_scope` (tenant/user/session, excludes trace_id).

1. **Get the scoped facade:** `scoped = getattr(ctx, "artifacts", None)`
2. **List persistent artifacts:** `refs = await scoped.list()` — scope is automatic
3. **Deduplicate:** Build a set of `artifact_id` values already present in the registry results. Skip any `ArtifactRef` whose `id` is already in that set.
4. **Convert `ArtifactRef` → `ArtifactSummary`-compatible dict:** Map fields using the same pattern as `ArtifactRecord.to_public()`. Reuse `_binary_component_name` and `_binary_summary` helpers from `penguiflow/planner/artifact_registry.py`.
5. **Respect `kind` filter:** Persistent store artifacts are `"binary"`. If `kind` is set to `"ui_component"`, skip the persistent store query entirely.
6. **Wrap in try/except:** Best-effort — never fail listing due to store errors.

### Pseudocode

```python
# After line 202 (existing registry query)
# Merge artifacts from the persistent ArtifactStore (if available).
if kind is None or kind == "binary":
    scoped = getattr(ctx, "artifacts", None)
    if scoped is not None:
        try:
            refs = await scoped.list()
            seen_ids = {item.get("artifact_id") for item in items if item.get("artifact_id")}
            for ref in refs:
                if ref.id in seen_ids:
                    continue
                seen_ids.add(ref.id)
                items.append({
                    "ref": ref.id,
                    "kind": "binary",
                    "source_tool": ref.source.get("tool") if ref.source else None,
                    "component": _binary_component_name(ref.mime_type),
                    "title": ref.filename,
                    "summary": _binary_summary(ref),
                    "artifact_id": ref.id,
                    "mime_type": ref.mime_type,
                    "size_bytes": ref.size_bytes,
                    "created_step": None,
                    "renderable": bool(_binary_component_name(ref.mime_type) or ref.mime_type),
                    "metadata": {},
                })
        except Exception:
            pass  # Best-effort — never fail listing
```

### Imports to add

Add `_binary_component_name` and `_binary_summary` to the existing import block from `penguiflow.planner.artifact_registry` (line 12-16).

### Re-apply limit

After merging, if `args.limit` was specified, re-slice the combined `items` list to respect the limit (take the last N items, matching `list_records` behavior).

## Files to modify

- `penguiflow/rich_output/nodes.py` — `list_artifacts` function + import block, `render_component` line 106
- `penguiflow/planner/artifact_registry.py` — `_maybe_hydrate_stored_payload` line 534 (add `.download()` fallback)

## Tests

Add test(s) to verify the persistent store fallback in `list_artifacts`. Test pattern: create a `ToolContext` mock with `_artifacts` set to an `InMemoryArtifactStore` containing a stored artifact, and `_planner` set to `None` (so registry is None). Verify that `list_artifacts` returns the persistent artifact.

Also test the deduplication: when the registry has an artifact AND the persistent store has the same artifact, it should appear only once.

## Verification

1. `uv run ruff check penguiflow/rich_output/nodes.py`
2. `uv run mypy penguiflow/rich_output/nodes.py`
3. `uv run pytest tests/ -k "list_artifacts or rich_output"`
4. `uv run pytest` (full suite)
