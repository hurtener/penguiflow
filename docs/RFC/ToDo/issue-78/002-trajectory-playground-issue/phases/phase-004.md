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
