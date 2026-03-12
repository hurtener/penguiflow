# Phase 005: `_extract_artifacts_from_observation` -- switch from `ArtifactStore` to `ScopedArtifacts`

## Objective

`_extract_artifacts_from_observation` in `penguiflow/sessions/tool_jobs.py` currently accepts `artifact_store: ArtifactStore` and `scope: ArtifactScope | None`, and calls `artifact_store.put_text(..., scope=scope)`. The scope is manually constructed at the call site from `snapshot.tool_context`. This manual construction is redundant when using the `ScopedArtifacts` facade, which auto-injects scope. This phase changes the function signature to accept `ScopedArtifacts` instead, injects `session_id` into `ToolJobContext.tool_context` so the facade always has a complete scope, removes the manual scope construction at the call site, and updates the corresponding test.

## Tasks
1. Inject `session_id` into `ToolJobContext` construction in `tool_jobs.py`.
2. Change `_extract_artifacts_from_observation` signature and body to use `ScopedArtifacts`.
3. Update the call site to pass `ctx.artifacts` instead of `ctx._artifacts` + manual scope.
4. Clean up imports in `tool_jobs.py` (remove `ArtifactScope`).
5. Update the test in `tests/test_tool_jobs.py`.

## Detailed Steps

### Step 1: Inject `session_id` into `ToolJobContext` construction

- Locate lines 263-275 in `penguiflow/sessions/tool_jobs.py` where `ToolJobContext` is constructed:
  ```python
  ctx = ToolJobContext(
      llm_context=snapshot.llm_context or {},
      tool_context={
          **(snapshot.tool_context or {}),
          "task_id": runtime.state.task_id,
          "trace_id": trace_id,
          "_current_tool_name": spec.name,
          "_current_tool_call_id": tool_call_id,
      },
      artifacts=artifacts,
      state_store=kv_store,
      checkpoint_publisher=_publish_checkpoint,
  )
  ```
- Add `"session_id": snapshot.session_id,` to the `tool_context` dict, after the unpacking of `snapshot.tool_context`:
  ```python
  ctx = ToolJobContext(
      llm_context=snapshot.llm_context or {},
      tool_context={
          **(snapshot.tool_context or {}),
          "session_id": snapshot.session_id,
          "task_id": runtime.state.task_id,
          "trace_id": trace_id,
          "_current_tool_name": spec.name,
          "_current_tool_call_id": tool_call_id,
      },
      artifacts=artifacts,
      state_store=kv_store,
      checkpoint_publisher=_publish_checkpoint,
  )
  ```
- This ensures `ScopedArtifacts` always gets `session_id` from `tool_context`, matching the old behavior where `snapshot.session_id` was used for scope construction.

### Step 2: Change `_extract_artifacts_from_observation` signature and body

**Function signature (lines 165-172):**
```python
# Before:
async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifact_store: ArtifactStore,
    scope: ArtifactScope | None,
) -> list[dict[str, Any]]:

# After:
async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifacts: ScopedArtifacts,
) -> list[dict[str, Any]]:
```

**Internal `_store` function (lines 191-204):**

Remove the stale comment at line 192 (`# 'scope' is now captured from the outer function's parameter (no local override)`).

Replace the `artifact_store.put_text(...)` call with `artifacts.upload(...)` and remove the `scope=scope` parameter (scope is auto-injected by `ScopedArtifacts`):

```python
# Before:
async def _store(
    item: Any,
    *,
    item_index: int | None,
    _field_name: str = field_name,
    _node_name: str = node_name,
) -> None:
    serialized = json.dumps(item, ensure_ascii=False)
    # `scope` is now captured from the outer function's parameter (no local override)
    ref = await artifact_store.put_text(
        serialized,
        mime_type="application/json",
        filename=f"{_node_name}.{_field_name}.json",
        namespace=f"tool_artifact.{_node_name}.{_field_name}",
        scope=scope,
        meta={
            "node": _node_name,
            "field": _field_name,
            "item_index": item_index,
        },
    )

# After:
async def _store(
    item: Any,
    *,
    item_index: int | None,
    _field_name: str = field_name,
    _node_name: str = node_name,
) -> None:
    serialized = json.dumps(item, ensure_ascii=False)
    ref = await artifacts.upload(
        serialized,
        mime_type="application/json",
        filename=f"{_node_name}.{_field_name}.json",
        namespace=f"tool_artifact.{_node_name}.{_field_name}",
        meta={
            "node": _node_name,
            "field": _field_name,
            "item_index": item_index,
        },
    )
```

