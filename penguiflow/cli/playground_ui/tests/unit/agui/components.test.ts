import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import { readable } from 'svelte/store';
import type { AGUIStore, StreamingMessage, StreamingToolCall } from '$lib/agui';

import AguiComponentHost from './AguiComponentHost.svelte';

describe('AGUI components', () => {
  it('renders message list, debugger, message, and tool call', () => {
    const store: AGUIStore = {
      messages: readable([]),
      state: readable({
        status: 'idle',
        threadId: 'thread-1',
        runId: 'run-1',
        messages: [],
        agentState: {},
        activeSteps: [],
        error: null
      }),
      status: readable('idle'),
      agentState: readable({ ready: true }),
      activeSteps: readable([]),
      isRunning: readable(false),
      error: readable(null),
      sendMessage: async () => {},
      cancel: () => {},
      reset: () => {}
    };

    const message: StreamingMessage = {
      id: 'msg-2',
      role: 'assistant',
      content: 'Rendered content',
      isStreaming: false,
      toolCalls: []
    };

    const toolCall: StreamingToolCall = {
      id: 'call-1',
      name: 'search',
      arguments: '{"query":"test"}',
      isStreaming: false,
      result: 'ok'
    };

    const { getByText } = render(AguiComponentHost, { store, message, toolCall });

    expect(getByText('No messages yet')).toBeInTheDocument();
    expect(getByText('AG-UI Debug')).toBeInTheDocument();
    expect(getByText('Rendered content')).toBeInTheDocument();
    expect(getByText('search')).toBeInTheDocument();
  });
});
