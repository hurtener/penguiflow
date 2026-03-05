# Phase 001: `list_artifacts` -- merge persistent ArtifactStore results

## Objective

The `list_artifacts` tool currently only queries the in-run `ArtifactRegistry`. It never checks the persistent `ArtifactStore` accessible via `ctx.artifacts`. This means binary artifacts stored via `ctx.artifacts.upload()` or `put_bytes()`/`put_text()` that were not explicitly registered into the in-run registry are invisible to the LLM. This phase rewrites `list_artifacts` to query the persistent store as a fallback after the in-run registry, with deduplication, filtering, and error handling.

## Tasks
1. Add `import logging` and a module logger to `penguiflow/rich_output/nodes.py`.
2. Add `_binary_component_name` and `_binary_summary` to the import block from `penguiflow.planner.artifact_registry`.
3. Rewrite the `list_artifacts` function to query both the in-run registry and the persistent store.

## Detailed Steps

### Step 1: Add logging to `penguiflow/rich_output/nodes.py`

- Add `import logging` after the existing `import json` line (around line 6).
- Add `logger = logging.getLogger(__name__)` after the import block, before the `_ensure_enabled` function definition. Place it around line 34 (after the `from .validate import RichOutputValidationError` import).

### Step 2: Add helper imports from `artifact_registry`

- Locate the import block from `penguiflow.planner.artifact_registry` (lines 12-16):
  ```python
  from penguiflow.planner.artifact_registry import (
      get_artifact_registry,
      has_artifact_refs,
      resolve_artifact_refs_async,
  )
  ```
- Add `_binary_component_name` and `_binary_summary` to this import block:
  ```python
  from penguiflow.planner.artifact_registry import (
      _binary_component_name,
      _binary_summary,
      get_artifact_registry,
      has_artifact_refs,
      resolve_artifact_refs_async,
  )
  ```
- These are private (`_`-prefixed) helper functions. Importing them cross-module is intentional since `nodes.py` and `artifact_registry.py` are closely-related internal modules within the same package.

### Step 3: Rewrite the `list_artifacts` function

- Replace the entire `list_artifacts` function body (lines 176-203) with the new implementation.
- **Key changes from the current code:**
  - Remove the early return at lines 179-180 (`if registry is None: return ...`). The persistent store must be queried regardless of whether a registry exists.
  - Call `registry.list_records()` WITHOUT `limit=` (limit is applied at the end on the combined list).
  - After registry query, query the persistent `ArtifactStore` via `ctx.artifacts.list()`.
  - Only query the persistent store when `kind` is `None` or `"binary"` (persistent store artifacts are always `"binary"`). If `kind == "ui_component"`, skip the persistent store entirely.
  - Apply `source_tool` filter on persistent store entries: `ref.source.get("tool")`. Note: `ArtifactRef.source` is typed as `dict[str, Any]` with `default_factory=dict` -- it is never `None`, always at least `{}`.
  - Deduplicate: persistent store entries take precedence. If a registry entry has the same `artifact_id` as a persistent store entry, replace the registry entry.
  - Convert `ArtifactRef` to dict matching `ArtifactSummary` fields using `_binary_component_name` and `_binary_summary` helpers.
  - Set `renderable` to `True` for all persistent store entries (consistent with `ArtifactRecord.to_public()` where `kind == "binary"` always yields `renderable=True`).
  - Wrap persistent store query in `try/except Exception`, log failures at `logger.debug` level.
  - Apply `limit` on the final combined list with `items[-limit:]` (favors persistent store entries when truncating since they are appended after registry items).

## Required Code

