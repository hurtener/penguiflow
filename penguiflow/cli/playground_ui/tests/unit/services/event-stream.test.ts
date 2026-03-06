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
  let constructorSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    mockInstances = [];
    constructorSpy = vi.fn();

    // Use a class so it can be called with `new`
    globalThis.EventSource = class FakeEventSource {
      addEventListener: ReturnType<typeof vi.fn>;
      close: ReturnType<typeof vi.fn>;
      onmessage: ((evt: MessageEvent) => void) | null = null;
      onerror: (() => void) | null = null;
      private _listeners: Record<string, ((evt: MessageEvent) => void)[]> = {};

      constructor(url: string) {
        constructorSpy(url);
        this.addEventListener = vi.fn((type: string, fn: (evt: MessageEvent) => void) => {
          (this._listeners[type] ??= []).push(fn);
        });
        this.close = vi.fn();
        mockInstances.push(this as unknown as MockEventSource);
      }

      _emit(type: string, data: Record<string, unknown>) {
        const evt = { type, data: JSON.stringify(data) } as unknown as MessageEvent;
        (this._listeners[type] ?? []).forEach(fn => fn(evt));
        if (this.onmessage) this.onmessage(evt);
      }

      _triggerError() {
        if (this.onerror) this.onerror();
      }
    } as unknown as typeof EventSource;
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
    expect(constructorSpy).toHaveBeenCalledTimes(1);
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
    const secondCallUrl = constructorSpy.mock.calls[1][0] as string;
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
