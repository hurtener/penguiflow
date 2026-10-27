import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import TracesTab from '$lib/components/features/traces/TracesTab.svelte';
import * as api from '$lib/services/api';

vi.mock('$lib/services/api', () => ({
  listTraces: vi.fn(),
  fetchTrajectory: vi.fn(),
  exportEvalDataset: vi.fn()
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

    expect(await screen.findByText('Trace history')).toBeTruthy();
    expect(screen.getByText(/\+tag include -tag ignore/)).toBeTruthy();
    expect(await screen.findByText('session-a')).toBeTruthy();
    expect(screen.getByText('session-b')).toBeTruthy();
    expect(screen.getAllByText('Turn 1').length).toBeGreaterThan(0);
    expect(screen.getByText('Turn 2')).toBeTruthy();
    expect(screen.getByText('alpha first')).toBeTruthy();
    expect(screen.getByText('alpha second')).toBeTruthy();
    expect(screen.getByText('dataset:policy')).toBeTruthy();
  });

  it('exports a dataset from +tag/-tag query', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:val'],
        query_preview: 'alpha second',
        turn_index: 2
      }
    ]);
    vi.mocked(api.exportEvalDataset).mockResolvedValue({
      trace_count: 2,
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      manifest_path: 'examples/evals/policy/manifest.json'
    });

    render(TracesTab);

    await fireEvent.input(await screen.findByLabelText('Export tags query'), {
      target: { value: '+split:test +dataset:policy -skip:bad,tag:implicit' }
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Export dataset traces tab' }));

    expect(api.exportEvalDataset).toHaveBeenCalledWith({
      include_tags: ['split:test', 'dataset:policy', 'tag:implicit'],
      exclude_tags: ['skip:bad'],
      limit: 0
    });
    expect(await screen.findByText('Exported 2 traces.')).toBeTruthy();
    expect(screen.getByText('examples/evals/policy/dataset.jsonl')).toBeTruthy();
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

    expect(screen.queryByRole('button', { name: 'Add tag trace-1' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark val trace-1' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Mark test trace-1' })).toBeNull();
  });

  it('auto-opens and scrolls to requested trace row from handoff request', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:val'],
        query_preview: 'trace one',
        turn_index: 1
      },
      {
        trace_id: 'trace-2',
        session_id: 'session-1',
        tags: ['split:test'],
        query_preview: 'trace two',
        turn_index: 2
      }
    ]);
    vi.mocked(api.fetchTrajectory).mockResolvedValue({
      steps: [],
      trace_id: 'trace-2',
      session_id: 'session-1'
    } as never);

    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      writable: true,
      value: scrollIntoView
    });

    render(TracesTab, {
      openRequest: {
        traceId: 'trace-2',
        sessionId: 'session-1',
        requestId: 42
      }
    });

    const selectedRow = await screen.findByRole('row', { name: /trace-2/i });
    await waitFor(() => {
      expect(api.fetchTrajectory).toHaveBeenCalledWith('trace-2', 'session-1');
      expect(selectedRow.getAttribute('data-selected')).toBe('true');
      expect(scrollIntoView).toHaveBeenCalled();
    });
  });

  it('filters trace groups by search across session/query/tags/trace id', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-policy-1',
        session_id: 'session-policy',
        tags: ['dataset:policy', 'split:val'],
        query_preview: 'policy coverage question',
        turn_index: 1
      },
      {
        trace_id: 'trace-math-1',
        session_id: 'session-math',
        tags: ['dataset:math', 'split:test'],
        query_preview: 'simple arithmetic prompt',
        turn_index: 1
      }
    ]);

    render(TracesTab);

    const searchInput = await screen.findByLabelText('Filter traces');
    await fireEvent.input(searchInput, { target: { value: 'policy' } });

    expect(await screen.findByText('session-policy')).toBeTruthy();
    expect(screen.queryByText('session-math')).toBeNull();
    expect(screen.getByText('trace-policy-1')).toBeTruthy();
    expect(screen.queryByText('trace-math-1')).toBeNull();
  });

  it('provides export tag autocomplete suggestions from known trace tags', async () => {
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:val', 'dataset:policy'],
        query_preview: 'one',
        turn_index: 1
      },
      {
        trace_id: 'trace-2',
        session_id: 'session-2',
        tags: ['dataset:finance'],
        query_preview: 'two',
        turn_index: 1
      }
    ]);

    render(TracesTab);

    await screen.findByLabelText('Export tags query');
    expect(document.querySelector('option[value="+dataset:finance"]')).toBeTruthy();
    expect(document.querySelector('option[value="-dataset:policy"]')).toBeTruthy();
  });
});
