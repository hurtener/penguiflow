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
