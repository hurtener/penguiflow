# Phase 001: Update Python tests to assert `namespace` field

## Objective
Update the existing artifact tests to verify that the new `namespace` field is correctly set on `ArtifactRef` instances -- both when explicitly provided and when defaulting to `None`. This ensures the model change from Phase 000 is fully covered by the test suite.

## Tasks
1. Update `test_minimal_ref` to assert `ref.namespace is None`.
2. Update `test_full_ref` to pass and assert `namespace`.
3. Update `test_ref_serialization` to verify namespace round-trips.
4. Update `test_namespace_in_id` to also assert `ref.namespace == "tableau"`.
5. Update `test_put_bytes_with_namespace_and_scope` to assert returned ref has namespace.
6. Update `test_put_text_with_namespace_and_scope` to assert returned ref has namespace.
7. Update `test_upload_passes_namespace` to assert `ref.namespace == "my_tool"`.

## Detailed Steps

### Step 1: Update `test_minimal_ref` (line 26)
- In `tests/test_artifacts.py`, locate `test_minimal_ref` in the `TestArtifactRef` class.
- Add `assert ref.namespace is None` after the existing `assert ref.scope is None` line (line 34) and before `assert ref.source == {}` (line 35).

### Step 2: Update `test_full_ref` (line 37)
- In the `ArtifactRef(...)` constructor call (~line 45), add `namespace="tableau"` after `scope=scope,` and before `source={"tool": "tableau.download_workbook"},`.
- Add `assert ref.namespace == "tableau"` after the existing `assert ref.scope == scope` assertion and before the `assert ref.source` assertion.

### Step 3: Update `test_ref_serialization` (line 62)
- In the `ArtifactRef(...)` constructor call (~line 64), add `namespace="test_ns"` as an additional keyword argument.
- After `assert data["size_bytes"] == 100` (line 72), add `assert data["namespace"] == "test_ns"`.
- After the round-trip reconstruction check (`assert ref2.id == ref.id`, line 76), add `assert ref2.namespace == "test_ns"`.

### Step 4: Update `test_namespace_in_id` (line 324)
- After the existing `assert ref.id.startswith("tableau_")` (line 330), add `assert ref.namespace == "tableau"`.

### Step 5: Update `test_put_bytes_with_namespace_and_scope` (line 543)
- After `assert ref.id.startswith("myns_")` (line 557), add `assert ref.namespace == "myns"`.

### Step 6: Update `test_put_text_with_namespace_and_scope` (line 562)
- After `assert ref.id.startswith("myns_")` (line 576), add `assert ref.namespace == "myns"`.

### Step 7: Update `test_upload_passes_namespace` (line 887)
- After `assert ref.id.startswith("my_tool_")` (line 894), add `assert ref.namespace == "my_tool"`.

## Required Code

```python
# Target file: tests/test_artifacts.py
# Change 1: In test_minimal_ref, add after `assert ref.scope is None`:
        assert ref.namespace is None
```

```python
# Target file: tests/test_artifacts.py
# Change 2: In test_full_ref, update the ArtifactRef constructor and add assertion.
# The constructor call should become:
        ref = ArtifactRef(
            id="pdf_abc123def456",
            mime_type="application/pdf",
            size_bytes=1024,
            filename="report.pdf",
            sha256="a" * 64,
            scope=scope,
            namespace="tableau",
            source={"tool": "tableau.download_workbook"},
        )
# And add assertion after `assert ref.scope == scope`:
        assert ref.namespace == "tableau"
```

```python
# Target file: tests/test_artifacts.py
# Change 3: In test_ref_serialization, update the constructor and add assertions.
# The constructor call should become:
        ref = ArtifactRef(
            id="test_123",
            mime_type="text/plain",
            size_bytes=100,
            namespace="test_ns",
        )
# Add after `assert data["size_bytes"] == 100`:
        assert data["namespace"] == "test_ns"
# Add after `assert ref2.id == ref.id`:
        assert ref2.namespace == "test_ns"
```

```python
# Target file: tests/test_artifacts.py
# Change 4: In test_namespace_in_id, add after `assert ref.id.startswith("tableau_")`:
        assert ref.namespace == "tableau"
```

```python
# Target file: tests/test_artifacts.py
# Change 5: In test_put_bytes_with_namespace_and_scope, add after `assert ref.id.startswith("myns_")`:
        assert ref.namespace == "myns"
```

```python
# Target file: tests/test_artifacts.py
# Change 6: In test_put_text_with_namespace_and_scope, add after `assert ref.id.startswith("myns_")`:
        assert ref.namespace == "myns"
```

```python
# Target file: tests/test_artifacts.py
# Change 7: In test_upload_passes_namespace, add after `assert ref.id.startswith("my_tool_")`:
        assert ref.namespace == "my_tool"
```

## Exit Criteria (Success)
- [ ] `test_minimal_ref` asserts `ref.namespace is None`.
- [ ] `test_full_ref` constructs `ArtifactRef` with `namespace="tableau"` and asserts it.
- [ ] `test_ref_serialization` includes `namespace="test_ns"` in the constructor, asserts it appears in `model_dump()` output, and verifies round-trip reconstruction.
- [ ] `test_namespace_in_id` asserts `ref.namespace == "tableau"`.
- [ ] `test_put_bytes_with_namespace_and_scope` asserts `ref.namespace == "myns"`.
- [ ] `test_put_text_with_namespace_and_scope` asserts `ref.namespace == "myns"`.
- [ ] `test_upload_passes_namespace` asserts `ref.namespace == "my_tool"`.
- [ ] All 7 updated tests pass: `uv run pytest tests/test_artifacts.py -v`.
- [ ] Full test suite passes with coverage threshold: `uv run pytest --cov=penguiflow --cov-report=term --cov-fail-under=84.5`.

## Implementation Notes
- This phase depends on Phase 000. The `namespace` field must exist on `ArtifactRef` and the store construction sites must pass it before these tests can pass.
- Only one file is modified: `tests/test_artifacts.py`.
- Each change is a small addition (1-3 lines) to an existing test. No new test functions are needed for the Python side.
- The test file uses `pytest-asyncio` with `asyncio_mode = "auto"`, so async tests run without explicit marks (though existing tests do have `@pytest.mark.asyncio`).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_artifacts.py -v -k "test_minimal_ref or test_full_ref or test_ref_serialization or test_namespace_in_id or test_put_bytes_with_namespace_and_scope or test_put_text_with_namespace_and_scope or test_upload_passes_namespace"
```
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run pytest tests/test_artifacts.py -v
```
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv run ruff check tests/test_artifacts.py
```
