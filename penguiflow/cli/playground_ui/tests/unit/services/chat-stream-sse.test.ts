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

const flush = () => new Promise(resolve => setTimeout(resolve, 0));

function createStores() {
  return {
    chatStore: createChatStore(),
    artifactsStore: createArtifactsStore(),
    eventsStore: createEventsStore(),
    trajectoryStore: createTrajectoryStore(),
    interactionsStore: createInteractionsStore(),
    tasksStore: createTasksStore(),
    notificationsStore: createNotificationsStore()
  };
}

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

  it('renders a store-backed ui_component from an artifact_stored frame (artifact mode)', async () => {
    const stored = {
      id: 'comp-1',
      component: 'report',
      props: { title: 'Stored' },
      title: 'Stored Report',
      metadata: { namespace: 'penguiflow_ui_component' }
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(stored)
    });

    const stores = createStores();
    const manager = createChatStreamManager(stores);
    stores.chatStore.addUserMessage('Make a report');
    manager.start('Make a report', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'sse');

    const eventSource = (manager as unknown as { eventSource: MockEventSource }).eventSource;
    const agentMsgId = (manager as unknown as { agentMsgId: string }).agentMsgId;

    eventSource.simulateEvent('artifact_stored', {
      event: 'artifact_stored',
      artifact_id: 'artifact-1',
      source: { namespace: 'penguiflow_ui_component' },
      message_id: agentMsgId,
      default_message_id: agentMsgId
    });

    await flush();

    expect(fetch).toHaveBeenCalledWith('/artifacts/artifact-1', {
      headers: { 'X-Session-ID': 'session-1' }
    });
    expect(stores.interactionsStore.artifacts.length).toBe(1);
    expect(stores.interactionsStore.artifacts[0]?.component).toBe('report');
    // Placed against the backend-supplied message id (Phase 007).
    expect(stores.interactionsStore.artifacts[0]?.message_id).toBe(agentMsgId);
    // Not a downloadable artifact.
    expect(stores.artifactsStore.count).toBe(0);
  });

  it('dedupes a both-mode component delivered inline-first then via artifact_stored', async () => {
    const stored = {
      id: 'comp-1',
      component: 'report',
      props: { title: 'Stored' },
      metadata: { namespace: 'penguiflow_ui_component', artifact_id: 'artifact-1' }
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(stored)
    });

    const stores = createStores();
    const manager = createChatStreamManager(stores);
    stores.chatStore.addUserMessage('Make a report');
    manager.start('Make a report', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'sse');

    const eventSource = (manager as unknown as { eventSource: MockEventSource }).eventSource;
    const agentMsgId = (manager as unknown as { agentMsgId: string }).agentMsgId;

    // Inline arrives first, carrying the same opaque store id in meta.artifact_id (both mode).
    eventSource.simulateEvent('artifact_chunk', {
      artifact_type: 'ui_component',
      chunk: { id: 'comp-1', component: 'report', props: { title: 'Inline' } },
      meta: { artifact_id: 'artifact-1' }
    });
    // Then the store-backed frame for the same artifact_id.
    eventSource.simulateEvent('artifact_stored', {
      event: 'artifact_stored',
      artifact_id: 'artifact-1',
      source: { namespace: 'penguiflow_ui_component' },
      message_id: agentMsgId
    });

    await flush();

    // Rendered exactly once, keyed on the opaque store artifact_id.
    expect(stores.interactionsStore.artifacts.length).toBe(1);
  });

  it('dedupes a both-mode component delivered via artifact_stored first then inline', async () => {
    const stored = {
      id: 'comp-1',
      component: 'report',
      props: { title: 'Stored' },
      metadata: { namespace: 'penguiflow_ui_component', artifact_id: 'artifact-1' }
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(stored)
    });

    const stores = createStores();
    const manager = createChatStreamManager(stores);
    stores.chatStore.addUserMessage('Make a report');
    manager.start('Make a report', 'session-1', {}, {}, { onDone: () => {}, onError: () => {} }, 'sse');

    const eventSource = (manager as unknown as { eventSource: MockEventSource }).eventSource;
    const agentMsgId = (manager as unknown as { agentMsgId: string }).agentMsgId;

    // Store-backed frame arrives first.
    eventSource.simulateEvent('artifact_stored', {
      event: 'artifact_stored',
      artifact_id: 'artifact-1',
      source: { namespace: 'penguiflow_ui_component' },
      message_id: agentMsgId
    });
    await flush();
    expect(stores.interactionsStore.artifacts.length).toBe(1);

    // Inline arrives second for the same opaque store id -> skipped.
    eventSource.simulateEvent('artifact_chunk', {
      artifact_type: 'ui_component',
      chunk: { id: 'comp-1', component: 'report', props: { title: 'Inline' } },
      meta: { artifact_id: 'artifact-1' }
    });
    await flush();

    expect(stores.interactionsStore.artifacts.length).toBe(1);
  });
});
