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
