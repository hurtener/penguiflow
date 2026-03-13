import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import ChatCardEvalTabHost from './ChatCardEvalTabHost.svelte';
import * as api from '$lib/services/api';

vi.mock('$lib/services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/services/api')>();
  return {
    ...original,
    listTraces: vi.fn(),
    listEvalDatasets: vi.fn(),
    listEvalMetrics: vi.fn(),
    runEval: vi.fn(),
    fetchTrajectory: vi.fn()
  };
});

describe('ChatCard Eval tab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listTraces).mockResolvedValue([]);
    vi.mocked(api.listEvalDatasets).mockResolvedValue([]);
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
  });

  it('renders an Eval tab and shows eval surface when selected', async () => {
    render(ChatCardEvalTabHost);

    const evalTab = screen.getByRole('button', { name: 'Eval' });
    expect(evalTab).toBeTruthy();

    await fireEvent.click(evalTab);
    expect(screen.getByText('Dataset')).toBeTruthy();
  });

  it('renders a Traces tab and shows traces surface when selected', async () => {
    render(ChatCardEvalTabHost);

    const tracesTab = screen.getByRole('button', { name: 'Traces' });
    expect(tracesTab).toBeTruthy();

    await fireEvent.click(tracesTab);
    expect(screen.getByRole('heading', { name: 'Traces' })).toBeTruthy();
  });

  it('routes Eval review-trace action into Traces tab and opens the selected trace', async () => {
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
    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.change(screen.getByLabelText('Metric selection'), { target: { value: 'example_app.evals.metrics:policy_metric' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));
    await fireEvent.click(await screen.findByRole('button', { name: 'Review trace ex-low' }));

    expect(await screen.findByRole('heading', { name: 'Traces' })).toBeTruthy();
    expect(api.fetchTrajectory).toHaveBeenCalledWith('trace-low', 'session-low');
  });
});
