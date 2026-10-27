import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import ChatCardEvalTabHost from './ChatCardEvalTabHost.svelte';
import * as api from '$lib/services/api';
import { resetPersistedEvalState } from '$lib/components/features/eval/EvalTab.svelte';

vi.mock('$lib/services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/services/api')>();
  return {
    ...original,
    listTraces: vi.fn(),
    listEvalDatasets: vi.fn(),
    listEvalMetrics: vi.fn(),
    loadEvalDataset: vi.fn(),
    runEval: vi.fn(),
    fetchTrajectory: vi.fn(),
    fetchEvalCaseComparison: vi.fn()
  };
});

describe('ChatCard Eval tab', () => {
  beforeEach(() => {
    resetPersistedEvalState();
    vi.clearAllMocks();
    vi.mocked(api.listTraces).mockResolvedValue([]);
    vi.mocked(api.listEvalDatasets).mockResolvedValue([
      {
        path: 'examples/evals/policy/dataset.jsonl',
        label: 'policy/dataset.jsonl',
        is_default: true
      }
    ]);
    vi.mocked(api.loadEvalDataset).mockResolvedValue({
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      manifest_path: null,
      counts: { total: 1, by_split: { val: 1 } },
      examples: [{ example_id: 'ex-1', split: 'val', question: 'Q1' }]
    });
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    vi.mocked(api.fetchEvalCaseComparison).mockResolvedValue(null);
  });

  it('renders an Eval tab and shows eval surface when selected', async () => {
    render(ChatCardEvalTabHost);

    const evalTab = screen.getByRole('button', { name: 'Eval' });
    expect(evalTab).toBeTruthy();

    await fireEvent.click(evalTab);
    expect(await screen.findByLabelText('Dataset selection')).toBeTruthy();
  });

  it('renders a Traces tab and shows traces surface when selected', async () => {
    render(ChatCardEvalTabHost);

    const tracesTab = screen.getByRole('button', { name: 'Traces' });
    expect(tracesTab).toBeTruthy();

    await fireEvent.click(tracesTab);
    expect(screen.getByRole('heading', { name: 'Traces' })).toBeTruthy();
  });

  it('keeps focus on Eval when opening a case row and syncs selection with Traces', async () => {
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-7',
      counts: { total: 1, val: 0, test: 1 },
      min_test_score: 0.8,
      passed_threshold: false,
      cases: [
        {
          example_id: 'ex-low',
          split: 'test',
          score: 0.2,
          feedback: 'Needs better evidence.',
          pred_trace_id: 'trace-low',
          pred_session_id: 'session-low',
          question: 'Question text'
        }
      ]
    });
    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-low',
        session_id: 'session-low',
        tags: ['split:test']
      }
    ]);
    vi.mocked(api.fetchTrajectory).mockResolvedValue({
      steps: [],
      trace_id: 'trace-low',
      session_id: 'session-low'
    } as never);

    render(ChatCardEvalTabHost);

    await fireEvent.click(screen.getByRole('button', { name: 'Eval' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));
    const evalRow = await screen.findByRole('row', { name: /ex-low/i });
    await fireEvent.click(evalRow);

    expect(await screen.findByLabelText('Dataset selection')).toBeTruthy();
    expect(api.fetchTrajectory).toHaveBeenCalledWith('trace-low', 'session-low');

    await fireEvent.click(screen.getByRole('button', { name: 'Traces' }));
    const traceRow = await screen.findByRole('row', { name: /trace-low/i });
    expect(traceRow.getAttribute('data-selected')).toBe('true');
  });

  it('keeps last eval run visible after switching tabs', async () => {
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-persist',
      counts: { total: 1, val: 1, test: 0 },
      min_test_score: 0.8,
      passed_threshold: true,
      cases: [
        {
          example_id: 'ex-1',
          split: 'val',
          score: 0.95,
          feedback: null,
          pred_trace_id: 'trace-1',
          pred_session_id: 'session-1',
          question: 'Question text'
        }
      ]
    });

    render(ChatCardEvalTabHost);

    await fireEvent.click(screen.getByRole('button', { name: 'Eval' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));

    expect(await screen.findByText('1 case evaluated.')).toBeTruthy();

    await fireEvent.click(screen.getByRole('button', { name: 'Traces' }));
    expect(await screen.findByRole('heading', { name: 'Traces' })).toBeTruthy();

    await fireEvent.click(screen.getByRole('button', { name: 'Eval' }));
    expect(await screen.findByText('1 case evaluated.')).toBeTruthy();
  });
});
