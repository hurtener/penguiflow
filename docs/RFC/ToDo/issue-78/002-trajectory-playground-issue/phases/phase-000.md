# Phase 000: Add retry-on-404 to `fetchTrajectory` in `api.ts`

## Objective
Modify the `fetchTrajectory` function in the playground UI's API service to retry up to 3 times with linear backoff when the server returns a 404. This addresses the race condition where the UI requests the trajectory before the backend has finished persisting it. Non-404 errors fail immediately without retrying.

## Tasks
1. Update the `fetchTrajectory` function signature to accept optional `retries` and `delayMs` parameters.
2. Replace the single fetch call with a retry loop that only retries on 404 responses.
3. Ensure `console.error` is only called on the final failure, not on intermediate 404 retries.

## Detailed Steps

### Step 1: Update the function signature
- Open `penguiflow/cli/playground_ui/src/lib/services/api.ts`.
- Locate the `fetchTrajectory` function at lines 136-148.
- Add two optional parameters: `retries = 3` and `delayMs = 500`.

### Step 2: Replace the function body with a retry loop
- Extract the URL construction to a `const url` variable before the loop.
- Wrap the fetch call in a `for` loop from `attempt = 0` to `attempt <= retries`.
- On success (`result.ok`), return `result.data` immediately.
- On failure, check if `result.error.statusCode !== 404` OR `attempt === retries`:
  - If either is true, log the error with `console.error` and return `null`.
  - Otherwise (it is a 404 and retries remain), `await` a delay of `delayMs * (attempt + 1)` milliseconds before the next iteration.
- Add a trailing `return null` after the loop for TypeScript type completeness (unreachable).

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/api.ts
// Replace lines 136-148 (the fetchTrajectory function) with:

/**
 * Fetch execution trajectory for a trace
 */
export async function fetchTrajectory(
  traceId: string,
  sessionId: string,
  retries = 3,
  delayMs = 500
): Promise<TrajectoryPayload | null> {
  const url = `${BASE_URL}/trajectory/${traceId}?session_id=${encodeURIComponent(sessionId)}`;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const result = await fetchWithErrorHandling<TrajectoryPayload>(url);
    if (result.ok) return result.data;
    if (result.error.statusCode !== 404 || attempt === retries) {
      console.error('trajectory fetch failed', result.error);
      return null;
    }
    await new Promise(r => setTimeout(r, delayMs * (attempt + 1)));
  }
  return null;
}
```

## Exit Criteria (Success)
- [ ] `fetchTrajectory` in `api.ts` accepts optional `retries` and `delayMs` parameters with defaults `3` and `500`.
- [ ] The function retries only on 404 errors, up to `retries` times.
- [ ] Non-404 errors cause immediate return of `null` without retrying.
- [ ] `console.error` is called only on the final failure (not on intermediate 404 retries).
- [ ] The backoff delay is `delayMs * (attempt + 1)` -- linear increase.
- [ ] Existing call sites in `App.svelte` (which pass only `traceId` and `sessionId`) continue to work without changes due to default parameter values.
- [ ] No TypeScript compilation errors in `api.ts`.

## Implementation Notes
- The `ApiError.statusCode` property (defined at `api.ts:17`) already carries the HTTP status code, so no changes to `fetchWithErrorHandling` are needed.
- Both `onDone` call sites in `App.svelte` (lines 140 and 193) call `fetchTrajectory` with just `traceId` and `sessionId` -- the new optional params with defaults ensure backward compatibility.
- The trailing `return null` after the `for` loop is unreachable but required for TypeScript to infer the return type correctly.
- Total worst-case wait with defaults: 500 + 1000 + 1500 = 3000ms.

## Verification Commands
```bash
# Verify TypeScript compiles without errors
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npx tsc --noEmit 2>&1 | head -20

# Verify the function signature is correct
grep -n "export async function fetchTrajectory" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/api.ts

# Verify retry loop exists
grep -n "for (let attempt" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/api.ts

# Run existing tests to confirm no regressions
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run tests/unit/services/api.test.ts 2>&1 | tail -20
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-06

### Summary of Changes
- **`penguiflow/cli/playground_ui/src/lib/services/api.ts`**: Replaced the `fetchTrajectory` function (lines 136-148) with a retry-on-404 version that accepts optional `retries` (default 3) and `delayMs` (default 500) parameters, implements a for-loop with linear backoff that only retries on 404 responses, and logs errors only on the final failure.

### Key Considerations
- The implementation exactly matches the code specified in the phase file's "Required Code" section. No deviations were needed since the plan was precise and complete.
- The existing `fetchWithErrorHandling` function already populates `ApiError.statusCode` from `response.status`, so no changes were needed to the error handling infrastructure.
- The URL is extracted to a `const` before the loop to avoid reconstructing the same string on each attempt.
- The `for` loop uses `attempt <= retries` (not `< retries`), meaning with the default `retries = 3` there are 4 total attempts (1 initial + 3 retries), consistent with the plan's description and the worst-case timing of 500 + 1000 + 1500 = 3000ms (3 delay periods between 4 attempts).

### Assumptions
- The existing test "returns null on error" (line 193 in `api.test.ts`) mocks `fetch` returning `{ ok: false }` without a `status` property. This results in `ApiError.statusCode` being `undefined`, which is `!== 404`, so the function returns `null` immediately without retrying. This is correct behavior and the test continues to pass.
- No new tests were added for the retry behavior because the phase file did not call for them and the existing 32 tests all pass. Adding retry-specific tests (e.g., verifying 404 retries, verifying non-404 immediate failure, verifying delay timing) would be valuable but is outside the scope of this phase.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The existing test for error behavior (`returns null on error`) passes because the mock lacks a `status` property, making `statusCode` be `undefined`. If that test were updated to mock a 404 status, it would trigger retry behavior and potentially cause test timing issues due to the `setTimeout` delays. Future test updates should pass `retries = 0` or use `vi.useFakeTimers()` to avoid slow tests.
- The `npx tsc --noEmit` verification command fails with a pre-existing `TS2688: Cannot find type definition file for 'node'` error unrelated to this change. TypeScript correctness was verified instead via `npx svelte-check`, which reported 0 errors.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/api.ts` (modified)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-78/002-trajectory-playground-issue/phases/phase-000.md` (appended implementation notes)
