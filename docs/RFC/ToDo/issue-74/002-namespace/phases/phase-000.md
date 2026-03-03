# Phase 000: Add `namespace` field to ArtifactRef model and update construction sites

## Objective
Add `namespace: str | None = None` as a first-class field on the `ArtifactRef` Pydantic model so that consumers can identify the origin/grouping of any artifact without parsing the ID or inspecting side-channel metadata. Then update every `ArtifactRef(...)` construction call in the same file to forward the `namespace` parameter.

## Tasks
1. Add the `namespace` field to the `ArtifactRef` model in `penguiflow/artifacts.py`.
2. Update `NoOpArtifactStore.put_bytes()` to pass `namespace=namespace` when constructing `ArtifactRef`.
3. Update `NoOpArtifactStore.put_text()` to pass `namespace=namespace` when constructing `ArtifactRef`.
4. Update `InMemoryArtifactStore.put_bytes()` to pass `namespace=namespace` when constructing `ArtifactRef`.

## Detailed Steps

### Step 1: Add `namespace` field to `ArtifactRef`
- Open `penguiflow/artifacts.py`.
- Locate the `ArtifactRef` class (line 56).
- Add the field `namespace: str | None = None` with docstring `"""Namespace for artifact grouping (e.g., tool name)."""` **after the `scope` field (line 79) and before the `source` field (line 81)**. This groups metadata fields together and mirrors the intended ordering.

### Step 2: Update `NoOpArtifactStore.put_bytes()` construction
- In `penguiflow/artifacts.py`, locate the `ArtifactRef(...)` call inside `NoOpArtifactStore.put_bytes()` (~line 483).
- Add `namespace=namespace,` to the constructor call, placed after `scope=scope,` and before `source=source,`.

### Step 3: Update `NoOpArtifactStore.put_text()` construction
- In `penguiflow/artifacts.py`, locate the `ArtifactRef(...)` call inside `NoOpArtifactStore.put_text()` (~line 530).
- Add `namespace=namespace,` to the constructor call, placed after `scope=scope,` and before `source=source,`.

### Step 4: Update `InMemoryArtifactStore.put_bytes()` construction
- In `penguiflow/artifacts.py`, locate the `ArtifactRef(...)` call inside `InMemoryArtifactStore.put_bytes()` (~line 624).
- Add `namespace=namespace,` to the constructor call, placed after `scope=effective_scope,` and before `source=dict(meta or {}),`.
- Note: `InMemoryArtifactStore.put_text()` delegates to `put_bytes()`, so it is already covered.

## Required Code

```python
# Target file: penguiflow/artifacts.py
# Change 1: In the ArtifactRef class, after the `scope` field and before the `source` field,
# add the following field:

    namespace: str | None = None
    """Namespace for artifact grouping (e.g., tool name)."""
```

```python
# Target file: penguiflow/artifacts.py
# Change 2: In NoOpArtifactStore.put_bytes() (~line 483), update the ArtifactRef constructor.
# Before:
        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=scope,
            source=source,
        )
# After:
        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=scope,
            namespace=namespace,
            source=source,
        )
```

```python
# Target file: penguiflow/artifacts.py
# Change 3: In NoOpArtifactStore.put_text() (~line 530), update the ArtifactRef constructor.
# Before:
        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=scope,
            source=source,
        )
# After:
        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=scope,
            namespace=namespace,
            source=source,
        )
```

```python
# Target file: penguiflow/artifacts.py
# Change 4: In InMemoryArtifactStore.put_bytes() (~line 624), update the ArtifactRef constructor.
# Before:
        ref = ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=effective_scope,
            source=dict(meta or {}),
        )
# After:
        ref = ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=content_hash,
            scope=effective_scope,
            namespace=namespace,
            source=dict(meta or {}),
        )
```

## Exit Criteria (Success)
- [ ] `ArtifactRef` model in `penguiflow/artifacts.py` has a `namespace: str | None = None` field after `scope` and before `source`.
- [ ] `NoOpArtifactStore.put_bytes()` passes `namespace=namespace` in its `ArtifactRef(...)` call.
- [ ] `NoOpArtifactStore.put_text()` passes `namespace=namespace` in its `ArtifactRef(...)` call.
- [ ] `InMemoryArtifactStore.put_bytes()` passes `namespace=namespace` in its `ArtifactRef(...)` call.
- [ ] `ArtifactRef(id="x")` still works (namespace defaults to `None`) -- backward compatible.
- [ ] `ArtifactRef(id="x", namespace="tableau")` sets `ref.namespace == "tableau"`.
- [ ] No lint or type-check errors in the file.

