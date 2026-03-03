# Phase 002: Update Published mkdocs Pages (tool-design, configuration, statestore)

## Objective
Update three small published mkdocs pages to correctly reference the `ScopedArtifacts` facade and clarify the porcelain/plumbing distinction. These are all single-edit files that appear in the published documentation site and will be validated by `mkdocs build --strict`.

## Tasks
1. Update `ToolContext` listing in `docs/planner/tool-design.md`
2. Add porcelain/plumbing note to artifact extraction section in `docs/tools/configuration.md`
3. Update artifacts mention in `docs/tools/statestore.md`

## Detailed Steps

### Step 1: Clarify ToolContext listing in tool-design.md (plan section 4.1)
- Open `docs/planner/tool-design.md`
- Find the line (around line 99):
  > - `artifacts`: artifact store for large/binary payloads
- Replace with:
  > - `artifacts`: scoped artifact facade (`ScopedArtifacts`) -- use `upload()`/`download()`/`list()` for large/binary payloads with automatic scope injection

### Step 2: Add porcelain/plumbing note in configuration.md (plan section 6.1)
- Open `docs/tools/configuration.md`
- Find the "Artifact extraction and resources" section (around lines 91-98), which contains:
  > ### Artifact extraction and resources
  > ToolNode can extract large/binary content into artifacts and handle MCP resources:
  > - `ExternalToolConfig.artifact_extraction` controls the extraction pipeline
  > - MCP resources can generate tools like `{namespace}.resources_read` (see **[MCP resources](mcp-resources.md)**)
  >
  > See **[Artifacts & resources](artifacts-and-resources.md)**.
- Insert a blockquote **before** the `See **[Artifacts & resources]...**` line, after the bullet points. Add a blank line before and after the blockquote:
  > **Note:** The extraction pipeline is an internal (plumbing) mechanism -- it uses `ctx._artifacts` (the raw `ArtifactStore`) directly. Tool developers storing artifacts manually should use `ctx.artifacts` (the `ScopedArtifacts` facade) with `upload()`/`download()`/`list()`.

### Step 3: Update artifacts mention in statestore.md (plan section 8.1)
- Open `docs/tools/statestore.md`
- Find the line (around line 68):
  > - expose `artifact_store` or implement `ArtifactStore` so the planner can discover it (`discover_artifact_store`)
- Replace with:
  > - expose `artifact_store` or implement `ArtifactStore` (including the `list` method) so the planner can discover it (`discover_artifact_store`). Tool developers access artifacts via `ctx.artifacts` (a `ScopedArtifacts` facade); the raw `ArtifactStore` is plumbing.

## Required Code

No standalone code files -- all changes are markdown edits. The exact replacement text is specified in each step above.

## Exit Criteria (Success)
- [ ] `docs/planner/tool-design.md` describes `artifacts` as `ScopedArtifacts` facade with `upload()`/`download()`/`list()`
- [ ] `docs/tools/configuration.md` contains a blockquote note about the extraction pipeline being plumbing, with `ctx._artifacts` reference
- [ ] `docs/tools/statestore.md` mentions the `list` method requirement and `ScopedArtifacts` facade
- [ ] No broken markdown formatting in any of the three files
- [ ] Old text ("artifact store for large/binary payloads") is fully replaced in tool-design.md

## Implementation Notes
- All three files are published in the mkdocs site:
  - `docs/planner/tool-design.md` at "Planner > Tool design"
  - `docs/tools/configuration.md` at "Tools > Configuration"
  - `docs/tools/statestore.md` at "Tools > State store"
- The mkdocs build in the verification phase (phase-004) will validate these pages for broken links.
- These are all small files (4-7KB) with single edits each, making this a low-risk phase.

## Verification Commands
```bash
# Verify tool-design.md update
grep -n "ScopedArtifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/planner/tool-design.md

# Verify old text is gone from tool-design.md
grep -c "artifact store for large/binary payloads" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/planner/tool-design.md
# Expected: 0

# Verify configuration.md note
grep -n "extraction pipeline is an internal" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/configuration.md
grep -n "ctx._artifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/configuration.md

# Verify statestore.md update
grep -n "including the .list. method" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/statestore.md
grep -n "ScopedArtifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/statestore.md
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-02-27

### Summary of Changes
- **`docs/planner/tool-design.md` (line 99):** Replaced the `artifacts` bullet in the `ToolContext` listing from the generic "artifact store for large/binary payloads" to a precise description referencing `ScopedArtifacts` facade with `upload()`/`download()`/`list()` and automatic scope injection.
- **`docs/tools/configuration.md` (after line 96):** Inserted a blockquote note in the "Artifact extraction and resources" section clarifying that the extraction pipeline is internal plumbing using `ctx._artifacts`, while tool developers should use `ctx.artifacts` (the `ScopedArtifacts` facade). Blank lines before and after the blockquote ensure proper markdown rendering.
- **`docs/tools/statestore.md` (line 68):** Expanded the artifacts bullet to mention the required `list` method on `ArtifactStore` and to clarify the porcelain/plumbing distinction (`ctx.artifacts` as `ScopedArtifacts` facade vs. raw `ArtifactStore`).

### Key Considerations
- Used em-dashes (unicode `U+2014`) in the replacement text as specified in the task instructions, rather than the double-hyphen (`--`) used in the phase plan's quoted text. The plan specified "Use em-dashes where specified" and the replacement text in the detailed steps used `--` as a plain-text representation of em-dashes.
- Ensured blank lines surround the blockquote in `configuration.md` so that markdown renderers (and mkdocs) correctly parse it as a standalone blockquote rather than a continuation of the previous paragraph.
- Each edit was a single, targeted replacement matched on exact text content rather than line numbers, ensuring robustness if line numbers shift between edits.

### Assumptions
- The `--` in the phase plan's replacement text (e.g., "scoped artifact facade ... -- use") was intended to represent an em-dash (`---`) in the rendered output. I used the actual em-dash character (`---`) since the task instructions explicitly said "Use em-dashes where specified."
- No other files reference the old text "artifact store for large/binary payloads" in a way that needs updating (this phrase was only present in `tool-design.md`).
- The mkdocs build validation referenced in the plan (phase-004) will be handled separately.

### Deviations from Plan
None. All three edits were implemented exactly as specified.

### Potential Risks & Reviewer Attention Points
- The blockquote syntax (`> **Note:** ...`) in `configuration.md` should render correctly in mkdocs Material theme, but verify it renders as expected in the actual documentation build.
- The em-dash character should display correctly across all browsers and editors. If the project prefers ASCII-only markdown, the `---` characters could be reverted to `--`.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/planner/tool-design.md`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/configuration.md`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/statestore.md`
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-74/001-documentation/phases/phase-002.md` (this file, implementation notes appended)
