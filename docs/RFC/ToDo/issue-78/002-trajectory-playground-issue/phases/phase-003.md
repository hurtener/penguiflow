# Phase 003: Add tests for `EventStreamManager` retry logic

## Objective
Create a new test file `event-stream.test.ts` with 6 test cases covering the retry-on-connection-failure behavior introduced in Phase 002. These tests verify: successful first connection, retry on pre-connect error, max retry exhaustion, no retry on post-connect error, stale retry cancellation on re-start, and retry cancellation on close.

## Tasks
1. Create the test file with mock infrastructure (MockEventSource, mock stores, EventSource constructor mock).
2. Implement 6 test cases covering all retry scenarios.

## Detailed Steps

### Step 1: Create the test file with mock infrastructure
- Create `penguiflow/cli/playground_ui/tests/unit/services/event-stream.test.ts`.
- Import `describe, it, expect, beforeEach, afterEach, vi` from `vitest`.
- Import `createEventStreamManager` from `$lib/services/event-stream`.
- Import type `EventStreamStores` if needed (or cast with `as unknown`).
- Define a `MockEventSource` interface with: `addEventListener`, `close`, `onmessage`, `onerror`, `_emit(type, data)`, `_triggerError()`.
- Define a `createMockEventSource()` factory that returns a `MockEventSource` with a `listeners` registry.
- Define a `createMockStores()` factory that returns minimal stub stores.
- In `beforeEach`: create a `mockInstances` array, mock `globalThis.EventSource` as a `vi.fn()` constructor that pushes to `mockInstances`, call `vi.useFakeTimers()`.
- In `afterEach`: call `vi.useRealTimers()` and `vi.restoreAllMocks()`.

### Step 2: Test -- connects successfully on first attempt
- Call `manager.start('trace-1', 'session-1', 3, 100)`.
- Emit `{ event: 'connected' }` via `mockInstances[0]._emit('event', { event: 'connected' })`.
- Assert `EventSource` constructor was called once.
- Assert `mockInstances[0].close` was not called.

### Step 3: Test -- retries on pre-connect error
- Call `manager.start('trace-1', 'session-1', 3, 100)`.
- Trigger `mockInstances[0]._triggerError()` (before emitting `connected`).
- Assert `mockInstances[0].close` was called (the failed connection is closed).
- Advance timers by 100ms (`delayMs * (0 + 1)`).
- Assert `mockInstances.length === 2` (a new EventSource was created for the retry).

### Step 4: Test -- stops retrying after max attempts
- Call `manager.start('trace-1', 'session-1', 2, 100)`.
- Trigger `_triggerError()` on `mockInstances[0]`, advance timers by 100ms.
- Trigger `_triggerError()` on `mockInstances[1]`, advance timers by 200ms.
- Trigger `_triggerError()` on `mockInstances[2]`, advance timers by 1000ms.
- Assert `mockInstances.length === 3` (1 initial + 2 retries, no more).

### Step 5: Test -- does not retry on post-connect error
- Call `manager.start('trace-1', 'session-1', 3, 100)`.
- Emit `{ event: 'connected' }` via `mockInstances[0]._emit('event', { event: 'connected' })`.
- Trigger `mockInstances[0]._triggerError()`.
- Advance timers by 1000ms.
- Assert `mockInstances.length === 1` (no retry connection created).

### Step 6: Test -- cancels pending retry when `start()` is called again
- Call `manager.start('trace-old', 'session-1', 3, 100)`.
- Trigger `mockInstances[0]._triggerError()` (schedules a retry with traceId `trace-old`).
- Immediately call `manager.start('trace-new', 'session-1', 3, 100)` (this calls `close()` which clears the pending timer, then starts a fresh connection).
- Advance timers by 1000ms.
- Assert `mockInstances.length === 2` (one from first `start`, one from second `start` -- the stale retry was cancelled).
- Assert the second EventSource constructor was called with a URL containing `trace-new`.

