# Phase 001: Add tests for `fetchTrajectory` retry logic

## Objective
Add 4 new test cases to the existing `describe('fetchTrajectory')` block in `api.test.ts` to cover the retry-on-404 behavior introduced in Phase 000. These tests verify: unchanged success-on-first-attempt behavior, success after one retry, null after exhausting all retries on persistent 404, and immediate failure without retry on non-404 errors.

## Tasks
1. Add a test that confirms data is returned on the first attempt without retrying.
2. Add a test that confirms data is returned after exactly 1 retry (first call returns 404, second returns 200).
3. Add a test that confirms null is returned after exhausting all retries on persistent 404.
4. Add a test that confirms null is returned immediately on non-404 errors (e.g., 500) without retrying.

## Detailed Steps

### Step 1: Add test for success on first attempt (no retry needed)
- Inside the existing `describe('fetchTrajectory')` block in `api.test.ts`, add a new `it` block.
- Mock `globalThis.fetch` to return `{ ok: true, json: () => Promise.resolve(mockData) }`.
- Call `fetchTrajectory('trace-1', 'session-1', 3, 0)`.
- Assert `fetch` was called exactly 1 time.
- Assert `console.error` was NOT called (spy on it with `vi.spyOn(console, 'error')`).
- Assert the result equals `mockData`.

### Step 2: Add test for success after exactly 1 retry
- Mock `globalThis.fetch` with chained `.mockResolvedValueOnce()` calls:
  - First call: `{ ok: false, status: 404, statusText: 'Not Found' }`.
  - Second call: `{ ok: true, json: () => Promise.resolve(mockData) }`.
- Call `fetchTrajectory('trace-1', 'session-1', 3, 0)` (use `delayMs = 0` to avoid real delays).
- Assert `fetch` was called exactly 2 times.
- Assert `console.error` was NOT called (intermediate 404 retries are silent).
- Assert the result equals `mockData`.

### Step 3: Add test for null after exhausting all retries on persistent 404
- Mock `globalThis.fetch` to always return `{ ok: false, status: 404, statusText: 'Not Found' }`.
- Call `fetchTrajectory('trace-1', 'session-1', 3, 0)`.
- Assert `fetch` was called exactly 4 times (1 initial + 3 retries).
- Assert `console.error` was called exactly once (on the final failure only).
- Assert the result is `null`.

### Step 4: Add test for immediate failure on non-404 error
- Mock `globalThis.fetch` to return `{ ok: false, status: 500, statusText: 'Internal Server Error' }`.
- Call `fetchTrajectory('trace-1', 'session-1', 3, 0)`.
- Assert `fetch` was called exactly 1 time (no retries).
- Assert `console.error` was called exactly once.
- Assert the result is `null`.

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/tests/unit/services/api.test.ts
// Add the following 4 test cases INSIDE the existing `describe('fetchTrajectory', () => { ... })` block,
// after the existing 3 tests (after the "returns null on error" test around line 200).

    it('returns data on first attempt without retrying', async () => {
      const errorSpy = vi.spyOn(console, 'error');
      const mockData = { steps: [{ action: { next_node: 'n1' } }] };

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(1);
      expect(errorSpy).not.toHaveBeenCalled();
      expect(result).toEqual(mockData);
      errorSpy.mockRestore();
    });

    it('returns data after exactly 1 retry on 404', async () => {
      const errorSpy = vi.spyOn(console, 'error');
      const mockData = { steps: [{ action: { next_node: 'n1' } }] };

      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({ ok: false, status: 404, statusText: 'Not Found' })
        .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(mockData) });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(2);
      expect(errorSpy).not.toHaveBeenCalled();
      expect(result).toEqual(mockData);
      errorSpy.mockRestore();
    });

    it('returns null after exhausting all retries on persistent 404', async () => {
      const errorSpy = vi.spyOn(console, 'error');

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false, status: 404, statusText: 'Not Found'
      });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(4);
      expect(errorSpy).toHaveBeenCalledTimes(1);
      expect(result).toBeNull();
      errorSpy.mockRestore();
    });

    it('returns null immediately on non-404 error without retrying', async () => {
      const errorSpy = vi.spyOn(console, 'error');

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false, status: 500, statusText: 'Internal Server Error'
      });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(1);
      expect(errorSpy).toHaveBeenCalledTimes(1);
      expect(result).toBeNull();
      errorSpy.mockRestore();
    });