## Implementation Notes
- The field is fully backward-compatible: existing code that omits `namespace` continues to work, and existing serialized JSON without the field deserializes with `None`.
- `InMemoryArtifactStore.put_text()` delegates to `put_bytes()`, so no separate change is needed there.
- No changes are needed to protocol method signatures -- `put_bytes`/`put_text` already accept a `namespace` parameter.
- No changes are needed to `ScopedArtifacts`, `_EventEmittingArtifactStoreProxy`, `ArtifactRegistry`, `ToolNode`, playground API endpoints, `payload_builders.py`, `sessions/tool_jobs.py`, or `state/in_memory.py` (PlaygroundArtifactStore). These all either delegate to the stores updated here or use `ref.model_dump()` which will automatically include the new field.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check penguiflow/artifacts.py
```
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run mypy penguiflow/artifacts.py
```
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run python -c "
from penguiflow.artifacts import ArtifactRef
# Test backward compat: namespace defaults to None
r1 = ArtifactRef(id='test')
assert r1.namespace is None, f'Expected None, got {r1.namespace}'
# Test explicit namespace
r2 = ArtifactRef(id='test', namespace='tableau')
assert r2.namespace == 'tableau', f'Expected tableau, got {r2.namespace}'
# Test serialization round-trip
data = r2.model_dump()
assert data['namespace'] == 'tableau'
r3 = ArtifactRef.model_validate(data)
assert r3.namespace == 'tableau'
# Test deserialization without namespace (backward compat)
r4 = ArtifactRef.model_validate({'id': 'old'})
assert r4.namespace is None
print('All checks passed.')
"
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-02

### Summary of Changes
- Added `namespace: str | None = None` field with docstring to `ArtifactRef` model in `penguiflow/artifacts.py`, positioned after `scope` and before `source` (line 81-82 in the updated file).
- Updated `NoOpArtifactStore.put_bytes()` ArtifactRef constructor to pass `namespace=namespace` after `scope=scope` and before `source=source`.
- Updated `NoOpArtifactStore.put_text()` ArtifactRef constructor to pass `namespace=namespace` after `scope=scope` and before `source=source`.
- Updated `InMemoryArtifactStore.put_bytes()` ArtifactRef constructor to pass `namespace=namespace` after `scope=effective_scope` and before `source=dict(meta or {})`.

### Key Considerations
- The field placement after `scope` and before `source` groups all metadata/scoping fields together, which is consistent with the existing model structure and the plan's specification.
- All three store construction sites already had `namespace` available as a method parameter, so the forwarding was straightforward with no parameter threading required.
- `InMemoryArtifactStore.put_text()` delegates to `put_bytes()` and already forwards `namespace`, so no change was needed there -- consistent with the plan's note.

### Assumptions
- The `namespace` field is purely additive metadata with no validation constraints (no regex pattern, no enum restriction). This matches the `str | None = None` type annotation specified in the plan.
- Existing serialized `ArtifactRef` JSON that lacks the `namespace` key will deserialize correctly with `None` as the default -- this is standard Pydantic v2 behavior and was confirmed by the verification script.
- No other files in the codebase construct `ArtifactRef` instances directly (other than the three sites updated here). The plan explicitly states that `ScopedArtifacts`, `_EventEmittingArtifactStoreProxy`, `ArtifactRegistry`, `ToolNode`, playground endpoints, `payload_builders.py`, `sessions/tool_jobs.py`, and `state/in_memory.py` all delegate to the stores updated here or use `ref.model_dump()`.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The `namespace` field is placed after `scope` in the model field order, but `model_dump()` output will include it in definition order. Any downstream consumers that rely on positional field ordering (unlikely with Pydantic dict output, but worth noting) should be verified.
- The existing 93 artifact-related tests all pass (`tests/test_artifacts.py` and `tests/test_artifact_registry.py`), confirming backward compatibility.

### Files Modified
- `penguiflow/artifacts.py` -- Added `namespace` field to `ArtifactRef` model and updated three `ArtifactRef(...)` constructor calls in `NoOpArtifactStore.put_bytes()`, `NoOpArtifactStore.put_text()`, and `InMemoryArtifactStore.put_bytes()`.
