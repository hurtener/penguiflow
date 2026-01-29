import { describe, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';
import type { BaseEvent } from '@ag-ui/core';

const runAgentMock = vi.fn();

vi.mock('@ag-ui/client', () => {
  class HttpAgentMock {
    runAgent = runAgentMock;
  }
  return { HttpAgent: HttpAgentMock };
});

import { createAGUIStore } from '$lib/agui';

describe('createAGUIStore', () => {
  it('updates state from streamed events', async () => {
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({ type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' } as BaseEvent);
        next({ type: 'TEXT_MESSAGE_START', messageId: 'msg-1', role: 'assistant' } as BaseEvent);
        next({ type: 'TEXT_MESSAGE_CONTENT', messageId: 'msg-1', delta: 'Hello' } as BaseEvent);
        next({ type: 'TEXT_MESSAGE_END', messageId: 'msg-1' } as BaseEvent);
        next({ type: 'RUN_FINISHED' } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    const store = createAGUIStore({ url: 'http://test' });
    await store.sendMessage('Hi');

    const snapshot = get(store.state);
    expect(snapshot.status).toBe('finished');
    expect(snapshot.messages.length).toBe(2);
    expect(snapshot.messages[0]?.role).toBe('user');
    expect(snapshot.messages[1]?.content).toBe('Hello');
  });

  it('handles run errors', async () => {
    const onError = vi.fn();
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({ type: 'RUN_ERROR', message: 'boom', code: 'ERR' } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    const store = createAGUIStore({ url: 'http://test', onError });
    await store.sendMessage('Hi');

    const snapshot = get(store.state);
    expect(snapshot.status).toBe('error');
    expect(snapshot.error?.message).toBe('boom');
    expect(onError).toHaveBeenCalledWith({ message: 'boom', code: 'ERR' });
  });

  it('cancels active runs', async () => {
    const unsubscribe = vi.fn();
    runAgentMock.mockReturnValueOnce({
      subscribe: () => ({ unsubscribe })
    });

    const store = createAGUIStore({ url: 'http://test' });
    const pending = store.sendMessage('Hi');
    store.cancel();
    await pending;

    const snapshot = get(store.state);
    expect(snapshot.status).toBe('idle');
    expect(unsubscribe).toHaveBeenCalled();
  });

  it('forwards custom events', async () => {
    const onCustomEvent = vi.fn();
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({ type: 'CUSTOM', name: 'ping', value: { ok: true } } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    const store = createAGUIStore({ url: 'http://test', onCustomEvent });
    await store.sendMessage('Hi');

    expect(onCustomEvent).toHaveBeenCalledWith('ping', { ok: true });
  });

  it('includes forwarded props in requests', async () => {
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ complete }: { complete: () => void }) => {
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    const store = createAGUIStore({
      url: 'http://test',
      getForwardedProps: () => ({ foo: 'bar' })
    });
    await store.sendMessage('Hi');

    expect(runAgentMock).toHaveBeenCalledWith(
      expect.objectContaining({ forwardedProps: { foo: 'bar' } })
    );
  });
});
