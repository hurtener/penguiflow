# Phase 002: Add retry-on-connection-failure to `EventStreamManager` in `event-stream.ts`

## Objective
Refactor the `EventStreamManager` class to retry the SSE connection when the initial connection fails (e.g., server returns 404 because events are not yet persisted). This splits the logic into a public `start()` method and a private `_connect()` method, tracks whether the server's `"connected"` frame has arrived, and retries with linear backoff only for pre-connect failures. Post-connect errors (mid-stream disconnects) do not retry.

## Tasks
1. Add a `retryTimer` private property to `EventStreamManager`.
2. Refactor `start()` to delegate to a new private `_connect()` method.
3. Add `connected` flag tracking inside `_connect()` based on the server's `"connected"` event.
4. Update the `onerror` handler to retry with backoff on pre-connect failures.
5. Update `close()` to clear any pending retry timer.

## Detailed Steps

### Step 1: Add `retryTimer` property
- Open `penguiflow/cli/playground_ui/src/lib/services/event-stream.ts`.
- In the `EventStreamManager` class, after the `private eventSource` declaration (line 15), add:
  `private retryTimer: ReturnType<typeof setTimeout> | null = null;`

### Step 2: Refactor `start()` to delegate to `_connect()`
- Change the `start` method signature to accept optional `retries = 3` and `delayMs = 500` parameters.
- Move the body of `start()` (everything after `this.close()`) into a new private method `_connect(traceId, sessionId, attempt, retries, delayMs)`.
- Have `start()` call `this.close()` then `this._connect(traceId, sessionId, 0, retries, delayMs)`.

### Step 3: Add `connected` flag inside `_connect()`
- At the top of `_connect()`, declare `let connected = false;`.
- Inside the existing `listener` function, before the existing event processing logic, add a check: if `!connected && data.event === 'connected'`, set `connected = true`.
- This detects the server's first SSE frame (`playground.py:1877-1880` sends a `"connected"` event).

### Step 4: Update `onerror` to retry on pre-connect failures
- Replace the existing `this.eventSource.onerror = () => this.close();` with:
  ```
  this.eventSource.onerror = () => {
    this.close();
    if (!connected && attempt < retries) {
      this.retryTimer = setTimeout(
        () => this._connect(traceId, sessionId, attempt + 1, retries, delayMs),
        delayMs * (attempt + 1)
      );
    }
  };
  ```
- This means: if the error happened before the `"connected"` frame arrived and retries remain, schedule a retry.
- If the error happened after `connected` was set to `true`, no retry (mid-stream disconnect).

### Step 5: Update `close()` to clear retry timer
- At the beginning of the `close()` method, before the existing `if (this.eventSource)` block, add:
  ```
  if (this.retryTimer !== null) {
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }
  ```
- This prevents stale retry callbacks from firing after `close()` or a new `start()` call.

## Required Code

```typescript
// Target file: penguiflow/cli/playground_ui/src/lib/services/event-stream.ts
// Replace the entire EventStreamManager class (lines 13-83) with:

/**
 * Manages the follow EventSource for live event updates
 */
class EventStreamManager {
  constructor(private stores: EventStreamStores) {}
  private eventSource: EventSource | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * Start following events for a trace
   */
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

      // Check if we should process this event
      if (!this.stores.eventsStore.shouldProcess(incomingEvent)) return;

      // Handle artifact chunks
      if (incomingEvent === 'artifact_chunk') {
        const payload = toArtifactChunkPayload(data);
        if (payload.artifact_type === 'ui_component') {
          this.stores.interactionsStore.addArtifactChunk(payload, {});
        } else {
          const streamId = payload.stream_id ?? 'artifact';
          this.stores.trajectoryStore.addArtifactChunk(streamId, payload.chunk);
        }
      }

      // Handle artifact_stored - add to artifacts store for download
      if (incomingEvent === 'artifact_stored') {
        const stored = toArtifactStoredEvent(data);
        if (stored) {
          this.stores.artifactsStore.addArtifact(stored);
        }
      }

      // Skip llm_stream_chunk to avoid flooding
      if (incomingEvent === 'llm_stream_chunk') return;

      // Add to events
      this.stores.eventsStore.addEvent(data, incomingEvent || 'event');
    };

    // Register for multiple event types
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

  /**
   * Close the EventSource connection
   */
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

## Exit Criteria (Success)
- [ ] `EventStreamManager` has a `private retryTimer` property initialized to `null`.
- [ ] `start()` accepts optional `retries = 3` and `delayMs = 500` parameters.
- [ ] `start()` calls `this.close()` then delegates to `this._connect()` with `attempt = 0`.
- [ ] `_connect()` is a private method that creates the `EventSource` and sets up listeners.
- [ ] A `let connected = false` flag is declared inside `_connect()`.
- [ ] The listener sets `connected = true` when `data.event === 'connected'` is received.
- [ ] The `onerror` handler calls `this.close()`, then retries via `setTimeout` only if `!connected && attempt < retries`.
- [ ] The backoff delay is `delayMs * (attempt + 1)`.
- [ ] `close()` clears `this.retryTimer` via `clearTimeout` before closing the `EventSource`.
- [ ] All existing event-processing logic (artifact chunks, artifact_stored, llm_stream_chunk skip, addEvent) is preserved exactly.
- [ ] No TypeScript compilation errors in `event-stream.ts`.
- [ ] Existing call sites in `App.svelte` that call `eventStreamManager.start(traceId, sessionId)` continue to work without changes.

## Implementation Notes
- The `_connect` method is private -- it is not part of the public API and should not be exported or called from outside the class.
- The `connected` flag is scoped to each `_connect()` invocation (not a class property). This means each connection attempt has its own flag, which is correct because a retry creates a new connection.
- The `close()` method must clear the retry timer BEFORE closing the EventSource. This ordering prevents a race where `close()` nullifies `this.eventSource` but a pending `setTimeout` callback still fires and creates a new connection.
- The `retryTimer` is stored as a class property (not a local variable) so that `close()` and `start()` can cancel it from outside the `_connect()` scope.
- The server sends a `"connected"` event as the first SSE frame (`playground.py:1877-1880`). If `onerror` fires before that frame, the connection failed during setup (likely a 404).

## Verification Commands
```bash
# Verify TypeScript compiles without errors
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npx tsc --noEmit 2>&1 | head -20

