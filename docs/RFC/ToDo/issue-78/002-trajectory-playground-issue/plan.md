# Plan: Add retry-on-404 to trajectory & event fetching in penguiflow playground UI

## Context

After a successful agent run, the playground UI shows "No trajectory yet" and the "Planner Events" panel stays empty. Both trajectory and planner-event persistence are fire-and-forget in `react_runtime.py` (`_fire_persistence_tasks()` at line 915 spawns two `asyncio.Task`s — `_persist_trajectory` and `_persist_events` — and returns immediately). The UI receives the SSE "done" frame and calls `fetchTrajectory()` / `eventStreamManager.start()` before the saves complete, getting 404s.

The `/events` endpoint (`playground.py:1849`) has the same race: it first checks `store.get_trajectory()` (line 1858-1860) as a guard — if the trajectory isn't persisted yet, it returns 404. Additionally, `store.list_planner_events()` (line 1867) may return empty if events haven't been persisted yet.

## Files to modify

1. `penguiflow/cli/playground_ui/src/lib/services/api.ts` (lines 136-148) — `fetchTrajectory`
2. `penguiflow/cli/playground_ui/src/lib/services/event-stream.ts` (lines 13-83) — `EventStreamManager`

---

## Change 1: `fetchTrajectory` retry

### File

`penguiflow/cli/playground_ui/src/lib/services/api.ts` (lines 136-148)

## Current code

```typescript
export async function fetchTrajectory(
  traceId: string,
  sessionId: string
): Promise<TrajectoryPayload | null> {
  const result = await fetchWithErrorHandling<TrajectoryPayload>(
    `${BASE_URL}/trajectory/${traceId}?session_id=${encodeURIComponent(sessionId)}`
  );
  if (!result.ok) {
    console.error('trajectory fetch failed', result.error);
    return null;
  }
  return result.data;
}
```

## Proposed change

Add a retry loop that retries up to 3 times with 500ms backoff when the response is a 404. Non-404 errors (500, network failures) should fail immediately without retrying.

`ApiError.statusCode` (defined at `api.ts:17`) already carries the HTTP status code, so no changes to `fetchWithErrorHandling` are needed.

```typescript
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

**Note:** The trailing `return null` after the `for` loop is unreachable — the loop always terminates via an explicit `return` inside the body. It is kept for TypeScript type completeness.

## Why this works

- Both `onDone` call sites in `App.svelte` (lines 140 and 193) call `fetchTrajectory` — both benefit automatically with no duplication.
- 404 means "not yet persisted" — retrying after a short delay gives the background save task time to complete.
- Non-404 errors fail fast — no wasted retries on real failures.
- Total worst-case wait: 500 + 1000 + 1500 = 3 seconds, which is acceptable for a background panel.

## Change 1 Tests

Existing tests are in `penguiflow/cli/playground_ui/tests/unit/services/api.test.ts` (lines 161-200). **Keep all existing tests as-is** — they remain valid because the new optional params (`retries`, `delayMs`) have defaults. Add the following **new** test cases inside the existing `describe('fetchTrajectory')` block:

1. **Returns data on first attempt (no retry)** — existing behavior unchanged. `fetch` called 1 time. `console.error` not called.
2. **Returns data after exactly 1 retry** — mock returns 404 on the first call, then 200 on the second. Pass `delayMs = 0` to avoid real wall-clock delays. `fetch` called 2 times. `console.error` not called (intermediate 404s are silent). This is a single test case (not two).
3. **Returns null after exhausting all retries on persistent 404** — mock returns 404 on every call. Pass `retries = 3, delayMs = 0`. `fetch` called 4 times (1 initial + 3 retries). `console.error` called exactly once (on final failure only).
4. **Returns null immediately on non-404 error (e.g., 500) without retrying** — mock returns 500. `fetch` called 1 time. `console.error` called exactly once.

### Test conventions

- Use `delayMs = 0` in all retry test calls to skip `setTimeout` delays. No need for `vi.useFakeTimers()`.
- Spy on `console.error` with `vi.spyOn(console, 'error')` to assert quiet-retry behavior: `console.error` must NOT fire on intermediate 404 retries, only on the final failure.
- **Mock format for HTTP errors**: The retry logic reads `statusCode` from `ApiError`, which `fetchWithErrorHandling` constructs from `response.status`. Mocks must include the `status` field:
  - 404 mock: `{ ok: false, status: 404, statusText: 'Not Found' }`
  - 500 mock: `{ ok: false, status: 500, statusText: 'Internal Server Error' }`
  - Success mock: `{ ok: true, json: () => Promise.resolve(mockData) }`
- **Sequential mock responses**: For retry tests (cases 2 and 3), use `vi.fn().mockResolvedValueOnce(...)` chaining to return different responses on successive calls. Example for case 2:
  ```typescript
  globalThis.fetch = vi.fn()
    .mockResolvedValueOnce({ ok: false, status: 404, statusText: 'Not Found' })
    .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(mockData) });
  ```

---

## Change 2: `EventStreamManager` retry on connection failure

### File

`penguiflow/cli/playground_ui/src/lib/services/event-stream.ts` (lines 13-83)

### Current code

```typescript
class EventStreamManager {
  constructor(private stores: EventStreamStores) {}
  private eventSource: EventSource | null = null;

