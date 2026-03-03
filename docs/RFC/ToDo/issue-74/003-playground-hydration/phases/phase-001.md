# Phase 001: Upstream scope fix -- tool_jobs

## Objective
Fix scope propagation in `_extract_artifacts_from_observation` in `penguiflow/sessions/tool_jobs.py` so that artifacts extracted from tool execution results are stored with the full scope (`session_id`, `tenant_id`, `user_id`, `trace_id`) rather than only `session_id`. This is plan section 0b -- the sessions-layer counterpart to the planner-layer fixes in Phase 000.

## Tasks
1. Change the `_extract_artifacts_from_observation` function signature to accept `scope: ArtifactScope | None` instead of `session_id: str | None`
2. Remove the local `scope` variable in the inner `_store` closure so it captures the outer parameter via closure
3. Update the call site to build the full `ArtifactScope` from `snapshot.tool_context`
4. Add a test to `tests/test_tool_jobs.py` verifying scope propagation

## Detailed Steps

### Step 1: Change function signature
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/sessions/tool_jobs.py`
- Find `_extract_artifacts_from_observation` at line 165
- Replace the `session_id: str | None,` parameter with `scope: ArtifactScope | None,`
- `ArtifactScope` is already imported at line 22: `from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore, ScopedArtifacts` -- no import change needed

### Step 2: Update the inner `_store` closure
- Inside `_extract_artifacts_from_observation`, find the inner `async def _store(...)` function at line 184
- **Remove line 192**: `scope = ArtifactScope(session_id=session_id) if session_id else None`
- **Replace with comment**: `# `scope` is now captured from the outer function's parameter (no local override)`
- This causes Python to resolve `scope` via closure capture from the enclosing function's `scope` parameter
- No other changes to `_store`'s body are needed -- the `scope=scope` keyword arg on line 198 (the `put_text` call) now references the outer `scope` parameter

### Step 3: Update the call site
- Find the call site at lines 280-289
- Replace the block starting at line 280 (`session_id = snapshot.session_id ...`) through line 289 (`session_id=session_id,`) with code that builds the full `ArtifactScope` and passes `scope=artifact_scope`
- The new code should: extract `session_id` from `snapshot.session_id`, extract `tool_context` from `snapshot.tool_context`, build `ArtifactScope` with all four fields if `session_id` is truthy (else `None`), and pass `scope=artifact_scope` to the function call

### Step 4: Add test to `tests/test_tool_jobs.py`
- Open `/Users/martin.alonso/Documents/lg/repos/penguiflow/tests/test_tool_jobs.py`
- Add imports: `from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore` and `from penguiflow.sessions.tool_jobs import _extract_artifacts_from_observation`
- Add a Pydantic model with an artifact-annotated field for testing
- Write an async test that calls `_extract_artifacts_from_observation` with a full `ArtifactScope` and verifies stored artifacts have matching scope fields

## Required Code

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Replace the function signature at line 165-172 with:

async def _extract_artifacts_from_observation(
    *,
    node_name: str,
    out_model: type[BaseModel],
    observation: Mapping[str, Any],
    artifact_store: ArtifactStore,
    scope: ArtifactScope | None,
) -> list[dict[str, Any]]:
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Inside the _store closure (line 184-), replace line 192:
#   scope = ArtifactScope(session_id=session_id) if session_id else None
# With:
#   # `scope` is now captured from the outer function's parameter (no local override)
#
# The resulting _store body should be:

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
            artifact_type = _infer_artifact_type(item)
            compact_meta = _extract_compact_metadata(item)
            stub: dict[str, Any] = {
                "artifact": ref.model_dump(mode="json"),
                **compact_meta,
            }
            if artifact_type:
                stub["type"] = artifact_type
            entry: dict[str, Any] = {
                "node": _node_name,
                "field": _field_name,
                "artifact": stub,
            }
            if item_index is not None:
                entry["item_index"] = item_index
            artifacts.append(entry)
```

```python
# Target file: penguiflow/sessions/tool_jobs.py
# Replace the call site at lines 280-289 with:

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
```

```python
# Target file: tests/test_tool_jobs.py
# Add these imports at the top (after existing imports):

from penguiflow.artifacts import ArtifactScope, InMemoryArtifactStore
from penguiflow.sessions.tool_jobs import _extract_artifacts_from_observation

# Add this model after existing models:

class ArtifactOut(BaseModel):
    report: str = Field(json_schema_extra={"artifact": True})

# Add this test:

async def test_extract_artifacts_from_observation_propagates_full_scope() -> None:
    """_extract_artifacts_from_observation stores artifacts with the full scope."""
    store = InMemoryArtifactStore()
    scope = ArtifactScope(
        session_id="sess-1",
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
    )
    observation = {"report": "some report data"}

    result = await _extract_artifacts_from_observation(
        node_name="test_node",
        out_model=ArtifactOut,
        observation=observation,
        artifact_store=store,
        scope=scope,
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
- [ ] `_extract_artifacts_from_observation` signature uses `scope: ArtifactScope | None` instead of `session_id: str | None`
- [ ] The inner `_store` closure no longer defines a local `scope` variable -- it captures the outer parameter
- [ ] The call site at line ~280 builds `ArtifactScope` with `session_id`, `tenant_id`, `user_id`, `trace_id` from `snapshot.tool_context`
- [ ] The call site passes `scope=artifact_scope` to `_extract_artifacts_from_observation`
- [ ] `tests/test_tool_jobs.py` has a new test for scope propagation that passes
- [ ] No ruff lint errors in modified files
- [ ] No mypy type errors in modified files

## Implementation Notes
- `ArtifactScope` is already imported at line 22 of `tool_jobs.py`: `from penguiflow.artifacts import ArtifactScope, ArtifactStore, NoOpArtifactStore, ScopedArtifacts`. No import changes needed in the source file.
- The `_store` closure captures `scope` from the enclosing `_extract_artifacts_from_observation` scope. By removing the local `scope = ArtifactScope(session_id=session_id) if session_id else None` line, Python's closure rules cause it to resolve `scope` from the outer function's parameter.
- The `snapshot.tool_context` is a dict that may contain `tenant_id`, `user_id`, and `trace_id` keys (set by session setup). Using `.get()` returns `None` for missing keys, which is the correct behavior.
- The existing `tests/test_tool_jobs.py` uses `pytest-asyncio` with `asyncio_mode = "auto"`, so async test functions run without the `@pytest.mark.asyncio` decorator (though the existing test at line 29 does use it -- either style works).
- The `Field(json_schema_extra={"artifact": True})` annotation is what triggers `_extract_artifacts_from_observation` to process a field as an artifact (see the `extra.get("artifact")` check at line 176).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_tool_jobs.py -k "extract_artifacts" -v
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/sessions/tool_jobs.py tests/test_tool_jobs.py
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy penguiflow/sessions/tool_jobs.py
```