# Verify the start method signature
grep -n "start(" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/event-stream.ts

# Verify _connect method exists
grep -n "_connect(" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/event-stream.ts

# Verify retryTimer property exists
grep -n "retryTimer" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/event-stream.ts

# Verify close() clears retry timer
grep -A2 "clearTimeout" /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/event-stream.ts

# Run existing tests to confirm no regressions
cd /Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui && npm run test -- --run 2>&1 | tail -20
```

---

## Implementation Notes

**Implemented by:** phase-implementer agent
**Date:** 2026-03-06

### Summary of Changes
- Refactored the `EventStreamManager` class in `event-stream.ts` to add retry-on-connection-failure with linear backoff:
  - Added `private retryTimer: ReturnType<typeof setTimeout> | null = null` property.
  - Changed `start()` signature to accept optional `retries = 3` and `delayMs = 500` parameters.
  - Extracted connection logic from `start()` into a new `private _connect(traceId, sessionId, attempt, retries, delayMs)` method.
  - Added `let connected = false` flag inside `_connect()` that gets set to `true` when the server's `"connected"` event is received.
  - Replaced the simple `onerror = () => this.close()` with a retry-aware handler that retries only for pre-connect failures (`!connected && attempt < retries`) using linear backoff (`delayMs * (attempt + 1)`).
  - Updated `close()` to clear any pending `retryTimer` before closing the EventSource.

### Key Considerations
- The implementation follows the phase file exactly, using the "Required Code" block as the canonical reference for the class replacement.
- The `connected` flag is intentionally scoped as a local variable inside `_connect()` rather than a class property. This ensures each connection attempt gets its own flag, which is semantically correct since a retry creates an entirely new EventSource connection.
- The `retryTimer` is a class property (not local) so that both `close()` and `start()` can cancel pending retries from outside the `_connect()` scope.
- The `close()` method clears the retry timer BEFORE closing the EventSource to prevent a race condition where the EventSource is nullified but a pending setTimeout callback still fires.

### Assumptions
- The server sends a `"connected"` event as the first SSE frame, as documented in the phase file (referencing `playground.py:1877-1880`). This is the signal that distinguishes a successful connection from a connection failure.
- The pre-existing `tsc` error (`Cannot find type definition file for 'node'`) is an environment/dependency issue unrelated to these changes. No errors were found specific to `event-stream.ts`.
- Existing call sites in `App.svelte` that call `eventStreamManager.start(traceId, sessionId)` with only two arguments will continue to work because `retries` and `delayMs` have default values.

### Deviations from Plan
None. The implementation matches the Required Code block and all Detailed Steps exactly.

### Potential Risks & Reviewer Attention Points
- The `onerror` handler calls `this.close()` which clears `this.retryTimer`. Then it immediately sets a new `this.retryTimer`. This works correctly because `close()` clears the timer first, and the new timer is set after. However, be aware that `close()` also nullifies `this.eventSource`, so the retry callback in `_connect` creates a fresh EventSource.
- If `close()` is called externally during a pending retry (e.g., user navigates away), the retry timer is properly cleared, preventing orphaned connections. This was verified by inspecting the `close()` logic.
- There are no dedicated tests for the retry behavior in the test suite (no `event-stream.test.ts` file exists). Adding unit tests for the retry logic would improve confidence. This could be addressed in a follow-up phase.

### Files Modified
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/penguiflow/cli/playground_ui/src/lib/services/event-stream.ts` (modified: replaced `EventStreamManager` class with retry-aware version)
- `/Users/martin.alonso/Documents/lg/repos/penguiflow/docs/RFC/ToDo/issue-78/002-trajectory-playground-issue/phases/phase-002.md` (modified: appended implementation notes)
