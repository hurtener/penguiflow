import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  createArtifactsStore,
  createChatStore,
  createInteractionsStore,
  createNotificationsStore,
  createTasksStore
} from '$lib/stores';
import { createSessionStreamManager } from '$lib/services/session-stream';
import { MockEventSource } from '../../setup';

describe('sessionStreamManager', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([])
    });
  });

  it('adds a notification for proactive result updates', () => {
    const tasksStore = createTasksStore();
    const notificationsStore = createNotificationsStore();
    const chatStore = createChatStore();
    const artifactsStore = createArtifactsStore();
    const interactionsStore = createInteractionsStore();
    const manager = createSessionStreamManager({
      tasksStore,
      notificationsStore,
      chatStore,
      artifactsStore,
      interactionsStore
    });

    manager.start('session-1');
    const eventSource = (manager as unknown as { eventSource: MockEventSource }).eventSource;

    eventSource.simulateEvent('state_update', {
      session_id: 'session-1',
      task_id: 'task-1',
      update_id: 'u1',
      update_type: 'RESULT',
      content: {
        proactive: true,
        text: 'Background task finished.',
        background_task_id: 'task-1',
        ui_components: [
          {
            stream_id: 'ui',
            seq: 0,
            done: true,
            artifact_type: 'ui_component',
            chunk: { id: null, component: 'report', props: { title: 'Hello' } }
          }
        ],
        artifacts: [
          {
            id: 'artifact-1',
            mime_type: 'text/plain',
            size_bytes: 4,
            filename: 'note.txt',
            source: { namespace: 'tools' }
          }
        ]
      },
      created_at: new Date().toISOString()
    });

    expect(notificationsStore.items.length).toBe(1);
    expect(notificationsStore.items[0]?.message).toContain('Background task update');
    expect(notificationsStore.items[0]?.message).toContain('Background task finished.');
    expect(chatStore.messages.length).toBe(1);
    expect(chatStore.messages[0]?.text).toBe('Background task finished.');
    expect(chatStore.messages[0]?.artifacts?.length).toBe(1);
    expect(artifactsStore.count).toBe(1);
    expect(artifactsStore.has('artifact-1')).toBe(true);

    // ui_component artifacts are rendered via interactions store.
    expect(interactionsStore.artifacts.length).toBe(1);
    expect(interactionsStore.artifacts[0]?.component).toBe('report');
    expect(interactionsStore.artifacts[0]?.props.title).toBe('Hello');
    expect(interactionsStore.artifacts[0]?.message_id).toBe(chatStore.messages[0]?.id);
  });
});
