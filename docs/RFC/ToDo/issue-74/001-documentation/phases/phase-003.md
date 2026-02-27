# Phase 003: Update REACT_PLANNER_INTEGRATION_GUIDE.md

## Objective
Update the React Planner Integration Guide to use the correct `ScopedArtifacts` type in the `ToolContext` protocol, fix the architecture diagram, update the MCP Tool Integration section to show the porcelain API, and correct the best practices. This is the most edit-heavy single file in the plan with 6 distinct changes.

## Tasks
1. Fix the import and `artifacts` property type in the ToolContext protocol listing
2. Add architecture note and fix the architecture diagram
3. Update the MCP Tool Integration section (prose and code example)
4. Update the Best Practices item about session isolation

## Detailed Steps

### Step 1: Fix import statement (plan section 5.1a)
- Open `REACT_PLANNER_INTEGRATION_GUIDE.md` (at the repo root)
- Find the import line (around line 468):
  ```python
  from penguiflow.artifacts import ArtifactStore
  ```
- Replace with:
  ```python
  from penguiflow.artifacts import ScopedArtifacts
  ```

### Step 2: Fix artifacts property (plan section 5.1b)
- In the same file, find the property definition (around lines 484-486):
  ```python
      @property
      def artifacts(self) -> ArtifactStore:
          """Binary/large-text artifact storage (out-of-band)."""
  ```
- Replace with:
  ```python
      @property
      def artifacts(self) -> ScopedArtifacts:
          """Scoped artifact facade for tool developers (porcelain API).

          Use upload()/download()/list() -- scope is injected automatically.
          Internal framework code uses ctx._artifacts (raw ArtifactStore) instead.
          """
  ```

### Step 3: Add architecture note (plan section 5.2b)
- Find the `### Architecture` heading (around line 1287)
- Between the blank line following the heading and the diagram's opening code fence (around line 1289), insert the following blockquote (with a blank line after it):
  > **Note:** This diagram shows the internal plumbing. Tool developers interact with `ctx.artifacts` (a `ScopedArtifacts` facade providing `upload()`/`download()`/`list()`). The facade delegates to the raw `ArtifactStore` internally, injecting scope automatically.

### Step 4: Fix architecture diagram method name (plan section 5.2a)
- In the architecture diagram code block, find the line (around line 1301):
  ```
  |  |  get_bytes(id, scope) -> bytes                           |    |
  ```
- Replace with:
  ```
  |  |  get(artifact_id) -> bytes | None                        |    |
  ```
- **Important:** Preserve the exact spacing/alignment of the ASCII diagram. The replacement should have the same visual width.

### Step 5: Update MCP Tool Integration prose (plan section 5.3, first part)
- Find the text (around lines 1380-1384):
  > MCP tools automatically use the artifact store when configured. The planner wraps the store with an event-emitting proxy that:
  > 1. Injects `session_id` from `tool_context` into artifact scope
  > 2. Emits `artifact_stored` events for real-time UI updates
  > 3. Stores artifacts with proper session isolation
- Replace with:
  > MCP tools access artifacts through `ctx.artifacts` -- a `ScopedArtifacts` facade that automatically injects `tenant_id`/`user_id`/`session_id`/`trace_id` from `tool_context`. Internally, the facade delegates to an `_EventEmittingArtifactStoreProxy` (plumbing) that:
  > 1. Emits `artifact_stored` events for real-time UI updates
  > 2. Delegates to the underlying `ArtifactStore` for persistence

### Step 6: Update MCP Tool Integration code example (plan section 5.3, second part)
- Find the Python code example inside the fences (around lines 1387-1395):
  ```python
  # Example: Tableau MCP tool storing a PDF
  # (handled automatically by ToolNode)
  ref = await artifact_store.put_bytes(
      pdf_data,
      mime_type="application/pdf",
      filename="dashboard_export.pdf",
      scope=ArtifactScope(session_id=session_id),
  )
  # ref.id is now accessible for download
  ```
- Replace the content **inside** the fences (keep the fences themselves) with:
  ```python
  # Tool developer stores a PDF via the porcelain API
  ref = await ctx.artifacts.upload(
      pdf_data,
      mime_type="application/pdf",
      filename="dashboard_export.pdf",
  )
  # Scope (tenant/user/session/trace) is injected automatically
  ```

### Step 7: Update Best Practices item 4 (plan section 5.4)
- Find the line (around line 1445):
  > 4. **Session isolation**: Always pass `session_id` in `tool_context` for proper scoping
- Replace with:
  > 4. **Session isolation**: Always pass `session_id`, `tenant_id`, and `user_id` in `tool_context` -- the `ScopedArtifacts` facade uses these to enforce scope automatically

## Required Code

No standalone code files -- all changes are markdown/code-in-markdown edits. The exact replacement text is specified in each step above.

