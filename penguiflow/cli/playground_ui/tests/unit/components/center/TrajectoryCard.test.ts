import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/svelte';
import TrajectoryCard from '$lib/components/features/trajectory/TrajectoryCard.svelte';
import * as api from '$lib/services/api';

const sessionStoreMock = {
  activeTraceId: 'trace-1'
};

const trajectoryStoreMock = {
  isEmpty: false,
  steps: [],
  traceId: 'trace-1',
  sessionId: 'session-1'
};

vi.mock('$lib/stores', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/stores')>();
  return {
    ...original,
    getSessionStore: vi.fn(() => sessionStoreMock),
    getTrajectoryStore: vi.fn(() => trajectoryStoreMock)
  };
});

vi.mock('$lib/services/api', () => ({
  listTraces: vi.fn(),
  setTraceTags: vi.fn()
}));

describe('TrajectoryCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows trace-level tagging controls and existing tags for loaded trajectory', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test', 'dataset:policy']
      }
    ]);

    render(TrajectoryCard);

    expect(await screen.findByText('Tags:')).toBeTruthy();
    expect(screen.getByText('split:test')).toBeTruthy();
    expect(screen.getByText('dataset:policy')).toBeTruthy();
    expect(screen.getByLabelText('Edit tags for active trajectory')).toBeTruthy();
  });

  it('adds and removes tags through compact inline editor', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test']
      }
    ]);
    vi.mocked(api.setTraceTags).mockResolvedValue({
      trace_id: 'trace-1',
      session_id: 'session-1',
      tags: ['split:test', 'dataset:gold']
    });

    render(TrajectoryCard);

    await fireEvent.input(await screen.findByLabelText('Edit tags for active trajectory'), {
      target: { value: 'dataset:gold' }
    });
    await fireEvent.keyDown(screen.getByLabelText('Edit tags for active trajectory'), { key: 'Enter' });

    expect(api.setTraceTags).toHaveBeenCalledWith('trace-1', 'session-1', ['dataset:gold'], []);
    expect(await screen.findByText('dataset:gold')).toBeTruthy();

    vi.mocked(api.setTraceTags).mockResolvedValueOnce({
      trace_id: 'trace-1',
      session_id: 'session-1',
      tags: ['dataset:gold']
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Remove active tag split:test' }));
    expect(api.setTraceTags).toHaveBeenCalledWith('trace-1', 'session-1', [], ['split:test']);
  });

  it('renders autocomplete suggestions sourced from known trace tags', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test', 'dataset:policy']
      },
      {
        trace_id: 'trace-2',
        session_id: 'session-2',
        tags: ['dataset:finance']
      }
    ]);

    render(TrajectoryCard);

    await screen.findByLabelText('Edit tags for active trajectory');
    expect(document.querySelector('option[value="dataset:finance"]')).toBeTruthy();
    expect(document.querySelector('option[value="dataset:policy"]')).toBeTruthy();
  });
});