  start(traceId: string, sessionId: string): void {
    this.close();

    const url = new URL('/events', window.location.origin);
    url.searchParams.set('trace_id', traceId);
    url.searchParams.set('session_id', sessionId);
    url.searchParams.set('follow', 'true');

    this.eventSource = new EventSource(url.toString());

    const listener = (evt: MessageEvent) => { /* ... */ };

    ['event', 'step', 'chunk', 'llm_stream_chunk', 'artifact_chunk', 'artifact_stored'].forEach(type => {
      this.eventSource!.addEventListener(type, listener);
    });

    this.eventSource.onmessage = listener;
    this.eventSource.onerror = () => this.close();
  }

  close(): void { /* ... */ }
}
```

### Problem

The `onerror` handler silently calls `this.close()` with no retry. If `/events` returns 404 (trajectory not persisted yet), the EventSource dies and the "Planner Events" panel stays empty. The server sends a `"connected"` event as the first SSE frame (`playground.py:1877-1880`) — if `onerror` fires before that frame arrives, it's a connection failure (likely 404), not a mid-stream error.

### Proposed change

Split into `start()` (public) and `_connect()` (private). Track a `connected` flag that flips to `true` when the first `"connected"` event arrives. If `onerror` fires before `connected` is set and attempts remain, close and retry after backoff.

```typescript
class EventStreamManager {
  constructor(private stores: EventStreamStores) {}
  private eventSource: EventSource | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  start(traceId: string, sessionId: string, retries = 3, delayMs = 500): void {
    this.close();
    this._connect(traceId, sessionId, 0, retries, delayMs);
  }

  private _connect(
    traceId: string,
    sessionId: string,
    attempt: number,
    retries: number,
    delayMs: number
  ): void {
    const url = new URL('/events', window.location.origin);
    url.searchParams.set('trace_id', traceId);
    url.searchParams.set('session_id', sessionId);
    url.searchParams.set('follow', 'true');

    this.eventSource = new EventSource(url.toString());
    let connected = false;

    const listener = (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;

      const incomingEvent = (evt.type as string) || (data.event as string) || '';

      // Mark connected on first server frame
      if (!connected && data.event === 'connected') {
        connected = true;
      }

      // ... rest of existing listener logic (unchanged) ...
    };

    ['event', 'step', 'chunk', 'llm_stream_chunk', 'artifact_chunk', 'artifact_stored'].forEach(type => {
      this.eventSource!.addEventListener(type, listener);
    });

    this.eventSource.onmessage = listener;
    this.eventSource.onerror = () => {
      this.close();
      if (!connected && attempt < retries) {
        this.retryTimer = setTimeout(
          () => this._connect(traceId, sessionId, attempt + 1, retries, delayMs),
          delayMs * (attempt + 1)
        );
      }
    };
  }

  close(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}
```

### Why this works

- **Pre-connect failures retry** — if `onerror` fires before `connected` is set, it's a 404 or network failure during initial connect. Retrying with backoff gives `_persist_events` time to finish.
- **Post-connect failures don't retry** — once the `"connected"` frame is received, `onerror` means a mid-stream disconnect (server crash, network drop). No retry prevents reconnecting to a broken stream.
- **Stale retry cancellation** — `close()` clears any pending `retryTimer` via `clearTimeout`. This prevents ghost connections when `start()` is called again (new user message) or `close()` is called externally while a retry `setTimeout` is still pending. Without this, the old callback would fire with a stale `traceId` and overwrite the current connection.
- **Same backoff pattern** as `fetchTrajectory`: `delayMs * (attempt + 1)` with defaults `retries = 3, delayMs = 500`. Worst-case 3 seconds.
- **Both `onDone` call sites** in `App.svelte` (lines 144 and 197) call `eventStreamManager.start()` — both benefit automatically.
- **Sequential ordering helps**: `eventStreamManager.start()` runs after `fetchTrajectory()` resolves (lines 140-144, 193-197). The `fetchTrajectory` retry ensures the trajectory is persisted before the event stream connects, so the `/events` trajectory guard (line 1858) is very likely to pass on the first attempt. The retry here is a safety net for edge cases where events are slower to persist than the trajectory.

## Change 2 Tests

Tests are in `penguiflow/cli/playground_ui/tests/unit/services/` — create a new file `event-stream.test.ts` (no existing tests for this module). Use `vi.fn()` to mock `EventSource` on `globalThis`.

### Importing EventStreamManager

The `EventStreamManager` class is **not exported** — only the factory function `createEventStreamManager(stores)` is public (via `event-stream.ts:135` and re-exported from `index.ts`). Tests must import and use `createEventStreamManager` to obtain instances, passing mock stores.

### Mock stores

`createEventStreamManager` requires an `EventStreamStores` object (`Pick<AppStores, 'eventsStore' | 'trajectoryStore' | 'artifactsStore' | 'interactionsStore'>`). Since the retry tests don't deeply exercise store logic, use minimal stubs:

```typescript
function createMockStores(): EventStreamStores {
  return {
    eventsStore: { shouldProcess: vi.fn().mockReturnValue(true), addEvent: vi.fn() },
    trajectoryStore: { addArtifactChunk: vi.fn() },
    artifactsStore: { addArtifact: vi.fn() },
    interactionsStore: { addArtifactChunk: vi.fn() },
  } as unknown as EventStreamStores;
}
```

Import the type if needed: `import type { EventStreamStores } from '$lib/services/event-stream'` (or cast with `as unknown`).

### `window.location.origin`

The `_connect` method uses `new URL('/events', window.location.origin)`. In Vitest with jsdom, `window.location.origin` defaults to `'http://localhost:3000'` (or similar). No explicit mock is needed — jsdom provides a working default. The tests should not assert on the constructed URL.

### EventSource mock pattern

Since `EventSource` is a constructor with event-based API, mock it as a class. The mock must capture both `addEventListener` handlers and property-assigned handlers (`onmessage`, `onerror`) so tests can trigger them:

```typescript
interface MockEventSource {
  addEventListener: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onmessage: ((evt: MessageEvent) => void) | null;
  onerror: (() => void) | null;
  // Test helpers (not part of real EventSource API):
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
      // Also fire onmessage if assigned
      if (mock.onmessage) mock.onmessage(evt);
    },
    _triggerError() {
      if (mock.onerror) mock.onerror();
    },
  };
  return mock;
}
```

Use a `vi.fn()` constructor that returns the mock, and track all created instances for assertions:

```typescript
let mockInstances: MockEventSource[];