## Exit Criteria (Success)
- [ ] Import line reads `from penguiflow.artifacts import ScopedArtifacts` (not `ArtifactStore`)
- [ ] The `artifacts` property return type is `ScopedArtifacts` with updated docstring mentioning porcelain API
- [ ] Architecture section has a blockquote note before the diagram explaining porcelain vs plumbing
- [ ] Architecture diagram uses `get(artifact_id) -> bytes | None` (not `get_bytes(id, scope)`)
- [ ] MCP Tool Integration prose mentions `ScopedArtifacts` facade and `_EventEmittingArtifactStoreProxy`
- [ ] MCP Tool Integration code example uses `ctx.artifacts.upload()` (not `artifact_store.put_bytes()`)
- [ ] Best Practices item 4 mentions `session_id`, `tenant_id`, `user_id`, and `ScopedArtifacts`
- [ ] No broken markdown formatting (code blocks, blockquotes, numbered lists)

## Implementation Notes
- This file is at the repo root: `REACT_PLANNER_INTEGRATION_GUIDE.md` (not under `docs/`).
- It is **excluded** from the published mkdocs site, so mkdocs build will not validate it. Manual review is sufficient.
- This is the most edit-heavy file in the plan (7 steps / 6 plan sections). Process edits **top-to-bottom** since earlier insertions shift line numbers.
- The architecture diagram is an ASCII art block -- take care to preserve alignment when replacing the method name. The replacement string should have comparable visual width.
- This phase has no dependencies on other phases (all edits are self-contained within this file).

## Verification Commands
```bash
# Verify import fix
grep -n "from penguiflow.artifacts import ScopedArtifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Verify old import is gone
grep -c "from penguiflow.artifacts import ArtifactStore" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md
# Expected: 0

# Verify property type fix
grep -n "def artifacts.*ScopedArtifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Verify architecture note
grep -n "This diagram shows the internal plumbing" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Verify diagram fix
grep -n "get(artifact_id)" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Verify old diagram method is gone
grep -c "get_bytes(id, scope)" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md
# Expected: 0

# Verify MCP integration prose update
grep -n "ScopedArtifacts facade" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Verify MCP code example update
grep -n "ctx.artifacts.upload" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Verify old code example is gone
grep -c "artifact_store.put_bytes" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md
# Expected: 0

# Verify best practices update
grep -n "tenant_id.*user_id.*tool_context" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-27

### Summary of Changes
- **Step 1 (line 468):** Changed import from `ArtifactStore` to `ScopedArtifacts` in the `ToolContext` protocol code listing.
- **Step 2 (lines 484-490):** Updated the `artifacts` property return type from `ArtifactStore` to `ScopedArtifacts` and expanded the docstring to describe the porcelain API (upload/download/list) with a note about the internal `ctx._artifacts` escape hatch.
- **Step 3 (inserted at line 1293):** Added a blockquote note before the ASCII architecture diagram explaining the porcelain vs plumbing distinction for tool developers.
- **Step 4 (line 1307):** Replaced `get_bytes(id, scope) -> bytes` with `get(artifact_id) -> bytes | None` in the ASCII architecture diagram, preserving column alignment with the surrounding box characters.
- **Step 5 (line 1386):** Rewrote the MCP Tool Integration prose to describe the `ScopedArtifacts` facade and `_EventEmittingArtifactStoreProxy` plumbing, removing the old 3-item list and replacing with a 2-item list.
- **Step 6 (lines 1391-1397):** Replaced the code example from `artifact_store.put_bytes()` with `ctx.artifacts.upload()`, removing explicit `scope=ArtifactScope(...)` argument and adding a comment about automatic scope injection.
- **Step 7 (line 1448):** Updated Best Practices item 4 to mention `session_id`, `tenant_id`, and `user_id` and reference the `ScopedArtifacts` facade for automatic scope enforcement.

### Key Considerations
- Edits were applied top-to-bottom (by line number) as specified in the phase file, since earlier insertions (Step 3's blockquote) shift subsequent line numbers.
- The ASCII art diagram alignment in Step 4 was carefully matched: `get(artifact_id) -> bytes | None` with trailing spaces produces the same visual width as the original `get_bytes(id, scope) -> bytes` line, keeping the right-side `|    |` box border aligned.
- The `### Architecture` heading appears twice in the file (once at line ~47 for the planner overview, once at line ~1287 for the Artifact Store section). The edit target was disambiguated by including surrounding context (the MCP Tool box lines) in the match string, ensuring the correct section was modified.

### Assumptions
- The phase file specifies the exact replacement text for each step, so the implementation follows those verbatim without deviation.
- The file is excluded from mkdocs build validation (as noted in the phase file), so no docs build was run.
- No other files reference the old `ArtifactStore` type in this context, so no cross-file changes were needed.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The ASCII art diagram alignment should be visually inspected in a fixed-width font to confirm the box borders line up perfectly. The replacement has the same character count including trailing spaces, but rendered display may vary by editor.
- The `_EventEmittingArtifactStoreProxy` class name in the MCP prose (Step 5) references an internal implementation detail -- if this class gets renamed in the codebase, this guide will need updating.
- The `ToolContext` protocol listing now imports `ScopedArtifacts` but the actual `penguiflow.artifacts` module needs to export this name for the example to be accurate. This was presumably handled in an earlier phase.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md` (7 edits applied)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-74/001-documentation/phases/phase-003.md` (implementation notes appended)
