import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/svelte';
import ContextTab from '$lib/components/features/context/ContextTab.svelte';

const trajectoryStoreMock = {
  hasContext: true,
  llmContext: {
    tenant_id: 'tenant-1',
    conversation_memory: { summary: 'memory summary' },
    external_memory: { policy: 'strict' }
  },
  toolContext: { scope: 'payments' },
  backgroundResults: {
    task_1: { task_id: 'task_1', status: 'completed', summary: 'ready' }
  },
  hasMemory: true,
  conversationMemory: { summary: 'memory summary', recent_turns: [] },
  hasExternalMemory: true,
  externalMemory: { policy: 'strict' }
};

const notificationsStoreMock = {
  add: vi.fn(),
  remove: vi.fn(),
  clear: vi.fn(),
  items: []
};

const clipboardWriteTextMock = vi.fn<(text: string) => Promise<void>>();

vi.mock('$lib/stores', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/stores')>();
  return {
    ...original,
    getTrajectoryStore: vi.fn(() => trajectoryStoreMock),
    getNotificationsStore: vi.fn(() => notificationsStoreMock)
  };
});

describe('ContextTab copy action', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(navigator, {
      clipboard: {
        writeText: clipboardWriteTextMock
      }
    });
  });

  it('copies full raw context JSON payload', async () => {
    render(ContextTab);

    expect(screen.getByTestId('context-header')).toBeTruthy();
    expect(screen.getByText('Context')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Copy context JSON' })).toHaveClass('compact-action-btn');

    await fireEvent.click(screen.getByRole('button', { name: 'Copy context JSON' }));

    expect(clipboardWriteTextMock).toHaveBeenCalledTimes(1);
    const firstCall = clipboardWriteTextMock.mock.calls[0];
    expect(firstCall).toBeTruthy();
    const copied = JSON.parse(firstCall![0]) as {
      llm_context: Record<string, unknown> | null;
      tool_context: Record<string, unknown> | null;
      background_results: Record<string, unknown> | null;
    };
    expect(copied.llm_context?.tenant_id).toBe('tenant-1');
    expect(copied.llm_context?.conversation_memory).toEqual({ summary: 'memory summary' });
    expect(copied.tool_context).toEqual({ scope: 'payments' });
    expect(copied.background_results?.task_1).toEqual({ task_id: 'task_1', status: 'completed', summary: 'ready' });
    expect(notificationsStoreMock.add).toHaveBeenCalledWith('Copied context payload.', 'success');
  });

  it('shows warning when clipboard API is unavailable', async () => {
    Object.assign(navigator, { clipboard: undefined });
    render(ContextTab);

    await fireEvent.click(screen.getByRole('button', { name: 'Copy context JSON' }));

    expect(notificationsStoreMock.add).toHaveBeenCalledWith('Clipboard is unavailable in this environment.', 'warning');
  });
});