### Step 7: Test -- cancels pending retry when `close()` is called
- Call `manager.start('trace-1', 'session-1', 3, 100)`.
- Trigger `mockInstances[0]._triggerError()` (schedules a retry).
- Call `manager.close()`.
- Advance timers by 1000ms.
- Assert `mockInstances.length === 1` (no retry connection was created).

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/tests/unit/services/event-stream.test.ts

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createEventStreamManager } from '$lib/services/event-stream';

interface MockEventSource {
  addEventListener: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onmessage: ((evt: MessageEvent) => void) | null;
  onerror: (() => void) | null;
  _emit(type: string, data: Record<string, unknown>): void;
  _triggerError(): void;
}

function createMockEventSource(): MockEventSource {
  const listeners: Record<string, ((evt: MessageEvent) => void)[]> = {};
  const mock: MockEventSource = {
    addEventListener: vi.fn((type: string, fn: (evt: MessageEvent) => void) => {
      (listeners[type] ??= []).push(fn);
    }),
    close: vi.fn(),
    onmessage: null,
    onerror: null,
    _emit(type: string, data: Record<string, unknown>) {
      const evt = { type, data: JSON.stringify(data) } as unknown as MessageEvent;
      (listeners[type] ?? []).forEach(fn => fn(evt));
      if (mock.onmessage) mock.onmessage(evt);
    },
    _triggerError() {
      if (mock.onerror) mock.onerror();
    },
  };
  return mock;
}

function createMockStores() {
  return {
    eventsStore: { shouldProcess: vi.fn().mockReturnValue(true), addEvent: vi.fn() },
    trajectoryStore: { addArtifactChunk: vi.fn() },
    artifactsStore: { addArtifact: vi.fn() },
    interactionsStore: { addArtifactChunk: vi.fn() },
  } as unknown as Parameters<typeof createEventStreamManager>[0];
}

