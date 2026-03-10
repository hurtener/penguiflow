# Phase 004: Build static assets and final verification

## Objective
Rebuild the playground UI's static assets so the running playground server serves the updated code with retry logic. Verify all tests pass before building, then regenerate the `dist/` directory. The dist files must be committed -- without this step, the running playground would serve stale code without the retry fixes.

## Tasks
1. Run the full test suite to confirm all changes are working.
2. Install dependencies (if needed) and rebuild the static assets.
3. Verify the dist directory was regenerated.

## Detailed Steps

### Step 1: Run the full test suite
- Run `npm run test -- --run` from the `penguiflow/cli/playground_ui` directory.
- All tests must pass, including the 4 new `fetchTrajectory` retry tests and the 6 new `EventStreamManager` retry tests.
- If any test fails, stop and fix before proceeding to the build step.

### Step 2: Rebuild static assets
- Run `npm install` from `penguiflow/cli/playground_ui` (installs any missing dependencies).
- Run `npm run build` from `penguiflow/cli/playground_ui`.
- This invokes `vite build`, which regenerates `penguiflow/cli/playground_ui/dist/`.

### Step 3: Verify the dist directory
- Confirm the `dist/` directory exists and contains updated files.
- Check that the built JavaScript files contain evidence of the retry logic (e.g., search for the retry-related patterns in the bundled output).

## Exit Criteria (Success)
- [ ] All tests pass: `npm run test -- --run` exits with code 0.
- [ ] `npm run build` completes without errors.
- [ ] The `penguiflow/cli/playground_ui/dist/` directory exists and contains updated build artifacts.
- [ ] The built JS bundle contains the retry logic from both `api.ts` and `event-stream.ts`.

## Implementation Notes
- The `dist/` directory is what the playground server serves at runtime (`playground.py:827`, `dev.py:51-57`). If this directory is not rebuilt, the running playground will use stale code without the retry fixes.
- The dist files should be committed to the repository so that users running `penguiflow dev` get the updated playground without needing to rebuild.
- This phase modifies no source files -- it only regenerates build artifacts.
- The build command is `vite build` (invoked via `npm run build` as defined in `package.json`).

## Verification Commands
```bash
# Run full test suite
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run 2>&1 | tail -20

# Install deps and build
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm install 2>&1 | tail -5 && npm run build 2>&1 | tail -10

# Verify dist directory exists and has recent files
ls -la /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/dist/ 2>&1 | head -10

# Verify the built bundle contains retry-related code (search for attempt pattern)
grep -r "attempt" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/dist/assets/*.js 2>/dev/null | head -5 || echo "Check dist manually"
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-06

### Summary of Changes
- **Step 1 -- Full test suite:** Ran `npm run test -- --run` from `penguiflow/cli/playground_ui`. All 394 tests across 33 test files passed, including the 4 `fetchTrajectory` retry tests (from phase-001) and the 6 `EventStreamManager` retry tests (from phase-003). Exit code 0.
- **Step 2 -- Dependency install and build:** Ran `npm install` (no new dependencies needed, all already present). Ran `npm run build` which invoked `vite build` and completed successfully in 11.44s, regenerating the entire `dist/` directory with 159 asset files.
- **Step 3 -- Dist directory verification:** Confirmed the `dist/` directory exists with files timestamped Mar 6 15:01 (current build time). Verified the main bundle (`dist/assets/index-O5eR_8h9.js`) contains retry logic patterns from both source files:
  - `api.ts` retry logic: `.statusCode!==404` pattern (1 occurrence)
  - `event-stream.ts` retry logic: `retryTimer` pattern (1 occurrence) and `_connect` pattern (1 occurrence)
- The build regenerated all JS chunks with new content hashes (old files like `index-DH_8uZM6.js` replaced by `index-O5eR_8h9.js`), confirming the dist reflects the updated source code.

### Key Considerations
- The build produces Svelte compiler warnings about `state_referenced_locally` in `McpApp.svelte` (lines 53-55). These are pre-existing warnings unrelated to the retry logic changes and do not affect the build output.
- Vite's chunk size warning for `Plotly-QqLAS5Vh.js` (4.6MB) is also pre-existing and not related to this phase's changes.
- The `npm run test` script in `package.json` is defined as `vitest run`, so `npm run test -- --run` effectively passes `--run` twice. This is harmless -- vitest ignores the redundant flag -- but the canonical way to run tests non-interactively here is just `npm run test`.

### Assumptions
- The dist files are intended to be committed to the repository, as stated in the phase file. This phase only regenerated them; the actual `git add` and `git commit` are left to the user or a subsequent step, as the phase explicitly says "This phase modifies no source files -- it only regenerates build artifacts."
- The `node_modules` directory was already populated from previous phases. `npm install` confirmed all dependencies were up to date.
- The vite build configuration in `vite.config.js` was not modified; the standard `vite build` command was used as-is.

### Deviations from Plan
None. All three steps (test, build, verify) were executed exactly as specified. All four exit criteria are satisfied.

### Potential Risks & Reviewer Attention Points
- **Dist file hashes changed:** The rebuild produced entirely new content hashes for all JS chunks (e.g., `index-DH_8uZM6.js` became `index-O5eR_8h9.js`). This is expected because even minor source changes cause Vite to regenerate different hashes. However, this means a large number of files will appear in the git diff (68 files: old hash files deleted, new hash files added). This is normal for Vite builds.
- **Old dist files still tracked by git:** The old files (e.g., `dist/assets/index-DH_8uZM6.js`) show as "deleted" in `git status` while the new files show as "untracked." When staging for commit, both the deletions and the new files need to be included (e.g., using `git add dist/`).
- **CSS files unchanged:** The CSS assets in `dist/assets/` retained their original filenames and content hashes, which is expected since no CSS was modified in the retry-logic phases.

### Files Modified
- **Regenerated:** `penguiflow/cli/playground_ui/dist/` -- entire directory rebuilt by `vite build`. Key files:
  - `dist/index.html` (updated asset references)
  - `dist/assets/index-O5eR_8h9.js` (main bundle, contains retry logic from both `api.ts` and `event-stream.ts`)
  - ~60 other JS chunk files with new content hashes
- **Modified:** `docs/RFC/ToDo/issue-78/002-trajectory-playground-issue/phases/phase-004.md` (appended implementation notes)
