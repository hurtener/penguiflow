# Phase 002: New tests for `list_artifacts` persistent store integration

## Objective

Add 4 new test cases to `tests/test_rich_output_nodes.py` that verify the persistent `ArtifactStore` integration added in Phase 001. These tests cover: (1) fallback when no registry exists, (2) deduplication, (3) `source_tool` filtering on persistent store entries, and (4) `kind="ui_component"` skipping the persistent store.

## Tasks
1. Add a test for persistent store fallback when no registry exists.
2. Add a test for deduplication (same `artifact_id` in both registry and store).
3. Add a test for `source_tool` filtering on persistent store entries.
4. Add a test for `kind="ui_component"` skipping persistent store.

## Detailed Steps

### Step 1: Add test -- persistent store fallback when no registry exists

- Create a `DummyContext` with `_planner = None` (so `get_artifact_registry(ctx)` returns `None`).
- Upload a binary artifact to the context's `InMemoryArtifactStore` via `ctx.artifacts.upload()` (the `ScopedArtifacts` facade) with `meta={"tool": "web_fetch"}` so that `ref.source.get("tool")` returns `"web_fetch"`.
- Call `list_artifacts(ListArtifactsArgs(), ctx)`.
- Assert the result contains exactly 1 artifact.
- Assert the artifact's `kind` is `"binary"`.
- Assert the artifact's `artifact_id` matches the uploaded ref's `id`.

### Step 2: Add test -- deduplication (persistent store wins)

- Create a `DummyContext` with an `ArtifactRegistry` containing a registered artifact.
- Upload a binary artifact to the `InMemoryArtifactStore` with the SAME `artifact_id` as the registry record (this requires manually creating the store entry to match IDs -- easiest approach: upload via `ctx._artifacts.put_bytes(...)` with a matching namespace so the ID is predictable, then register a record in the registry using that same `artifact_id`).
- Call `list_artifacts(ListArtifactsArgs(), ctx)`.
- Assert the result contains exactly 1 artifact (not 2 -- dedup removes the duplicate).
- Assert the artifact's `kind` is `"binary"` (the persistent store entry won).

### Step 3: Add test -- `source_tool` filter on persistent store