```python
# Target file: penguiflow/rich_output/nodes.py
# Full replacement of lines 176-203 (the entire list_artifacts function):

@tool(
    desc="List available artifacts for reuse in UI components.",
    tags=["rich_output", "artifacts"],
    side_effects="read",
)
async def list_artifacts(args: ListArtifactsArgs, ctx: ToolContext) -> ListArtifactsResult:
    _ensure_enabled()
    # Backward/behavioral compatibility: callers often use kind="tool_artifact"
    # when they really mean "any tool-produced artifact" (including ui_component).
    kind = None if args.kind in {"all", "tool_artifact"} else args.kind

    items: list[dict[str, Any]] = []

    # -- Step 1: Query in-run ArtifactRegistry (if available) --
    registry = get_artifact_registry(ctx)
    if registry is not None:
        planner = getattr(ctx, "_planner", None)
        trajectory = getattr(planner, "_active_trajectory", None)
        if trajectory is not None:
            try:
                registry.ingest_background_results(getattr(trajectory, "background_results", None))
            except Exception:
                pass
        llm_context = getattr(ctx, "llm_context", None)
        if llm_context is not None:
            try:
                registry.ingest_llm_context(llm_context)
            except Exception:
                pass
        items.extend(registry.list_records(kind=kind, source_tool=args.source_tool))

    # -- Step 2: Query persistent ArtifactStore (appended after registry) --
    if kind is None or kind == "binary":
        scoped = getattr(ctx, "artifacts", None)
        if scoped is not None:
            try:
                refs = await scoped.list()
                # Build set of IDs already in items (from registry) for dedup
                seen_ids = {item.get("artifact_id") for item in items if item.get("artifact_id")}
                for ref in refs:
                    # Persistent store wins dedup: replace registry entry if same ID
                    if ref.id in seen_ids:
                        items = [item for item in items if item.get("artifact_id") != ref.id]
                    source_tool = ref.source.get("tool")
                    if args.source_tool and source_tool != args.source_tool:
                        continue
                    items.append({
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
                    })
            except Exception as e:
                logger.debug("Failed to list persistent artifacts: %s", e, exc_info=True)

    # -- Step 3: Apply limit --
    # Persistent store items come after registry items, so [-limit:] favors them.
    if args.limit and args.limit > 0:
        items = items[-args.limit:]

    return ListArtifactsResult(artifacts=[ArtifactSummary.model_validate(item) for item in items])
```

```python
# Target file: penguiflow/rich_output/nodes.py
# Updated import block (lines 12-16):

from penguiflow.planner.artifact_registry import (
    _binary_component_name,
    _binary_summary,
    get_artifact_registry,
    has_artifact_refs,
    resolve_artifact_refs_async,
)
```

```python
# Target file: penguiflow/rich_output/nodes.py
# Add after "import json" (line 6):

import logging

# Add after the last import and before _ensure_enabled (around line 34):

logger = logging.getLogger(__name__)
```

## Exit Criteria (Success)
- [ ] `list_artifacts` no longer has the early return when `registry is None`
- [ ] `list_artifacts` queries `ctx.artifacts.list()` when `kind` is `None` or `"binary"`
- [ ] Persistent store results are deduplicated against registry results (persistent store wins)
- [ ] `source_tool` filter is applied to persistent store entries
- [ ] Persistent store query is wrapped in `try/except` with `logger.debug` on failure
- [ ] `limit` is applied on the combined list at the end with `items[-limit:]`
- [ ] `import logging` and `logger = logging.getLogger(__name__)` are present in the file
- [ ] `_binary_component_name` and `_binary_summary` are imported from `penguiflow.planner.artifact_registry`
- [ ] No import or syntax errors

## Implementation Notes
- `registry.list_records()` is called WITHOUT `limit=` because the limit is applied once on the combined list at the end.
- Persistent store items are appended after registry items so that `items[-limit:]` favors persistent store entries when truncating.
- `ArtifactRef.source` is typed as `dict[str, Any]` with `default_factory=dict`. It is never `None`, always at least `{}`. Calling `.get("tool")` directly is safe.
- The existing test `test_list_artifacts_returns_empty_without_registry` (line 272 in `tests/test_rich_output_nodes.py`) remains valid because the `DummyContext` has a `ScopedArtifacts` backed by an empty `InMemoryArtifactStore` -- so the result is still empty.
- New tests for this behavior are added in Phase 002.
- Depends on Phase 000 (establishes the `ctx.artifacts` convention in this file).

## Verification Commands
```bash
uv run ruff check penguiflow/rich_output/nodes.py
uv run mypy penguiflow/rich_output/nodes.py
uv run pytest tests/test_rich_output_nodes.py -v
```
