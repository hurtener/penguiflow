# Phase 000: `render_component` -- switch from `ctx._artifacts` to `ctx.artifacts` and add `download()` fallback in hydration

## Objective

`render_component` currently passes the raw `ArtifactStore` (`ctx._artifacts`) to `resolve_artifact_refs_async`. Other artifact-interacting tools (`web_fetch`, `web_context`) use the public `ctx.artifacts` facade (`ScopedArtifacts`). This phase switches `render_component` to use `ctx.artifacts` for consistency and proper scope enforcement, and updates the hydration helper `_maybe_hydrate_stored_payload` to support the `ScopedArtifacts` interface (which exposes `.download()` instead of `.get()`).

## Tasks
1. Change the `artifact_store=` argument in `render_component` from `ctx._artifacts` to `ctx.artifacts`.
2. Update `_maybe_hydrate_stored_payload` to fall back to `.download()` when `.get()` is not available.

## Detailed Steps

### Step 1: Update `render_component` in `penguiflow/rich_output/nodes.py`

- Open `penguiflow/rich_output/nodes.py`.
- Locate line 106 inside the `render_component` function, within the `resolve_artifact_refs_async` call:
  ```python
  artifact_store=getattr(ctx, "_artifacts", None),
  ```
- Change it to:
  ```python
  artifact_store=getattr(ctx, "artifacts", None),
  ```
- This is the only change in this file for this phase.

### Step 2: Update `_maybe_hydrate_stored_payload` in `penguiflow/planner/artifact_registry.py`

- Open `penguiflow/planner/artifact_registry.py`.
- Locate the `_maybe_hydrate_stored_payload` function (around line 521).
- Find lines 534-536:
  ```python
  get_fn = getattr(artifact_store, "get", None)
  if not callable(get_fn):
      return None
  ```
- Replace with:
  ```python
  get_fn = getattr(artifact_store, "get", None)
  if not callable(get_fn):
      get_fn = getattr(artifact_store, "download", None)
  if not callable(get_fn):
      return None
  ```
- This makes the function try `.get()` first (for raw `ArtifactStore` instances), then fall back to `.download()` (for `ScopedArtifacts` instances). Both return `bytes | None`.

## Required Code

```python
# Target file: penguiflow/rich_output/nodes.py
# Change at line 106 (inside render_component, within resolve_artifact_refs_async call):

# Before:
            artifact_store=getattr(ctx, "_artifacts", None),

# After:
            artifact_store=getattr(ctx, "artifacts", None),
```

```python
# Target file: penguiflow/planner/artifact_registry.py
# Change at lines 534-536 (inside _maybe_hydrate_stored_payload):

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

## Exit Criteria (Success)
- [ ] `penguiflow/rich_output/nodes.py` line 106 reads `artifact_store=getattr(ctx, "artifacts", None),`
- [ ] `penguiflow/planner/artifact_registry.py` `_maybe_hydrate_stored_payload` tries `.get()` first, then falls back to `.download()`, then returns `None`
- [ ] No import or syntax errors in either file
- [ ] Existing tests still pass (the `DummyContext` in `tests/test_rich_output_nodes.py` already exposes both `._artifacts` and `.artifacts` properties)

## Implementation Notes
- `ScopedArtifacts.download()` is scope-checked -- it returns `None` for artifacts belonging to a different tenant/user/session. This is intentional: scope enforcement is the purpose of this migration.
- Previously, the raw `.get()` returned any artifact regardless of scope.
- The `DummyContext` in `tests/test_rich_output_nodes.py` already has an `.artifacts` property returning `ScopedArtifacts`, so existing tests will continue to work.
- This is a prerequisite for Phase 001 (`list_artifacts`) which establishes the convention of using `ctx.artifacts` throughout `nodes.py`.

## Verification Commands
```bash
uv run ruff check penguiflow/rich_output/nodes.py penguiflow/planner/artifact_registry.py
uv run mypy penguiflow/rich_output/nodes.py penguiflow/planner/artifact_registry.py
uv run pytest tests/test_rich_output_nodes.py -v
```
