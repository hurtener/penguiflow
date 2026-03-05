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

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes
- **`penguiflow/rich_output/nodes.py` (line 106):** Changed `artifact_store=getattr(ctx, "_artifacts", None)` to `artifact_store=getattr(ctx, "artifacts", None)` in the `render_component` function's call to `resolve_artifact_refs_async`. This switches from the raw `ArtifactStore` to the public `ScopedArtifacts` facade, consistent with how other tools (`web_fetch`, `web_context`) access artifacts.
- **`penguiflow/planner/artifact_registry.py` (lines 534-538):** Added a `.download()` fallback in `_maybe_hydrate_stored_payload`. The function now tries `artifact_store.get()` first (for raw `ArtifactStore` instances), then falls back to `artifact_store.download()` (for `ScopedArtifacts` instances), and only returns `None` if neither is callable.

### Key Considerations
- The `ScopedArtifacts` class exposes `download()` (with scope checking) but does not expose a `get()` method. The raw `ArtifactStore` exposes `get()` but not `download()`. The fallback order (`.get()` first, `.download()` second) preserves backward compatibility if `_maybe_hydrate_stored_payload` is ever called with a raw `ArtifactStore` directly, while also supporting the new `ScopedArtifacts` path.
- Both `.get()` and `.download()` have the same signature `(artifact_id: str) -> bytes | None` and are async, so the calling code (`raw = await get_fn(...)`) works identically with either.
- The `DummyContext` in the test file already has both `._artifacts` (raw store) and `.artifacts` (scoped facade) properties. Since `ScopedArtifacts` is constructed with all-`None` scope values in the test, scope checking is effectively a no-op, allowing the existing test `test_list_artifacts_ingests_background_results_for_artifact_refs` to pass unchanged.

### Assumptions
- Assumed that `ScopedArtifacts.download()` is the correct public API for reading artifact bytes in the scoped context, based on the class implementation in `penguiflow/artifacts.py` which confirms it returns `bytes | None` after scope checking.
- Assumed no other callers of `_maybe_hydrate_stored_payload` outside of `ArtifactRegistry.resolve_ref_async` need to be updated, since the artifact_store parameter flows through from the caller.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- After this change, `render_component` will perform scope-checked artifact reads. If a production context has restrictive scoping (specific `tenant_id`/`user_id`/`session_id`), artifact hydration will return `None` for out-of-scope artifacts, silently failing to hydrate rather than raising an error. This is the intended behavior per the plan notes, but worth verifying in integration tests with scoped contexts.
- The fallback pattern (`try .get(), then .download()`) means that if a future object has both `.get()` and `.download()` with different semantics, `.get()` will always win. This is fine for the current codebase but should be kept in mind.

### Files Modified
- `penguiflow/rich_output/nodes.py` (modified line 106)
- `penguiflow/planner/artifact_registry.py` (modified lines 534-538)
