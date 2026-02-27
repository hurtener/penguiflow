# Phase 006: Update Documentation References

## Objective
Update documentation files that reference the old `ctx.artifacts.put_bytes()`/`put_text()` tool-developer API to use the new `ctx.artifacts.upload()` API. Only update references that describe the tool developer API; leave internal framework pipeline references unchanged. This is Enhancement 4.

## Tasks
1. Update `docs/tools/artifacts-guide.md` -- tool-developer code example
2. Verify `docs/tools/artifacts-and-resources.md` -- no changes needed
3. Verify `docs/planner/tool-design.md` -- no changes needed (or optional clarity improvement)

## Detailed Steps

### Step 1: Update `docs/tools/artifacts-guide.md`
- Line 274: Change `ref = await ctx.artifacts.put_bytes(` to `ref = await ctx.artifacts.upload(`
- This is a tool-developer code example showing how to store a PDF artifact.
- The surrounding code context is:

```python
async def my_tool(ctx: ToolContext, url: str) -> dict:
    """Download PDF and store as artifact."""
    pdf_bytes = await download_pdf(url)

    # Store in artifact store
    ref = await ctx.artifacts.put_bytes(
        pdf_bytes,
        mime_type="application/pdf",
        filename="report.pdf",
        namespace="my_tool",  # Groups artifacts by tool
    )
```

- Change `put_bytes` to `upload` on line 274.
- **Do NOT change line 829** (table entry `ctx.artifacts.put_bytes()` in the "Binary Storage" row) -- that describes the internal pipeline component, not the tool-developer API.
- Optionally update any surrounding prose that describes `put_bytes`/`put_text` as the tool developer interface.

### Step 2: Verify `docs/tools/artifacts-and-resources.md`
- Lines 70-71 describe what ToolNode's internal extraction pipeline does. These are internal framework calls, NOT tool-developer API.
- **No changes needed.**

### Step 3: Verify `docs/planner/tool-design.md`
- Line 80 says "store them in `ctx.artifacts` and return a compact reference". After this plan, `ctx.artifacts` returns the `ScopedArtifacts` facade, so the reference is correct as-is.
- **No changes needed**, but optionally expand to `ctx.artifacts.upload(...)` for clarity.

## Required Code

```markdown
<!-- Target file: docs/tools/artifacts-guide.md -->
<!-- Line 274: change put_bytes to upload -->
<!-- Before: -->
    ref = await ctx.artifacts.put_bytes(
<!-- After: -->
    ref = await ctx.artifacts.upload(
```

## Exit Criteria (Success)
- [ ] `docs/tools/artifacts-guide.md` line 274 uses `ctx.artifacts.upload(` instead of `ctx.artifacts.put_bytes(`
- [ ] Line 829 of `docs/tools/artifacts-guide.md` is UNCHANGED (internal pipeline reference)
- [ ] `docs/tools/artifacts-and-resources.md` is UNCHANGED
- [ ] `docs/planner/tool-design.md` is UNCHANGED (or has optional clarity improvement only)
- [ ] No broken markdown syntax introduced

## Implementation Notes
- This is a documentation-only phase. No Python code changes.
- The scope rule is: only update references that describe the **tool developer** API (`ctx.artifacts.*`). References that describe the **internal framework pipeline** should remain as-is, because internal code still uses `ctx._artifacts.put_bytes()`/`put_text()`.
- This phase depends on Phases 003-005 (the `ScopedArtifacts` facade must be wired in so that `ctx.artifacts.upload()` is the actual API).

## Verification Commands
```bash
cd /Users/martin.alonso/Documents/lg/repos/penguiflow
# Verify the change was made:
grep -n 'ctx.artifacts.upload' docs/tools/artifacts-guide.md
# Verify the internal reference was NOT changed:
grep -n 'ctx.artifacts.put_bytes' docs/tools/artifacts-guide.md
# The second grep should still find the line 829 table entry (internal pipeline).
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-26

### Summary of Changes
- `docs/tools/artifacts-guide.md` line 274: Changed `ctx.artifacts.put_bytes(` to `ctx.artifacts.upload(` in the tool-developer code example under the "Storing Binary Content via ToolContext" section.

### Key Considerations
- The change was made using a targeted string replacement scoped to the exact surrounding block (the `# Store in artifact store` comment through the closing parenthesis of the call), ensuring no other occurrences were affected.
- The scope rule documented in the phase — only update tool-developer API references, leave internal pipeline references alone — was strictly followed. The summary table entry on line 829 (`ctx.artifacts.put_bytes()` in the "Binary Storage" row) was deliberately left untouched.

### Assumptions
- The optional prose update around `put_bytes`/`put_text` in the surrounding text was not performed. Reading the surrounding content in the "Storing Binary Content via ToolContext" section, the prose introduces the change by saying "Tools can store binary/large text via the `ToolContext.artifacts` API" and then shows the code example — there is no standalone prose sentence that says "call `put_bytes`", so no additional prose update was needed.
- The optional clarity improvement in `docs/planner/tool-design.md` (expanding `ctx.artifacts` to `ctx.artifacts.upload(...)`) was not applied, consistent with the "no changes needed" instruction and the intent to keep scope minimal in a documentation-only phase.

### Deviations from Plan
None. The single required change (line 274) was applied. All three verification conditions from the exit criteria were confirmed via grep before writing these notes.

### Potential Risks & Reviewer Attention Points
- Confirm that `ctx.artifacts.upload()` is in fact the live API exposed by `ScopedArtifacts` once Phases 003-005 are merged. This phase is purely editorial; if those phases have not yet landed, the documentation would momentarily describe an API that does not yet exist.
- The summary table entry on line 829 still reads `ctx.artifacts.put_bytes()`. Reviewers should decide whether that row should eventually be updated to reflect the `ScopedArtifacts` upload facade (or removed) in a future cleanup phase, since the label "Binary Storage" could be confusing once the public API is `upload()`.

### Files Modified
- `docs/tools/artifacts-guide.md` — one-line change on line 274 (`put_bytes` → `upload`)
- `docs/RFC/ToDo/issue-74/phases/phase-006.md` — appended this implementation notes section
