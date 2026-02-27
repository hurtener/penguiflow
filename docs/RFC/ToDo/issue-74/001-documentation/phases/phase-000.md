# Phase 000: Update the StateStore Implementation Spec

## Objective
Add the `list` method to the `ArtifactStore` protocol definition, fix the scope enforcement note, add a new `ScopedArtifacts Facade` subsection, and update the implementation checklist. This is the foundational spec document that establishes the protocol contract -- updating it first ensures all other documentation phases reference the canonical source of truth.

## Tasks
1. Add `list` method signature to the `ArtifactStore` protocol code block
2. Fix the scope enforcement note to mention `ScopedArtifacts` facade
3. Insert the new `ScopedArtifacts Facade` subsection (with code block and tables)
4. Add `list` checklist item to the Implementation Checklist

## Detailed Steps

### Step 1: Add `list` to ArtifactStore protocol code block (plan section 1.1)
- Open `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md`
- Find the `ArtifactStore` protocol code block (around line 1136)
- Locate the `exists` method (the last method before the closing code fence)
- Insert the following **inside** the code block, between the `exists` method and the closing ` ``` `:

```python
    async def list(self, *, scope: ArtifactScope | None = None) -> list[ArtifactRef]:
        """List artifacts matching the given scope filter.
        None fields in scope = don't filter on that dimension.
        If scope is None, returns all artifacts.
        """
        ...
```

### Step 2: Fix scope enforcement note (plan section 1.2)
- Find the note around line 1163 that reads:
  > **Note:** Scope is stored by ArtifactStore but **enforcement happens at HTTP layer**.
- Replace it with:
  > **Note:** Scope is stored by `ArtifactStore` and **enforced at two layers**: the `ScopedArtifacts` facade (application-level, for tool/agent developers via `ctx.artifacts`) and the HTTP layer (for API consumers). See [ScopedArtifacts Facade](#scopedartifacts-facade) below.

### Step 3: Add ScopedArtifacts Facade subsection (plan section 1.3)
- Insert after the updated note from Step 2 and before `### ArtifactRetentionConfig`
- Insert the full subsection content verbatim as specified in the plan (the `### ScopedArtifacts Facade` heading, class definition code block, scope semantics table, and immutability explanation)
- The content to insert is:

```
### ScopedArtifacts Facade

Tool and agent developers interact with artifacts through `ctx.artifacts` -- an instance of `ScopedArtifacts`. This is the **porcelain** API. PenguiFlow internals (ToolNode extraction pipeline, playground, etc.) use `ctx._artifacts` -- the raw `ArtifactStore` -- which is the **plumbing** API.
```

Followed by a Python code block with the `ScopedArtifacts` class definition (see plan section 1.3 for full content), then the "Scope semantics" table and "Immutability" explanation.

### Step 4: Update Implementation Checklist (plan section 1.4)
- Find `### Artifact Store (if applicable)` in the implementation checklist section (around line 2278)
- Find the last existing checklist item: `- [ ] Session-scoped access patterns supported`
- After that line, add:
  ```
  - [ ] `list(scope=...)` method implemented on ArtifactStore
  ```

## Required Code

No standalone code files -- all changes are markdown edits to an existing document. The specific content to insert is fully specified in the plan file sections 1.1 through 1.4.

## Exit Criteria (Success)
- [ ] `docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md` contains the `list` method in the ArtifactStore protocol code block (7 methods total: `put_bytes`, `put_text`, `get`, `delete`, `exists`, `list`, and the metadata-related methods)
- [ ] The scope enforcement note references `ScopedArtifacts` facade and mentions two enforcement layers
- [ ] A `### ScopedArtifacts Facade` subsection exists between the scope enforcement note and `### ArtifactRetentionConfig`
- [ ] The ScopedArtifacts Facade subsection includes the class definition, scope semantics table, and immutability note
- [ ] The implementation checklist includes `- [ ] \`list(scope=...)\` method implemented on ArtifactStore`
- [ ] No markdown syntax errors (proper code fences, table formatting, heading hierarchy)

## Implementation Notes
- This is a very large file (~78KB). Match edits on **exact text content**, not line numbers, as the plan warns line numbers are approximate.
- Process edits **top-to-bottom** within the file -- earlier insertions shift subsequent line numbers.
- When inserting the ScopedArtifacts Facade subsection, ensure blank lines before and after headings and blockquotes for proper markdown rendering.
- The ScopedArtifacts class code block uses em-dashes (`--`) in the plan text; ensure they render correctly in markdown.
- This file is under `docs/spec/` which is **excluded** from the published mkdocs site, so mkdocs build will not validate it. Manual review is sufficient.

## Verification Commands
```bash
# Verify the list method is in the ArtifactStore protocol block
grep -n "async def list" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md

# Verify the updated scope enforcement note
grep -n "enforced at two layers" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md

# Verify the ScopedArtifacts Facade subsection exists
grep -n "### ScopedArtifacts Facade" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md

# Verify scope semantics table exists
grep -n "Scope behavior" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md

# Verify implementation checklist update
grep -n "list(scope=...)" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md

# Verify old scope note is gone
grep -c "enforcement happens at HTTP layer" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md
# Expected: 0
```
