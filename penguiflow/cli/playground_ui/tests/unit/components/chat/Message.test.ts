import { render, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it } from 'vitest';
import MessageHost from './MessageHost.svelte';
import {
  createComponentRegistryStore,
  createInteractionsStore,
  createLayoutStore,
  createSessionStore,
} from '$lib/stores';
import type { ChatMessage } from '$lib/types';

describe('Message component MCP app routing', () => {
  const componentRegistryStore = createComponentRegistryStore();
  const interactionsStore = createInteractionsStore();
  const layoutStore = createLayoutStore();
  const sessionStore = createSessionStore();

  const message: ChatMessage = {
    id: 'msg-1',
    role: 'agent',
    text: 'App opened.',
    ts: Date.now(),
  };

  beforeEach(() => {
    interactionsStore.clear();
    layoutStore.reset();
    componentRegistryStore.reset();
    componentRegistryStore.setFromPayload({
      version: '1.0.0',
      enabled: true,
      allowlist: [],
      components: {
        markdown: { name: 'markdown', interactive: false, description: '', propsSchema: {}, category: 'display' },
        mcp_app: { name: 'mcp_app', interactive: false, description: '', propsSchema: {}, category: 'display' },
      }
    });
  });

  it('keeps non-MCP component artifacts inline on desktop', async () => {
    interactionsStore.addArtifactChunk({
      artifact_type: 'ui_component',
      chunk: {
        id: 'artifact-1',
        component: 'markdown',
        props: { content: 'Inline artifact body' },
      },
      meta: { message_id: message.id },
      seq: 1,
      ts: Date.now(),
    });

    const { findByText } = render(MessageHost, {
      props: { message, componentRegistryStore, interactionsStore, layoutStore, sessionStore }
    });

    expect(await findByText('Inline artifact body')).toBeInTheDocument();
  });

  it('hides MCP app artifacts inline on desktop', () => {
    interactionsStore.addArtifactChunk({
      artifact_type: 'ui_component',
      chunk: {
        id: 'artifact-2',
        component: 'mcp_app',
        props: {
          html: '<!doctype html><html><body><main>Editor</main></body></html>',
          namespace: 'pengui_slides',
        },
      },
      meta: { message_id: message.id },
      seq: 1,
      ts: Date.now(),
    });

    render(MessageHost, {
      props: { message, componentRegistryStore, interactionsStore, layoutStore, sessionStore }
    });

    expect(document.querySelector('.mcp-app-frame')).not.toBeInTheDocument();
  });

  it('keeps MCP app artifacts inline on mobile', async () => {
    layoutStore.setMobile(true);
    interactionsStore.addArtifactChunk({
      artifact_type: 'ui_component',
      chunk: {
        id: 'artifact-3',
        component: 'mcp_app',
        props: {
          html: '<!doctype html><html><body><main>Editor</main></body></html>',
          namespace: 'pengui_slides',
        },
      },
      meta: { message_id: message.id },
      seq: 1,
      ts: Date.now(),
    });

    render(MessageHost, {
      props: { message, componentRegistryStore, interactionsStore, layoutStore, sessionStore }
    });

    await waitFor(() => {
      expect(document.querySelector('.mcp-app-frame')).toBeInTheDocument();
    });
  });
});
