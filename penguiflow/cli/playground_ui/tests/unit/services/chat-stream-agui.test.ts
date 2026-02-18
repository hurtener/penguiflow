import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { BaseEvent } from '@ag-ui/core';
import {
  createArtifactsStore,
  createChatStore,
  createEventsStore,
  createTrajectoryStore,
  createInteractionsStore,
  createTasksStore,
  createNotificationsStore
} from '$lib/stores';

const runAgentMock = vi.fn();

vi.mock('@ag-ui/client', () => {
  class HttpAgentMock {
    run = runAgentMock;
  }
  return { HttpAgent: HttpAgentMock };
});

import { createChatStreamManager } from '$lib/services/chat-stream';

describe('chatStreamManager (AG-UI)', () => {
  const chatStore = createChatStore();
  const artifactsStore = createArtifactsStore();
  const eventsStore = createEventsStore();
  const trajectoryStore = createTrajectoryStore();
  const interactionsStore = createInteractionsStore();
  const tasksStore = createTasksStore();
  const notificationsStore = createNotificationsStore();
  const chatStreamManager = createChatStreamManager({
    chatStore,
    eventsStore,
    trajectoryStore,
    artifactsStore,
    interactionsStore,
    tasksStore,
    notificationsStore
  });

  beforeEach(() => {
    chatStore.clear();
    artifactsStore.clear();
    eventsStore.clear();
    trajectoryStore.clear();
    interactionsStore.clear();
    tasksStore.clear();
    notificationsStore.clear();
    runAgentMock.mockReset();
  });

  it('streams AG-UI text events into chat store', () => {
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({ type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' } as BaseEvent);
        next({ type: 'TEXT_MESSAGE_START', messageId: 'msg-1', role: 'assistant' } as BaseEvent);
        next({ type: 'TEXT_MESSAGE_CONTENT', messageId: 'msg-1', delta: 'Hello' } as BaseEvent);
        next({ type: 'TEXT_MESSAGE_END', messageId: 'msg-1' } as BaseEvent);
        next({ type: 'RUN_FINISHED', runId: 'run-1' } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    chatStore.addUserMessage('Hi');
    chatStreamManager.start('Hi', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'agui');

    const lastMessage = chatStore.messages.at(-1);
    expect(lastMessage?.role).toBe('agent');
    expect(lastMessage?.text).toBe('Hello');
  });

  it('handles pause custom events', () => {
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({
          type: 'CUSTOM',
          name: 'pause',
          value: {
            reason: 'oauth',
            payload: { provider: 'github', auth_url: 'https://example.com' },
            resume_token: 'resume-123'
          }
        } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    chatStore.addUserMessage('Hi');
    chatStreamManager.start('Hi', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'agui');

    const lastMessage = chatStore.messages.at(-1);
    expect(lastMessage?.text).toContain('Planner paused');
    expect(lastMessage?.pause).toBeTruthy();
  });

  it('stores artifacts from custom events', () => {
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({
          type: 'CUSTOM',
          name: 'artifact_stored',
          value: {
            artifact: {
              id: 'artifact-1',
              mime_type: 'text/plain',
              size_bytes: 4,
              filename: 'note.txt',
              source: { namespace: 'tools' }
            },
            download_url: '/artifacts/artifact-1'
          }
        } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    chatStore.addUserMessage('Hi');
    chatStreamManager.start('Hi', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'agui');

    expect(artifactsStore.count).toBe(1);
    expect(artifactsStore.has('artifact-1')).toBe(true);
  });

  it('stores artifacts from custom events when metadata is partial', () => {
    runAgentMock.mockReturnValueOnce({
      subscribe: ({ next, complete }: { next: (e: BaseEvent) => void; complete: () => void }) => {
        next({
          type: 'CUSTOM',
          name: 'artifact_stored',
          value: {
            artifact: {
              id: 'artifact-partial',
              source: { namespace: 'tools' }
            },
            download_url: '/artifacts/artifact-partial'
          }
        } as BaseEvent);
        complete();
        return { unsubscribe: vi.fn() };
      }
    });

    chatStore.addUserMessage('Hi');
    chatStreamManager.start('Hi', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'agui');

    expect(artifactsStore.count).toBe(1);
    const artifact = artifactsStore.get('artifact-partial');
    expect(artifact?.filename).toBe('artifact-partial');
    expect(artifact?.mime_type).toBe('application/octet-stream');
    expect(artifact?.size_bytes).toBe(0);
  });
});
