# Phase 004: Update TEMPLATING_QUICKGUIDE.md and Run Final Verification

## Objective
Add the developer API note to the Templating Quickguide, then run cross-file verification checks and the mkdocs build to confirm all documentation updates from phases 000-003 are correct and consistent. This is the final phase that validates the entire documentation update.

## Tasks
1. Add developer API note to `TEMPLATING_QUICKGUIDE.md`
2. Run cross-file consistency verification checks
3. Run `mkdocs build --strict` to validate published pages

## Detailed Steps

### Step 1: Add developer API note in TEMPLATING_QUICKGUIDE.md (plan section 7.1)
- Open `TEMPLATING_QUICKGUIDE.md` (at the repo root)
- Find the "Artifact Store Configuration" block (around lines 2085-2089):
  > **When artifact store is enabled:**
  > - `InMemoryArtifactStore` is created with retention config
  > - Artifacts stored by MCP tools (e.g., Tableau PDFs) are accessible
  > - Playground UI shows artifacts in the sidebar
  > - REST endpoint `/artifacts/{id}` serves binary content
- After this block, add a blank line, then the following blockquote, then another blank line:
  > **Developer API:** Tool developers access artifacts via `ctx.artifacts` -- a `ScopedArtifacts` facade that provides `upload()`/`download()`/`list()` with automatic scope injection. The raw `ArtifactStore` (`ctx._artifacts`) is used internally by the ToolNode extraction pipeline and playground.

### Step 2: Run cross-file verification checks
After the edit, run the following verification checks to ensure all 8 files are consistent:

1. **Check porcelain/plumbing distinction is clear** -- every file that mentions artifacts should correctly distinguish `ctx.artifacts` (porcelain / `ScopedArtifacts`) from `ctx._artifacts` (plumbing / raw `ArtifactStore`)
2. **Check ArtifactStore protocol has all methods** -- the spec file should list all 7 methods including `list`
3. **Check no doc incorrectly uses `ctx.artifacts.put_bytes()` or `ctx.artifacts.put_text()`** -- these are internal methods that should only appear with `ctx._artifacts`
4. **Check internal pipeline references use `ctx._artifacts`**
5. **Check ToolContext protocol shows `ScopedArtifacts`** in REACT_PLANNER_INTEGRATION_GUIDE.md

### Step 3: Run mkdocs build
- Install docs dependencies: `uv pip install -e ".[dev,docs]"`
- Run: `uv run mkdocs build --strict`
- Verify no broken links or build errors for the published pages:
  - `docs/tools/artifacts-and-resources.md` (File 3)
  - `docs/planner/tool-design.md` (File 4)
  - `docs/tools/configuration.md` (File 6)
  - `docs/tools/statestore.md` (File 8)

## Required Code

No standalone code files -- one markdown edit plus verification commands.

## Exit Criteria (Success)
- [ ] `TEMPLATING_QUICKGUIDE.md` contains the "Developer API" blockquote note about `ScopedArtifacts` facade
- [ ] No file uses `ctx.artifacts.put_bytes()` or `ctx.artifacts.put_text()` (these should be `ctx._artifacts.put_bytes()` / `ctx._artifacts.put_text()` or `ctx.artifacts.upload()`)
- [ ] All internal pipeline references use `ctx._artifacts` (with underscore prefix)
- [ ] `REACT_PLANNER_INTEGRATION_GUIDE.md` ToolContext protocol listing shows `ScopedArtifacts`, not `ArtifactStore`
- [ ] `mkdocs build --strict` completes without errors

## Implementation Notes
- **Depends on phases 000, 001, 002, and 003 being complete.** The cross-file verification checks assume all prior edits have been applied.
- `TEMPLATING_QUICKGUIDE.md` is at the repo root and is **excluded** from the published mkdocs site.
- The mkdocs build validates only the files under `docs/` that are included in `mkdocs.yml`. Files 1, 2, 5, and 7 are excluded. Files 3, 4, 6, and 8 are published.
- The `TEMPLATING_QUICKGUIDE.md` file is very large (~86KB). Use exact text matching to locate the edit target.

## Verification Commands
```bash
# --- Step 1 verification: TEMPLATING_QUICKGUIDE.md ---
grep -n "Developer API:" /Users/martin.alonso/Documents/lg/repos/penguiflow/TEMPLATING_QUICKGUIDE.md
grep -n "ScopedArtifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/TEMPLATING_QUICKGUIDE.md

# --- Step 2 verification: Cross-file consistency checks ---

# Check 1: No doc incorrectly uses ctx.artifacts.put_bytes() or ctx.artifacts.put_text()
# (These should only appear as ctx._artifacts.put_bytes/put_text)
echo "=== Checking for incorrect ctx.artifacts.put_ references ==="
grep -rn "ctx\.artifacts\.put_" \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-guide.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/planner/tool-design.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/configuration.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/TEMPLATING_QUICKGUIDE.md \
  /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/statestore.md \
  || echo "PASS: No incorrect ctx.artifacts.put_ references found"

# Check 2: ArtifactStore protocol includes list method
echo "=== Checking for list method in ArtifactStore protocol ==="
grep -n "async def list" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/spec/STATESTORE_IMPLEMENTATION_SPEC.md

# Check 3: ToolContext shows ScopedArtifacts (not ArtifactStore) in guide
echo "=== Checking ToolContext type in REACT_PLANNER_INTEGRATION_GUIDE ==="
grep -n "def artifacts.*ScopedArtifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/REACT_PLANNER_INTEGRATION_GUIDE.md

# Check 4: Internal references use ctx._artifacts
echo "=== Checking internal references use ctx._artifacts ==="
grep -n "ctx\._artifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/artifacts-and-resources.md
grep -n "ctx\._artifacts" /Users/martin.alonso/Documents/lg/repos/penguiflow/docs/tools/configuration.md

# --- Step 3: mkdocs build ---
cd /Users/martin.alonso/Documents/lg/repos/penguiflow && uv pip install -e ".[dev,docs]" && uv run mkdocs build --strict
```
