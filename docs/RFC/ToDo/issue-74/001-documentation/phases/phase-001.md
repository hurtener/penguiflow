# Phase 001: Update Artifact Guide and Artifacts-and-Resources Pages

## Objective
Update the two artifact-focused documentation files to correctly distinguish between the porcelain API (`ctx.artifacts` / `ScopedArtifacts`) and the plumbing API (`ctx._artifacts` / raw `ArtifactStore`). These files are the primary artifact documentation for users and must clearly communicate which API layer each reference describes.

## Tasks
1. Clarify porcelain API description in `artifacts-guide.md`
2. Fix the summary table row in `artifacts-guide.md` to use `ctx._artifacts`
3. Fix internal pipeline references in `artifacts-and-resources.md`
4. Add a porcelain/plumbing distinction note in `artifacts-and-resources.md`

## Detailed Steps

### Step 1: Clarify porcelain API in artifacts-guide.md (plan section 2.1)
- Open `docs/tools/artifacts-guide.md`
- Find the text (around line 264):
  > Tools can store binary/large text via the `ToolContext.artifacts` API:
- Replace it with:
  > Tools store binary/large text via `ctx.artifacts` -- a `ScopedArtifacts` facade that automatically injects tenant/user/session/trace scope on writes and enforces it on reads. This is the **porcelain** API for tool and agent developers; penguiflow internals use `ctx._artifacts` (the raw `ArtifactStore`) instead.

### Step 2: Fix summary table row in artifacts-guide.md (plan section 2.2)
- In the same file, find the table row (around line 829):
  > | **Binary Storage** | `ctx.artifacts.put_bytes()` | Store binary out-of-band |
- Replace with:
  > | **Binary Storage** | `ctx._artifacts.put_bytes()` | Store binary out-of-band (internal) |

### Step 3: Fix internal pipeline references in artifacts-and-resources.md (plan section 3.1)
- Open `docs/tools/artifacts-and-resources.md`
- Find these two bullet lines (around lines 70-71):
  > - clamp large inline strings into `ctx.artifacts.put_text(...)`
  > - extract binary content into `ctx.artifacts.put_bytes(...)`
- Replace with:
  > - clamp large inline strings into `ctx._artifacts.put_text(...)` (internal)
  > - extract binary content into `ctx._artifacts.put_bytes(...)` (internal)

### Step 4: Add porcelain/plumbing distinction note in artifacts-and-resources.md (plan section 3.2)
- In the same file, after the full bullet list (after the `resource_link` bullet, around line 72), add a blank line then the following blockquote, followed by another blank line:
  > **Note:** `ctx._artifacts` is the raw `ArtifactStore` used by penguiflow internals. Tool developers should use `ctx.artifacts` (the `ScopedArtifacts` facade) which provides `upload()`/`download()`/`list()` with automatic scope injection.

## Required Code

No standalone code files -- all changes are markdown edits. The exact replacement text is specified in each step above.

## Exit Criteria (Success)
- [ ] `docs/tools/artifacts-guide.md` line about artifact API mentions `ScopedArtifacts` facade and porcelain/plumbing distinction
- [ ] `docs/tools/artifacts-guide.md` summary table row uses `ctx._artifacts.put_bytes()` (with underscore prefix) and says "(internal)"
- [ ] `docs/tools/artifacts-and-resources.md` internal pipeline bullets use `ctx._artifacts` (with underscore prefix) and say "(internal)"
- [ ] `docs/tools/artifacts-and-resources.md` contains the blockquote note about `ctx._artifacts` vs `ctx.artifacts`
- [ ] No broken markdown formatting (tables render correctly, blockquote has blank lines before and after)

## Implementation Notes
- `docs/tools/artifacts-guide.md` is **not published** in the mkdocs site (it is excluded). Changes here are for internal reference.
- `docs/tools/artifacts-and-resources.md` **is published** in the mkdocs site under "Tools > Artifacts & resources". The mkdocs build in the verification phase will validate it.
- Match on exact text content, not line numbers. The plan says line numbers are approximate.
- Ensure blank lines before and after blockquote notes for proper markdown rendering.