- Create a `DummyContext` with `_planner = None` (no registry).
- Upload a binary artifact with `meta={"tool": "web_fetch"}`.
- Call `list_artifacts(ListArtifactsArgs(source_tool="other_tool"), ctx)`.
- Assert the result is empty (the artifact's `source.get("tool")` is `"web_fetch"`, not `"other_tool"`).
- Then call `list_artifacts(ListArtifactsArgs(source_tool="web_fetch"), ctx)`.
- Assert the result contains exactly 1 artifact.

### Step 4: Add test -- `kind="ui_component"` skips persistent store

- Create a `DummyContext` with `_planner = None` (no registry).
- Upload a binary artifact to the store.
- Call `list_artifacts(ListArtifactsArgs(kind="ui_component"), ctx)`.
- Assert the result is empty (persistent store artifacts are `"binary"`, not `"ui_component"`).

## Required Code

```python
# Target file: tests/test_rich_output_nodes.py
# Add these 4 test functions after the existing test_list_artifacts_* tests
# (after test_list_artifacts_tool_artifact_kind_includes_ui_components or similar).

@pytest.mark.asyncio
async def test_list_artifacts_persistent_store_fallback() -> None:
    """Persistent store artifacts are returned even when no in-run registry exists."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    # No registry -- ctx._planner is not set, so get_artifact_registry returns None.
    ref = await ctx.artifacts.upload(
        b"binary content",
        mime_type="application/octet-stream",
        filename="data.bin",
        meta={"tool": "web_fetch"},
    )
    result = await list_artifacts(ListArtifactsArgs(), ctx)
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.kind == "binary"
    assert artifact.artifact_id == ref.id
    assert artifact.renderable is True


@pytest.mark.asyncio
async def test_list_artifacts_deduplication_persistent_store_wins() -> None:
    """When registry and persistent store have the same artifact_id, persistent store wins."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=5000, max_total_bytes=10000)
    )
    ctx = DummyContext()
    # Upload a binary artifact to the persistent store
    ref = await ctx.artifacts.upload(
        b"binary content",
        mime_type="image/png",
        filename="chart.png",
        meta={"tool": "gather_data"},
    )
    # Register the same artifact_id in the in-run registry
    registry = ArtifactRegistry()
    registry.register_tool_artifact(
        "gather_data",
        "chart_artifacts",
        {"type": "echarts", "config": {}},
        step_index=0,
        artifact_id=ref.id,
    )
    ctx._planner = SimpleNamespace(_artifact_registry=registry)
    result = await list_artifacts(ListArtifactsArgs(), ctx)
    # Should have both the registry echarts entry AND the binary entry.
    # The echarts entry has a different artifact_id (auto-generated by register_tool_artifact).
    # But if the IDs match, the persistent store entry replaces the registry entry.
    # Count entries with this specific artifact_id:
    matching = [a for a in result.artifacts if a.artifact_id == ref.id]
    assert len(matching) == 1
    assert matching[0].kind == "binary"


@pytest.mark.asyncio
async def test_list_artifacts_source_tool_filter_persistent_store() -> None:
    """source_tool filter is applied to persistent store entries."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    await ctx.artifacts.upload(
        b"binary content",
        mime_type="application/octet-stream",
        meta={"tool": "web_fetch"},
    )
    # Filter for a different tool -- should exclude the persistent artifact
    result = await list_artifacts(ListArtifactsArgs(source_tool="other_tool"), ctx)
    assert result.artifacts == []

    # Filter for the correct tool -- should include it
    result = await list_artifacts(ListArtifactsArgs(source_tool="web_fetch"), ctx)
    assert len(result.artifacts) == 1


@pytest.mark.asyncio
async def test_list_artifacts_ui_component_kind_skips_persistent_store() -> None:
    """kind='ui_component' skips persistent store (persistent artifacts are 'binary')."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    await ctx.artifacts.upload(
        b"binary content",
        mime_type="application/octet-stream",
    )
    result = await list_artifacts(ListArtifactsArgs(kind="ui_component"), ctx)
    assert result.artifacts == []
```

## Exit Criteria (Success)
- [ ] 4 new test functions exist in `tests/test_rich_output_nodes.py`
- [ ] `test_list_artifacts_persistent_store_fallback` passes -- verifies persistent store is queried when no registry exists
- [ ] `test_list_artifacts_deduplication_persistent_store_wins` passes -- verifies dedup behavior
- [ ] `test_list_artifacts_source_tool_filter_persistent_store` passes -- verifies `source_tool` filtering
- [ ] `test_list_artifacts_ui_component_kind_skips_persistent_store` passes -- verifies `kind` filtering
- [ ] All existing tests in the file still pass

## Implementation Notes
- The `DummyContext` in `tests/test_rich_output_nodes.py` already provides both `._artifacts` (raw `InMemoryArtifactStore`) and `.artifacts` (`ScopedArtifacts` facade) properties, so no changes to the test helper are needed.
- The `meta={"tool": "web_fetch"}` passed to `upload()` maps to `ArtifactRef.source = {"tool": "web_fetch"}` because `InMemoryArtifactStore.put_bytes` sets `source=dict(meta or {})`.
- The deduplication test may need adjustment depending on how `ArtifactRegistry.register_tool_artifact` generates `artifact_id`. If the method does not accept an `artifact_id=` parameter, use a different approach: upload via the store, get the ref id, then manually set the `artifact_id` on the registry record. Check the `register_tool_artifact` signature before implementing.
- Depends on Phase 001 (the `list_artifacts` function must already have the persistent store query).
- All tests must call `configure_rich_output(RichOutputConfig(enabled=True, ...))` before invoking `list_artifacts`, following the pattern of existing tests.

## Verification Commands
```bash
uv run pytest tests/test_rich_output_nodes.py -v -k "persistent_store or deduplication or source_tool_filter_persistent or ui_component_kind_skips"
uv run pytest tests/test_rich_output_nodes.py -v
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-05

### Summary of Changes
- Added 4 new async test functions to `tests/test_rich_output_nodes.py` after the existing `test_describe_component_returns_component_schema` test:
  1. `test_list_artifacts_persistent_store_fallback` -- verifies that persistent store artifacts are returned even when no in-run ArtifactRegistry exists (no `_planner` attribute on context).
  2. `test_list_artifacts_deduplication_persistent_store_wins` -- verifies that when both the registry and persistent store contain an artifact with the same `artifact_id`, only one entry appears and it has `kind="binary"` (persistent store wins).
  3. `test_list_artifacts_source_tool_filter_persistent_store` -- verifies that the `source_tool` filter is correctly applied to persistent store entries via `ArtifactRef.source["tool"]`.
  4. `test_list_artifacts_ui_component_kind_skips_persistent_store` -- verifies that `kind="ui_component"` filtering excludes persistent store artifacts (which are always `kind="binary"`).

### Key Considerations
- **Deduplication test approach:** The phase plan's code example passed `artifact_id=ref.id` to `register_tool_artifact()`, but that method does not accept an `artifact_id` parameter. Instead, I register the artifact normally and then manually set `record.artifact_id = ref.id` on the returned `ArtifactRecord` dataclass. This is the approach suggested by the plan's own implementation notes ("manually set the artifact_id on the registry record"). The `ArtifactRecord` is a `@dataclass` so direct attribute assignment works.
- **`sourceTool` vs `source_tool` parameter name:** The `ListArtifactsArgs` Pydantic model defines the field as `source_tool` with alias `sourceTool`. The plan's code examples used `source_tool=`, but existing tests in the file use the `sourceTool` alias form. I used `sourceTool` to match the existing test convention and avoid mypy `[call-arg]` errors.
- **All tests follow existing patterns:** Each test calls `configure_rich_output(...)` first, creates a fresh `DummyContext`, and uses the same assertion style as existing tests.

### Assumptions
- The Phase 001 implementation of the persistent store query in `list_artifacts` is already in place and working. This was confirmed by all 4 tests passing.
- Setting `record.artifact_id` directly on the `ArtifactRecord` dataclass is a valid way to simulate a registry entry that shares an ID with a persistent store entry. In production, this would happen via `ingest_background_results` or similar ingestion paths.
- The `DummyContext` without a `_planner` attribute is sufficient to simulate "no registry exists" since `get_artifact_registry(ctx)` checks for `_planner` and returns `None` if absent.

### Deviations from Plan
- **Deduplication test:** Changed from passing `artifact_id=ref.id` to `register_tool_artifact()` (which doesn't accept that parameter) to manually setting `record.artifact_id = ref.id` after registration. Removed the stale comments about "auto-generated by register_tool_artifact" from the plan's code example.
- **`sourceTool` alias:** Changed `ListArtifactsArgs(source_tool="...")` to `ListArtifactsArgs(sourceTool="...")` to match existing test conventions and satisfy mypy type checking.

### Potential Risks & Reviewer Attention Points
- The deduplication test relies on directly mutating `record.artifact_id` on a dataclass instance. This is fragile if `ArtifactRecord` is ever changed to a frozen dataclass. However, this matches the test's intent and the same mutation pattern could be used by production code paths.
- The `_planner` attribute assignment (`ctx._planner = SimpleNamespace(...)`) in the deduplication test produces a mypy `[attr-defined]` warning, but this is the same pre-existing pattern used by 6+ other tests in this file.
- The `test_list_artifacts_returns_empty_without_registry` test (pre-existing) still passes because a fresh `DummyContext` has an empty `InMemoryArtifactStore`. If any test inadvertently shared state, this could break, but the `_reset_runtime` fixture and fresh context creation prevent this.

### Files Modified
- `tests/test_rich_output_nodes.py` -- added 4 new test functions (approximately 85 lines of new code)