describe('EventStreamManager', () => {
  let mockInstances: MockEventSource[];

  beforeEach(() => {
    vi.useFakeTimers();
    mockInstances = [];
    globalThis.EventSource = vi.fn(() => {
      const instance = createMockEventSource();
      mockInstances.push(instance);
      return instance;
    }) as unknown as typeof EventSource;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('connects successfully on first attempt', () => {
    const manager = createEventStreamManager(createMockStores());
    manager.start('trace-1', 'session-1', 3, 100);

    expect(mockInstances).toHaveLength(1);
    mockInstances[0]._emit('event', { event: 'connected' });

    expect(mockInstances[0].close).not.toHaveBeenCalled();
    expect(EventSource).toHaveBeenCalledTimes(1);
  });

  it('retries on pre-connect error', () => {
    const manager = createEventStreamManager(createMockStores());
    manager.start('trace-1', 'session-1', 3, 100);

    expect(mockInstances).toHaveLength(1);

    // Error before connected frame
    mockInstances[0]._triggerError();
    expect(mockInstances[0].close).toHaveBeenCalled();

    // Advance timer to trigger retry (delayMs * (attempt + 1) = 100 * 1)
    vi.advanceTimersByTime(100);
    expect(mockInstances).toHaveLength(2);
  });

  it('stops retrying after max attempts', () => {
    const manager = createEventStreamManager(createMockStores());
    manager.start('trace-1', 'session-1', 2, 100);

    // Initial attempt fails
    mockInstances[0]._triggerError();
    vi.advanceTimersByTime(100); // retry 1

    // Retry 1 fails
    mockInstances[1]._triggerError();
    vi.advanceTimersByTime(200); // retry 2

    // Retry 2 fails -- no more retries
    mockInstances[2]._triggerError();
    vi.advanceTimersByTime(1000);

    // 1 initial + 2 retries = 3 total, no more
    expect(mockInstances).toHaveLength(3);
  });

  it('does not retry on post-connect error', () => {
    const manager = createEventStreamManager(createMockStores());
    manager.start('trace-1', 'session-1', 3, 100);

    // Receive connected frame
    mockInstances[0]._emit('event', { event: 'connected' });

    // Error after connected
    mockInstances[0]._triggerError();
    vi.advanceTimersByTime(1000);

    // No retry -- still only 1 instance
    expect(mockInstances).toHaveLength(1);
  });

  it('cancels pending retry when start() is called again', () => {
    const manager = createEventStreamManager(createMockStores());
    manager.start('trace-old', 'session-1', 3, 100);

    // Error schedules a retry for trace-old
    mockInstances[0]._triggerError();

    // Immediately start a new connection with different traceId
    manager.start('trace-new', 'session-1', 3, 100);

    // Advance timers -- stale retry should NOT fire
    vi.advanceTimersByTime(1000);

    // 2 instances: one from first start (closed on error), one from second start
    expect(mockInstances).toHaveLength(2);

    // Verify the second instance used the new traceId
    const secondCallUrl = (EventSource as unknown as ReturnType<typeof vi.fn>).mock.calls[1][0] as string;
    expect(secondCallUrl).toContain('trace-new');
    expect(secondCallUrl).not.toContain('trace-old');
  });

  it('cancels pending retry when close() is called', () => {
    const manager = createEventStreamManager(createMockStores());
    manager.start('trace-1', 'session-1', 3, 100);

    // Error schedules a retry
    mockInstances[0]._triggerError();

    // Explicitly close
    manager.close();

    // Advance timers -- retry should NOT fire
    vi.advanceTimersByTime(1000);

    // Still only 1 instance (no retry connection created)
    expect(mockInstances).toHaveLength(1);
  });
});
```

## Exit Criteria (Success)
- [ ] File `penguiflow/cli/playground_ui/tests/unit/services/event-stream.test.ts` exists.
- [ ] The file contains 6 test cases inside a `describe('EventStreamManager')` block.
- [ ] Test "connects successfully on first attempt" passes.
- [ ] Test "retries on pre-connect error" passes.
- [ ] Test "stops retrying after max attempts" passes.
- [ ] Test "does not retry on post-connect error" passes.
- [ ] Test "cancels pending retry when start() is called again" passes.
- [ ] Test "cancels pending retry when close() is called" passes.
- [ ] All tests pass: `npm run test -- --run tests/unit/services/event-stream.test.ts`.
- [ ] All existing tests in the project still pass: `npm run test -- --run`.

## Implementation Notes
- `vi.useFakeTimers()` is required because `EventStreamManager` uses `setTimeout` internally and the test needs to advance timers to trigger retry callbacks. Unlike the `fetchTrajectory` tests (which can pass `delayMs = 0` to skip delays via `await`), the EventSource retry uses `setTimeout` which must be controlled with fake timers.
- The `MockEventSource` captures both `addEventListener` handlers and property-assigned handlers (`onmessage`, `onerror`). The `_emit` helper fires both registered listeners and `onmessage`.
- The mock stores use minimal stubs -- the retry tests do not deeply exercise store logic.
- `createEventStreamManager` is the public factory (the class itself is not exported). Tests must use this factory to obtain instances.
- In jsdom (Vitest's default environment), `window.location.origin` has a working default, so no explicit mock is needed.

## Verification Commands
```bash
# Run only the new event-stream tests
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run tests/unit/services/event-stream.test.ts 2>&1 | tail -30

# Run all service tests to confirm no regressions
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run tests/unit/services/ 2>&1 | tail -30

# Run the full test suite
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run 2>&1 | tail -30
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-06

### Summary of Changes
- Created `penguiflow/cli/playground_ui/tests/unit/services/event-stream.test.ts` with 6 test cases inside a `describe('EventStreamManager')` block.
- Test 1: "connects successfully on first attempt" -- verifies single EventSource instantiation and no close on successful connect.
- Test 2: "retries on pre-connect error" -- verifies close of failed connection and creation of a second EventSource after timer advance.
- Test 3: "stops retrying after max attempts" -- verifies exactly 3 instances (1 initial + 2 retries) when `retries=2`.
- Test 4: "does not retry on post-connect error" -- verifies no retry after the `connected` event has been received.
- Test 5: "cancels pending retry when start() is called again" -- verifies stale retry timer is cleared and new connection uses the new traceId.
- Test 6: "cancels pending retry when close() is called" -- verifies explicit close prevents the scheduled retry from firing.

### Key Considerations
- **Constructor mock approach:** The plan specified `vi.fn(() => {...})` as the `EventSource` constructor mock. However, `vi.fn()` creates an arrow function internally, which cannot be used with `new` (throws "is not a constructor"). The implementation uses a proper `class FakeEventSource` definition instead, with a separate `constructorSpy` (`vi.fn()`) called inside the constructor to track invocations and capture URL arguments.
- **Closure correctness for `_emit` and `_triggerError`:** The plan's `createMockEventSource()` factory uses a plain object literal where `_triggerError` references `mock.onerror` via closure. This works when the mock object IS the EventSource instance. However, if the mock were copied via `Object.assign` into a `new`-constructed object, the closures would reference the original object (whose `onerror` stays `null`), not the constructed one. Using a class avoids this entirely because `_emit` and `_triggerError` use `this`, which correctly refers to the constructed instance.
- **`createMockStores()`** is identical to the plan -- minimal stubs sufficient for retry-focused tests.
- **Fake timers** are used exactly as specified, with `vi.useFakeTimers()` in `beforeEach` and `vi.useRealTimers()` in `afterEach`.

### Assumptions
- The `EventStreamManager.close()` method in the production code calls `this.eventSource.close()` before nulling out the reference. This means the mock's `close` spy is called by the manager's internal `close()`, which is separate from test-level assertions on the mock.
- The global test setup (`tests/setup.ts`) stubs `globalThis.EventSource` with its own `MockEventSource`. The test's `beforeEach` overrides this with the local class mock, and `vi.restoreAllMocks()` in `afterEach` handles cleanup. Since the setup runs before each test file and `beforeEach` runs before each test, the override order is correct.
- `window.location.origin` defaults to `http://localhost:3000` or similar in jsdom, so `new URL('/events', window.location.origin)` works without explicit mocking.

### Deviations from Plan
- **Mock infrastructure restructured:** Instead of a standalone `createMockEventSource()` factory function with `vi.fn()` as the constructor, the implementation uses an inline `class FakeEventSource` in `beforeEach` with a `constructorSpy` for call tracking. This was necessary because `vi.fn()` cannot be used as a constructor with `new`. The `MockEventSource` interface is retained for type safety on `mockInstances`.
- **`constructorSpy` instead of `EventSource` spy:** The plan's test code references `expect(EventSource).toHaveBeenCalledTimes(1)` and `(EventSource as ...).mock.calls[1][0]`. Since the class itself is not a `vi.fn()`, these were changed to use `constructorSpy.toHaveBeenCalledTimes(1)` and `constructorSpy.mock.calls[1][0]` respectively. The semantic intent (verifying constructor call count and URL arguments) is preserved.

### Potential Risks & Reviewer Attention Points
- The `vi.restoreAllMocks()` in `afterEach` may not restore the original `globalThis.EventSource` that was set in the global setup file (`tests/setup.ts`). Since each test in `beforeEach` re-assigns `globalThis.EventSource` anyway, this is not an issue for this test file. However, if test ordering matters across files, verify that the global setup's `EventSource` stub is re-applied. Vitest re-runs setup files per test file by default, so this should be fine.
- The `_listeners` property on `FakeEventSource` is `private`, which means tests cannot directly inspect registered listeners. This is intentional -- tests only use `_emit` and `_triggerError` to exercise the manager's behavior indirectly.

### Files Modified
- **Created:** `penguiflow/cli/playground_ui/tests/unit/services/event-stream.test.ts`
- **Modified:** `docs/RFC/ToDo/issue-78/002-trajectory-playground-issue/phases/phase-003.md` (appended implementation notes)
