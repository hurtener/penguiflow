import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import TracesTab from '$lib/components/features/traces/TracesTab.svelte';
import * as api from '$lib/services/api';

vi.mock('$lib/services/api', () => ({
  listTraces: vi.fn(),
  setTraceTags: vi.fn(),
  fetchTrajectory: vi.fn()
}));

describe('TracesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders compact session headers with turn and query columns', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-a2',
        session_id: 'session-a',
        tags: ['split:test'],
        query_preview: 'alpha second',
        turn_index: 2
      },
      {
        trace_id: 'trace-b1',
        session_id: 'session-b',
        tags: [],
        query_preview: 'beta first',
        turn_index: 1
      },
      {
        trace_id: 'trace-a1',
        session_id: 'session-a',
        tags: ['dataset:policy'],
        query_preview: 'alpha first',
        turn_index: 1
      }
    ]);

    render(TracesTab);

    expect(await screen.findByText('Session session-a')).toBeTruthy();
    expect(screen.getByText('Session session-b')).toBeTruthy();
    expect(screen.getAllByText('Turn 1').length).toBeGreaterThan(0);
    expect(screen.getByText('Turn 2')).toBeTruthy();
    expect(screen.getByText('alpha first')).toBeTruthy();
    expect(screen.getByText('alpha second')).toBeTruthy();
    expect(screen.getByText('dataset:policy')).toBeTruthy();
  });

  it('adds an arbitrary tag to a trace', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:val'],
        query_preview: 'alpha second',
        turn_index: 2
      }
    ]);
    vi.mocked(api.setTraceTags).mockResolvedValue({
      trace_id: 'trace-1',
      session_id: 'session-1',
      tags: ['split:val', 'dataset:gold'],
      query_preview: 'alpha second',
      turn_index: 2
    });

    render(TracesTab);

    const input = await screen.findByLabelText('Add tag for trace-1');
    await fireEvent.input(input, { target: { value: 'dataset:gold' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Add tag trace-1' }));

    expect(api.setTraceTags).toHaveBeenCalledWith('trace-1', 'session-1', ['dataset:gold'], []);
    expect(await screen.findByText('dataset:gold')).toBeTruthy();
  });

  it('loads trajectory when clicking a trace row', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:val'],
        query_preview: 'trace one',
        turn_index: 1
      }
    ]);
    vi.mocked(api.fetchTrajectory).mockResolvedValue({
      steps: [],
      trace_id: 'trace-1',
      session_id: 'session-1'
    } as never);

    render(TracesTab);

    const row = await screen.findByRole('row', { name: /trace-1/i });
    await fireEvent.click(row);

    await waitFor(() => {
      expect(api.fetchTrajectory).toHaveBeenCalledWith('trace-1', 'session-1');
      expect(row.getAttribute('data-selected')).toBe('true');
    });
  });
});
