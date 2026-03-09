import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  createArtifactsStore,
  createChatStore,
  createEventsStore,
  createInteractionsStore,
  createNotificationsStore,
  createTasksStore,
  createTrajectoryStore
} from '$lib/stores';
import { createChatStreamManager } from '$lib/services/chat-stream';
import { MockEventSource } from '../../setup';

describe('chatStreamManager (SSE)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders MCP app payloads from tool_call_result events', () => {
    const chatStore = createChatStore();
    const artifactsStore = createArtifactsStore();
    const eventsStore = createEventsStore();
    const trajectoryStore = createTrajectoryStore();
    const interactionsStore = createInteractionsStore();
    const tasksStore = createTasksStore();
    const notificationsStore = createNotificationsStore();
    const manager = createChatStreamManager({
      chatStore,
      eventsStore,
      trajectoryStore,
      artifactsStore,
      interactionsStore,
      tasksStore,
      notificationsStore
    });

    chatStore.addUserMessage('Open editor');
    manager.start('Open editor', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'sse');

    const eventSource = (manager as unknown as { eventSource: MockEventSource }).eventSource;
    eventSource.simulateEvent('event', {
      event: 'tool_call_result',
      tool_call_id: 'call-1',
      tool_name: 'pengui_slides.open_deck_editor',
      result_json: JSON.stringify({
        result: {
          value: 'Opening editor',
          __mcp_app__: {
            artifact_id: 'pengui_slides_app_123',
            csp: {},
            permissions: {},
            tool_data: 'Opening editor',
            tool_input: { deck_id: 'deck-1' },
            namespace: 'pengui_slides',
            session_id: 'session-1',
            sandbox: 'allow-scripts allow-forms',
            prefers_border: false
          }
        }
      })
    });

    expect(interactionsStore.artifacts.length).toBe(1);
    expect(interactionsStore.artifacts[0]?.component).toBe('mcp_app');
    expect(interactionsStore.artifacts[0]?.id).toBe('pengui_slides_app_123:call-1');
    expect(interactionsStore.artifacts[0]?.props.namespace).toBe('pengui_slides');
    expect(interactionsStore.artifacts[0]?.props.artifact_url).toBe('/artifacts/pengui_slides_app_123');
  });
});
