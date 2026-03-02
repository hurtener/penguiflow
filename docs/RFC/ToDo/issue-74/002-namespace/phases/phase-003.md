# Phase 003: Update documentation with `namespace` field

## Objective
Update the artifacts guide documentation to include the `namespace` field in both `ArtifactRef` code examples, so that developers reading the docs see the field and understand how to use it.

## Tasks
1. Update the main `ArtifactRef` example in the artifacts guide.
2. Update the S3 custom store `ArtifactRef` construction example in the artifacts guide.

## Detailed Steps

### Step 1: Update the main `ArtifactRef` example (~line 73)
- Open `docs/tools/artifacts-guide.md`.
- Locate the `ArtifactRef` example code block starting around line 73 (under the "ArtifactRef Model" heading).
- Add `namespace="tableau",` to the constructor call, placed **after the `sha256="abc123...",` line and before the `scope=ArtifactScope(` line**. Add a comment `# Artifact grouping`.

### Step 2: Update the S3 custom store `ArtifactRef` construction example (~line 758)
- In the same file, locate the `return ArtifactRef(...)` call inside the S3 custom store example around line 758.
- Add `namespace=namespace,` to the constructor call, placed **after `sha256=hashlib.sha256(data).hexdigest(),` and before `scope=scope,`**.

## Required Code

```python
# Target file: docs/tools/artifacts-guide.md
# Change 1: The main ArtifactRef example (~line 73) should become:
ref = ArtifactRef(
    id="tableau_a1b2c3d4e5f6",      # Unique ID (namespace + content hash)
    mime_type="application/pdf",    # Content type
    size_bytes=1048576,             # 1MB
    filename="report.pdf",          # Suggested download name
    sha256="abc123...",             # Content hash for integrity
    namespace="tableau",            # Artifact grouping
    scope=ArtifactScope(            # Access control metadata
        session_id="sess_123",
        user_id="user_456",
    ),
    source={                        # Additional metadata
        "tool": "tableau.download_workbook",
```

```python
# Target file: docs/tools/artifacts-guide.md
# Change 2: The S3 custom store example (~line 758) should become:
        return ArtifactRef(
            id=artifact_id,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=filename,
            sha256=hashlib.sha256(data).hexdigest(),
            namespace=namespace,
            scope=scope,
            source=dict(meta or {}),
        )
```

## Exit Criteria (Success)
- [ ] The main `ArtifactRef` example in `docs/tools/artifacts-guide.md` (~line 73) includes `namespace="tableau",` with a comment.
- [ ] The S3 custom store `ArtifactRef` example (~line 758) includes `namespace=namespace,`.
- [ ] No broken markdown formatting in the file.
- [ ] Documentation builds without errors: `uv run mkdocs build --strict`.

## Implementation Notes
- This phase depends on Phase 000. The model field must exist before documentation examples reference it.
- Only one file is modified: `docs/tools/artifacts-guide.md`.
- The documentation build requires the `docs` extra: `uv pip install -e ".[dev,docs]"`.
- Field ordering in the examples should mirror the actual `ArtifactRef` model: `id`, `mime_type`, `size_bytes`, `filename`, `sha256`, `namespace`, `scope`, `source`.

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && grep -n "namespace" docs/tools/artifacts-guide.md
```
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv pip install -e ".[dev,docs]" && uv run mkdocs build --strict
```