## Verification Commands
```bash
# Verify porcelain API clarification in artifacts-guide.md
grep -n "ScopedArtifacts facade" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-guide.md

# Verify summary table fix in artifacts-guide.md
grep -n "ctx._artifacts.put_bytes" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-guide.md

# Verify old summary table row is gone
grep -c "ctx.artifacts.put_bytes" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-guide.md
# Expected: 0

# Verify internal pipeline references in artifacts-and-resources.md
grep -n "ctx._artifacts.put_text" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md
grep -n "ctx._artifacts.put_bytes" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md

# Verify the distinction note exists
grep -n "raw.*ArtifactStore.*penguiflow internals" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md

# Verify old references are fixed (should find 0 matches for ctx.artifacts.put_ without underscore)
grep -c "ctx\.artifacts\.put_" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md
# Expected: 0
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-27

### Summary of Changes
- **`docs/tools/artifacts-guide.md` (line 264):** Replaced the one-line porcelain API description with an expanded description that names the `ScopedArtifacts` facade, explains automatic scope injection, and explicitly labels it as the **porcelain** API while noting that `ctx._artifacts` is the plumbing layer used by internals.
- **`docs/tools/artifacts-guide.md` (line 829):** Changed the summary table "Binary Storage" row from `ctx.artifacts.put_bytes()` to `ctx._artifacts.put_bytes()` and appended "(internal)" to the purpose column.
- **`docs/tools/artifacts-and-resources.md` (lines 70-71):** Changed two bullet points in the ToolNode integration section from `ctx.artifacts.put_text(...)` / `ctx.artifacts.put_bytes(...)` to `ctx._artifacts.put_text(...)` / `ctx._artifacts.put_bytes(...)` and appended "(internal)" to each.
- **`docs/tools/artifacts-and-resources.md` (after line 72):** Inserted a blockquote note explaining that `ctx._artifacts` is the raw `ArtifactStore` for internals and that tool developers should use `ctx.artifacts` (the `ScopedArtifacts` facade). Blank lines are present before and after the blockquote for proper markdown rendering.

### Key Considerations
- All edits were matched on exact text content rather than line numbers, as the plan specified line numbers are approximate.
- The blockquote in `artifacts-and-resources.md` uses markdown `>` syntax (not MkDocs `!!!` admonition syntax) as specified in the phase plan. Since this file is published in the mkdocs site, the `>` blockquote will render as a standard HTML blockquote.
- Blank lines before and after the blockquote were verified to ensure proper markdown rendering per the exit criteria.
- The `artifacts-guide.md` file is noted as excluded from the mkdocs build, so the changes there are for internal reference only and will not affect the published site.

### Assumptions
- The phrase "ScopedArtifacts facade" in the replacement text on line 264 is wrapped in backticks as part of a code reference, consistent with the surrounding markdown style.
- The `ctx.artifacts.upload()` call in the code block at lines 274-279 of `artifacts-guide.md` was intentionally left unchanged, as the phase plan only targets the prose description line and the summary table row -- the code example correctly demonstrates the porcelain API usage.
- No other occurrences of `ctx.artifacts.put_bytes` or `ctx.artifacts.put_text` exist in these two files that need updating (verified by grep returning 0 matches for the old patterns).

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The `artifacts-guide.md` code example at line 274 still shows `ctx.artifacts.upload(...)` which is correct (it demonstrates the porcelain API). A reviewer should confirm this is intentionally left as-is.
- The blockquote in `artifacts-and-resources.md` uses `>` markdown syntax. If the MkDocs theme expects `!!!` admonition blocks for notes, this may render differently than other notes in the published documentation. However, the phase plan explicitly specified the `>` blockquote format.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-guide.md`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-74/001-documentation/phases/phase-001.md` (this file, implementation notes appended)