### Step 3: Update the call site (lines 280-296)

Delete the manual `artifact_scope` construction (lines 280-287) and update the function call to use `ctx.artifacts`:

```python
# Before (lines 280-296):
session_id = snapshot.session_id if isinstance(snapshot.session_id, str) and snapshot.session_id else None
tool_ctx = snapshot.tool_context or {}
artifact_scope = ArtifactScope(
    session_id=session_id,
    tenant_id=tool_ctx.get("tenant_id"),
    user_id=tool_ctx.get("user_id"),
    trace_id=tool_ctx.get("trace_id"),
) if session_id else None
extracted_artifacts = []
if isinstance(payload, Mapping):
    extracted_artifacts = await _extract_artifacts_from_observation(
        node_name=spec.name,
        out_model=spec.out_model,
        observation=payload,
        artifact_store=ctx._artifacts,
        scope=artifact_scope,
    )

# After:
extracted_artifacts = []
if isinstance(payload, Mapping):
    extracted_artifacts = await _extract_artifacts_from_observation(
        node_name=spec.name,
        out_model=spec.out_model,
        observation=payload,
        artifacts=ctx.artifacts,
    )
```

### Step 4: Clean up imports in `tool_jobs.py`

- Locate line 22:
  ```python
  from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore, ScopedArtifacts
  ```
- Remove `ArtifactScope` (no longer used anywhere in the file after removing it from the function signature at line 171 and the deleted scope construction at lines 282-287):
  ```python
  from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore, ScopedArtifacts
  ```
- `ArtifactStore` is still needed (used in `ToolJobContext.__init__` signature at line 36, `_artifacts` property return type at line 68, and `_run_job_pipeline` parameter at line 236).
- `ScopedArtifacts` is already imported (used in `ToolJobContext.artifacts` property).

### Step 5: Update test in `tests/test_tool_jobs.py`

- Locate `test_extract_artifacts_from_observation_propagates_full_scope` (lines 104-132).
- Update the test to construct a `ScopedArtifacts` instance and pass it via the new `artifacts=` parameter.
- Update imports: add `ScopedArtifacts` to the import from `penguiflow.artifacts` (line 8). Keep `ArtifactScope` -- it is still needed for the `store.list(scope=scope)` assertion.

**Updated test:**
```python
async def test_extract_artifacts_from_observation_propagates_full_scope() -> None:
    """_extract_artifacts_from_observation stores artifacts with the full scope."""
    store = InMemoryArtifactStore()
    scope = ArtifactScope(
        session_id="sess-1",
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
    )
    scoped = ScopedArtifacts(
        store,
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="sess-1",
        trace_id="trace-1",
    )
    observation = {"report": "some report data"}

    result = await _extract_artifacts_from_observation(
        node_name="test_node",
        out_model=ArtifactOut,
        observation=observation,
        artifacts=scoped,
    )

    assert len(result) == 1
    # Verify the stored artifact has the correct scope by listing from the store
    refs = await store.list(scope=scope)
    assert len(refs) >= 1
    ref = refs[0]
    assert ref.scope is not None
    assert ref.scope.session_id == "sess-1"
    assert ref.scope.tenant_id == "tenant-1"
    assert ref.scope.user_id == "user-1"
    assert ref.scope.trace_id == "trace-1"
```

**Updated imports (line 8 of test file):**
```python
# Before:
from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore

# After:
from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore, ScopedArtifacts
```

## Required Code

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Updated import (line 22):

from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore, ScopedArtifacts
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Updated function signature (lines 165-172):

async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifacts: ScopedArtifacts,
) -> list[dict[str, Any]]:
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Updated _store inner function (lines 184-204):

        async def _store(
            item: Any,
            *,
            item_index: int | None,
            _field_name: str = field_name,
            _node_name: str = node_name,
        ) -> None:
            serialized = json.dumps(item, ensure_ascii=False)
            ref = await artifacts.upload(
                serialized,
                mime_type="application/json",
                filename=f"{_node_name}.{_field_name}.json",
                namespace=f"tool_artifact.{_node_name}.{_field_name}",
                meta={
                    "node": _node_name,
                    "field": _field_name,
                    "item_index": item_index,
                },
            )
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Updated ToolJobContext construction (lines 263-275) -- add session_id:

        ctx = ToolJobContext(
            llm_context=snapshot.llm_context or {},
            tool_context={
                **(snapshot.tool_context or {}),
                "session_id": snapshot.session_id,
                "task_id": runtime.state.task_id,
                "trace_id": trace_id,
                "_current_tool_name": spec.name,
                "_current_tool_call_id": tool_call_id,
            },
            artifacts=artifacts,
            state_store=kv_store,
            checkpoint_publisher=_publish_checkpoint,
        )
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Updated call site (lines 280-296) -- remove manual scope construction:

        extracted_artifacts = []
        if isinstance(payload, Mapping):
            extracted_artifacts = await _extract_artifacts_from_observation(
                node_name=spec.name,
                out_model=spec.out_model,
                observation=payload,
                artifacts=ctx.artifacts,
            )