```

## Exit Criteria (Success)
- [ ] 4 new test cases exist inside the `describe('fetchTrajectory')` block in `api.test.ts`.
- [ ] All existing tests in `api.test.ts` continue to pass (no regressions).
- [ ] The new "returns data on first attempt" test passes and confirms `fetch` is called once.
- [ ] The new "returns data after exactly 1 retry" test passes and confirms `fetch` is called twice.
- [ ] The new "returns null after exhausting retries" test passes and confirms `fetch` is called 4 times and `console.error` is called once.
- [ ] The new "returns null immediately on non-404 error" test passes and confirms `fetch` is called once.
- [ ] All tests pass: `npm run test -- --run tests/unit/services/api.test.ts`.

## Implementation Notes
- Use `delayMs = 0` in all retry test calls to avoid real `setTimeout` delays. No need for `vi.useFakeTimers()`.
- Spy on `console.error` with `vi.spyOn(console, 'error')` and restore with `errorSpy.mockRestore()` after each test.
- The mock format for HTTP errors must include a `status` field because `fetchWithErrorHandling` reads `response.status` to construct `ApiError.statusCode`. Missing the `status` field will cause the retry logic to not recognize 404s.
- For sequential mock responses, use `vi.fn().mockResolvedValueOnce(...)` chaining.
- The existing 3 tests in the `fetchTrajectory` block remain valid because the new optional params have defaults.

## Verification Commands
```bash
# Run only the api.test.ts tests
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run tests/unit/services/api.test.ts 2>&1 | tail -30

# Verify all 7 fetchTrajectory tests pass (3 existing + 4 new)
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run tests/unit/services/api.test.ts 2>&1 | grep -c "fetchTrajectory"
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-06

### Summary of Changes
- Added 4 new test cases to the `describe('fetchTrajectory')` block in `penguiflow/cli/playground_ui/tests/unit/services/api.test.ts`:
  1. `returns data on first attempt without retrying` -- verifies fetch called once, no console.error, correct data returned.
  2. `returns data after exactly 1 retry on 404` -- verifies fetch called twice (first 404, second 200), no console.error, correct data returned.
  3. `returns null after exhausting all retries on persistent 404` -- verifies fetch called 4 times (1 initial + 3 retries), console.error called once, null returned.
  4. `returns null immediately on non-404 error without retrying` -- verifies fetch called once (no retry on 500), console.error called once, null returned.

### Key Considerations
- The tests were inserted after the existing "returns null on error" test (line 199 in original) and before the closing of the `describe('fetchTrajectory')` block, preserving the structure.
- All new tests pass `delayMs = 0` as the 4th argument to `fetchTrajectory` to avoid real `setTimeout` delays, as recommended by the phase file.
- The `console.error` spy is created and restored within each test to avoid cross-test interference, especially since `vi.resetAllMocks()` in `beforeEach` handles the fetch mock but not manually created spies.
- The mock responses for error cases include `status` and `statusText` fields because `fetchWithErrorHandling` reads `response.status` to set `ApiError.statusCode`, which the retry logic in `fetchTrajectory` checks against `404`.

### Assumptions
- The code exactly matches what was specified in the phase file's "Required Code" section, as it was well-defined and correct for the implementation.
- The existing 3 tests in the `fetchTrajectory` block continue to work unchanged because `retries` and `delayMs` have default values in the function signature.

### Deviations from Plan
None.

### Potential Risks & Reviewer Attention Points
- The stderr output during test runs shows `console.error` messages from the production code (e.g., "trajectory fetch failed"). This is expected behavior -- the tests that assert `console.error` was called are verifying this exact output. The tests that assert `console.error` was NOT called confirm the retry path stays silent until final failure.
- The existing "returns null on error" test (line 193) uses `{ ok: false }` without a `status` field, so `statusCode` is `undefined`. Since `undefined !== 404`, the retry logic treats this as a non-404 error and does not retry. This is consistent behavior.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/tests/unit/services/api.test.ts` (modified -- added 4 test cases)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-78/002-trajectory-playground-issue/phases/phase-001.md` (modified -- appended implementation notes)