beforeEach(() => {
  mockInstances = [];
  globalThis.EventSource = vi.fn(() => {
    const instance = createMockEventSource();
    mockInstances.push(instance);
    return instance;
  }) as unknown as typeof EventSource;
});
```

This lets tests access `mockInstances[0]._triggerError()` to simulate connection errors and `mockInstances[0]._emit('event', { event: 'connected' })` to simulate the server's connected frame.

### Test cases

1. **Connects successfully on first attempt** — call `start()`, emit `{ event: 'connected' }` via `mockInstances[0]._emit('event', { event: 'connected' })`. Verify `EventSource` constructor called once, `mockInstances[0].close` not called.
2. **Retries on pre-connect error** — call `start()`, trigger `mockInstances[0]._triggerError()` immediately (before emitting `connected`). Advance fake timers. Verify `mockInstances.length === 2` (a new `EventSource` was created). Pass `delayMs = 0` and use `vi.useFakeTimers()` + `vi.advanceTimersByTime()`.
3. **Stops retrying after max attempts** — trigger `_triggerError()` on every connection. With `retries = 2, delayMs = 0`, verify `mockInstances.length === 3` (1 initial + 2 retries), then no more after advancing timers further.
4. **Does not retry on post-connect error** — emit `{ event: 'connected' }` via `_emit`, then trigger `_triggerError()`. Verify `mockInstances.length === 1` (no new `EventSource` created).
5. **Cancels pending retry when `start()` is called again** — trigger `_triggerError()` (schedules a retry setTimeout), then immediately call `start()` again with a different `traceId`. Advance fake timers. Verify `mockInstances.length === 2` (the second `start()` call, NOT the stale retry). Verify the second instance's URL contains the new `traceId`, not the old one.
6. **Cancels pending retry when `close()` is called** — trigger `_triggerError()` (schedules a retry), then call `close()`. Advance fake timers. Verify `mockInstances.length === 1` (no retry connection was created).

### Test conventions

- Use `vi.useFakeTimers()` in these tests — unlike `fetchTrajectory` tests that can pass `delayMs = 0`, the `EventStreamManager` uses `setTimeout` internally and the test needs to advance timers to trigger the retry callbacks.
- Restore real timers in `afterEach` with `vi.useRealTimers()`.

---

## Build

After modifying `api.ts` and `event-stream.ts`, rebuild the static assets so the playground serves the updated code:

```bash
cd penguiflow/cli/playground_ui
npm install   # if needed
npm run build
```

This regenerates `penguiflow/cli/playground_ui/dist/`, which is what the playground server serves at runtime (`playground.py:827`, `dev.py:51-57`). **The dist files must be committed** — skipping this step will leave the running playground on stale code.

## Verification

After implementing, reproduce by sending a message in the playground and confirming:
- The "Execution Trajectory" panel populates after the agent finishes.
- The "Planner Events" panel populates with events after the agent finishes.
- No `trajectory fetch failed` errors in the browser console.
- No EventSource connection errors in the browser console.
- Backend logs show `GET /trajectory/{trace_id}` eventually returning 200.
- Backend logs show `GET /events?trace_id=...` eventually returning 200 (SSE stream opens).