```

```python
# Target file: tests/test_tool_jobs.py
# Updated import (line 8):

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore, ScopedArtifacts
```

```python
# Target file: tests/test_tool_jobs.py
# Updated test (lines 104-132):

async def test_extract_artifacts_from_observation_propagates_full_scope() -> None:
    """_extract_artifacts_from_observation stores artifacts with the full scope."""
    store = InMemoryArtifactStore()
    scope = ArtifactScope(
        session_id="sess-1",
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
    )
    scoped = ScopedArtifacts(
        store,
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="sess-1",
        trace_id="trace-1",
    )
    observation = {"report": "some report data"}

    result = await _extract_artifacts_from_observation(
        node_name="test_node",
        out_model=ArtifactOut,
        observation=observation,
        artifacts=scoped,
    )

    assert len(result) == 1
    # Verify the stored artifact has the correct scope by listing from the store
    refs = await store.list(scope=scope)
    assert len(refs) >= 1
    ref = refs[0]
    assert ref.scope is not None
    assert ref.scope.session_id == "sess-1"
    assert ref.scope.tenant_id == "tenant-1"
    assert ref.scope.user_id == "user-1"
    assert ref.scope.trace_id == "trace-1"
```

## Exit Criteria (Success)
- [ ] `_extract_artifacts_from_observation` signature accepts `artifacts: ScopedArtifacts` instead of `artifact_store: ArtifactStore` + `scope: ArtifactScope | None`
- [ ] The `_store` inner function calls `artifacts.upload(...)` without `scope=` parameter
- [ ] The stale comment about scope capture is removed from `_store`
- [ ] `session_id` is injected into `ToolJobContext.tool_context` from `snapshot.session_id`
- [ ] The manual `artifact_scope` construction (lines 280-287) is deleted from the call site
- [ ] The call site passes `artifacts=ctx.artifacts` instead of `artifact_store=ctx._artifacts, scope=artifact_scope`
- [ ] `ArtifactScope` is removed from the import in `tool_jobs.py` (line 22)
- [ ] `ArtifactStore`, `NoOpArtifactStore`, and `ScopedArtifacts` remain in the import
- [ ] The test in `tests/test_tool_jobs.py` is updated to use `ScopedArtifacts`
- [ ] `ScopedArtifacts` is added to the import in `tests/test_tool_jobs.py`
- [ ] No import or syntax errors in either file
- [ ] All tests pass

## Implementation Notes
- The `trace_id` in `ctx.artifacts` will be the computed one (from `runtime.state.trace_id or runtime.context_snapshot.trace_id or tool_call_id` at lines 254-258) rather than the original `snapshot.tool_context.get("trace_id")`. This is intentional and more correct, as it matches the `trace_id` used by all other operations in the same tool execution context.
- The scope verification assertions in the test (`ref.scope.session_id == "sess-1"`, etc.) remain valid because `ScopedArtifacts.upload()` injects the scope into the underlying store, so `store.list(scope=scope)` still returns refs with the correct scope fields.
- `ArtifactScope` is still needed in the test file for the `store.list(scope=scope)` assertion, so keep it in the test imports.
- This phase is independent of Phases 000-004 but is logically placed last because it touches the sessions layer.

## Verification Commands
```bash
# Verify no remaining ArtifactScope usage in tool_jobs.py (should only appear in imports of other modules):
grep -n "ArtifactScope" penguiflow/sessions/tool_jobs.py && echo "FAIL: ArtifactScope still referenced" || echo "OK"

# Lint and type check:
uv run ruff check penguiflow/sessions/tool_jobs.py
uv run mypy penguiflow/sessions/tool_jobs.py

# Run relevant tests:
uv run pytest tests/test_tool_jobs.py -v

# Run full test suite to confirm no regressions:
uv run pytest
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes

