import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock crypto.randomUUID
vi.stubGlobal('crypto', {
  randomUUID: () => 'test-uuid-' + Math.random().toString(36).substr(2, 9)
});

// Mock EventSource for SSE testing
class MockEventSource {
  url: string;
  onmessage: ((evt: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Map<string, ((evt: MessageEvent) => void)[]> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (evt: MessageEvent) => void) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, []);
    }
    this.listeners.get(type)!.push(listener);
  }

  removeEventListener(type: string, listener: (evt: MessageEvent) => void) {
    const typeListeners = this.listeners.get(type);
    if (typeListeners) {
      const index = typeListeners.indexOf(listener);
      if (index !== -1) {
        typeListeners.splice(index, 1);
      }
    }
  }

  close() {}

  // Test helper to simulate events
  simulateEvent(type: string, data: unknown) {
    const evt = new MessageEvent(type, { data: JSON.stringify(data) });
    this.listeners.get(type)?.forEach(l => l(evt));
  }

  // Test helper to simulate error
  simulateError() {
    if (this.onerror) {
      this.onerror();
    }
  }
}

vi.stubGlobal('EventSource', MockEventSource);

// Mock fetch for API testing
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

export { mockFetch, MockEventSource };