- **`penguiflow/sessions/tool_jobs.py` (line 22):** Removed `ArtifactScope` from the import statement. The import now reads `from penguiflow.artifacts import ArtifactStore, NoOpArtifactStore, ScopedArtifacts`.
- **`penguiflow/sessions/tool_jobs.py` (lines 165-171):** Changed `_extract_artifacts_from_observation` signature from `artifact_store: ArtifactStore, scope: ArtifactScope | None` to `artifacts: ScopedArtifacts`.
- **`penguiflow/sessions/tool_jobs.py` (line 172):** Renamed the local accumulator variable from `artifacts` to `collected` to avoid shadowing the new `artifacts: ScopedArtifacts` parameter. Updated the corresponding `collected.append(entry)` call in `_store` (line 217) and `return collected` (line 226).
- **`penguiflow/sessions/tool_jobs.py` (lines 190-201):** Replaced `artifact_store.put_text(..., scope=scope)` with `artifacts.upload(...)` (no `scope=` parameter). Removed the stale comment about scope capture.
- **`penguiflow/sessions/tool_jobs.py` (line 264):** Added `"session_id": snapshot.session_id` to the `tool_context` dict in the `ToolJobContext` constructor call.
- **`penguiflow/sessions/tool_jobs.py` (lines 278-285):** Replaced the manual `artifact_scope` construction block (8 lines) and old function call with a simplified call passing `artifacts=ctx.artifacts`.
- **`tests/test_tool_jobs.py` (line 8):** Added `ScopedArtifacts` to the import from `penguiflow.artifacts`.
- **`tests/test_tool_jobs.py` (lines 104-138):** Updated `test_extract_artifacts_from_observation_propagates_full_scope` to construct a `ScopedArtifacts` instance and pass it via `artifacts=scoped`. The `ArtifactScope` and `store.list(scope=scope)` assertion remain for verifying the scope was correctly propagated to the underlying store.

### Key Considerations

- **Variable shadowing fix:** The original code had a local variable `artifacts: list[dict[str, Any]] = []` inside `_extract_artifacts_from_observation`. When the parameter was renamed from `artifact_store` to `artifacts` (per the plan), this created a name collision where the local list would shadow the `ScopedArtifacts` parameter. The inner `_store` function closure would then try to call `.upload()` on a list, causing an `AttributeError` at runtime. I renamed the local accumulator list to `collected` to resolve this. This is the minimal change needed to avoid the bug while preserving all existing behavior.
- **Parameter naming consistency:** The new parameter name `artifacts` matches the naming convention used elsewhere in the codebase (e.g., `ctx.artifacts` property returns `ScopedArtifacts`).

### Assumptions

- The `ScopedArtifacts.upload()` method signature (accepting `data`, `mime_type`, `filename`, `namespace`, `meta`) was verified by reading `penguiflow/artifacts.py` to confirm compatibility with the old `ArtifactStore.put_text()` call pattern.
- The `snapshot.session_id` value injected into `tool_context` may be `None` in cases where no session ID is available. This is acceptable because `ScopedArtifacts.__init__` already handles `None` values for `session_id` (the conditional `str(...)` conversion in `ToolJobContext.__init__` at line 50 guards against this).

### Deviations from Plan

- **Renamed local variable `artifacts` to `collected`:** The plan did not mention this rename because the variable shadowing issue was not anticipated. The original code had `artifacts: list[dict[str, Any]] = []` as the local accumulator, which was fine when the parameter was named `artifact_store`. After renaming the parameter to `artifacts` per the plan, a name collision would occur. I renamed the local to `collected` and updated all references (`collected.append(entry)` and `return collected`) to resolve this. This is a necessary deviation to make the code correct.

### Potential Risks & Reviewer Attention Points

- **Closure capture in `_store`:** The inner `_store` function captures `artifacts` (the `ScopedArtifacts` parameter) from the enclosing scope. Since `collected` is now a separate name, there is no risk of the closure accidentally capturing the wrong variable. However, reviewers should verify that the closure behavior is correct, especially in the loop where `_store` is defined inside a `for` loop over fields (the default parameter trick `_field_name: str = field_name` already handles this correctly).
- **`session_id` injection ordering:** The `session_id` is placed after `**(snapshot.tool_context or {})` unpacking, which means if `snapshot.tool_context` already contains a `session_id` key, it will be overwritten by `snapshot.session_id`. This is intentional -- the canonical session ID should come from `snapshot.session_id`, not from a potentially stale value in `tool_context`.

### Files Modified

- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/sessions/tool_jobs.py`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_tool_jobs.py`
