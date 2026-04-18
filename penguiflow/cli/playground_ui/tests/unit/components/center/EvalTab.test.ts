import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import EvalTab, { resetPersistedEvalState } from '$lib/components/features/eval/EvalTab.svelte';
import * as api from '$lib/services/api';

const trajectoryStoreMock = {
  clearArtifacts: vi.fn(),
  setFromPayload: vi.fn(),
  setEvalCaseSelection: vi.fn(),
  setEvalComparison: vi.fn(),
  setEvalComparisonLoading: vi.fn(),
  setEvalComparisonError: vi.fn(),
  setTrajectoryViewMode: vi.fn()
};

vi.mock('$lib/stores', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/stores')>();
  return {
    ...original,
    getTrajectoryStore: vi.fn(() => trajectoryStoreMock)
  };
});

vi.mock('$lib/services/api', () => ({
  loadEvalDataset: vi.fn(),
  runEval: vi.fn(),
  fetchTrajectory: vi.fn(),
  fetchEvalCaseComparison: vi.fn(),
  listEvalDatasets: vi.fn(),
  listEvalMetrics: vi.fn()
}));

describe('EvalTab minimalist flow', () => {
  beforeEach(() => {
    resetPersistedEvalState();
    vi.clearAllMocks();
    trajectoryStoreMock.clearArtifacts.mockReset();
    trajectoryStoreMock.setFromPayload.mockReset();
    vi.mocked(api.listEvalDatasets).mockResolvedValue([
      { path: 'examples/evals/policy/dataset.jsonl', label: 'policy/dataset.jsonl', is_default: true },
      { path: 'examples/evals/finance/dataset.jsonl', label: 'finance/dataset.jsonl', is_default: false }
    ]);
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    vi.mocked(api.loadEvalDataset).mockResolvedValue({
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      manifest_path: 'examples/evals/policy/manifest.json',
      counts: { total: 12, by_split: { val: 8, test: 4 } },
      examples: [{ example_id: 'ex-1', split: 'val', question: 'Q1' }]
    });
    vi.mocked(api.fetchEvalCaseComparison).mockResolvedValue(null);
  });

  it('renders compact toolbar and removes legacy dataset path input', async () => {
    render(EvalTab);

    expect(await screen.findByTestId('eval-toolbar')).toBeTruthy();
    expect(screen.getByLabelText('Dataset selection')).toBeTruthy();
    expect(screen.getByLabelText('Metric selection')).toBeTruthy();
    expect(screen.queryByLabelText('Selected dataset path')).toBeNull();
    expect(screen.queryByText('Browse datasets')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Preview dataset' })).toBeNull();
  });

  it('auto-previews selected dataset from dropdown', async () => {
    render(EvalTab);

    await screen.findByLabelText('Dataset selection');
    expect(api.loadEvalDataset).toHaveBeenCalledWith('examples/evals/policy/dataset.jsonl');

    await fireEvent.change(screen.getByLabelText('Dataset selection'), {
      target: { value: 'examples/evals/finance/dataset.jsonl' }
    });
    expect(api.loadEvalDataset).toHaveBeenCalledWith('examples/evals/finance/dataset.jsonl');
  });

  it('shows compact status line and empty results state', async () => {
    render(EvalTab);

    expect(await screen.findByText('12 examples loaded.')).toBeTruthy();
    expect(screen.getByText('Run evaluation to see results.')).toBeTruthy();
  });

  it('disables run until dataset and metric are available', async () => {
    vi.mocked(api.listEvalDatasets).mockResolvedValue([]);
    vi.mocked(api.listEvalMetrics).mockResolvedValue([]);

    render(EvalTab);

    const runButton = await screen.findByRole('button', { name: 'Run evaluation' });
    expect(runButton).toBeDisabled();
  });

  it('submits run and renders minimalist summary chips and sorted cases', async () => {
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-1',
      counts: { total: 2, val: 1, test: 1 },
      min_test_score: 0.8,
      passed_threshold: false,
      metric: {
        name: 'Policy Compliance',
        summary: 'Checks routing and tool discipline.',
        criteria: [
          {
            id: 'starts_with_triage',
            label: 'Starts with triage',
            description: null
          }
        ]
      },
      cases: [
        {
          example_id: 'ex-high',
          split: 'val',
          score: 0.95,
          feedback: "route=general; tools=['triage_query','answer_general']",
          checks: { starts_with_triage: true },
          pred_trace_id: 'trace-high',
          pred_session_id: 'session-high',
          question: 'High score question'
        },
        {
          example_id: 'ex-low',
          split: 'test',
          score: 0.6666666666666666,
          feedback: 'Needs evidence',
          checks: { starts_with_triage: false },
          pred_trace_id: 'trace-low',
          pred_session_id: 'session-low',
          question: 'Low score question'
        }
      ]
    });

    render(EvalTab);

    await fireEvent.input(await screen.findByLabelText('Min score'), { target: { value: '0.8' } });
    await fireEvent.input(screen.getByLabelText('Max cases'), { target: { value: '25' } });
    const runButton = screen.getByRole('button', { name: 'Run evaluation' });
    await waitFor(() => expect(runButton).not.toBeDisabled());
    await fireEvent.click(runButton);

    expect(api.runEval).toHaveBeenCalledWith({
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      metric_spec: 'example_app.evals.metrics:policy_metric',
      min_test_score: 0.8,
      max_cases: 25
    });
    expect(screen.getByText('2 cases evaluated.')).toBeTruthy();
    expect(screen.getByText('Policy Compliance')).toBeTruthy();
    expect(screen.getByText(/Checks routing and tool discipline\./)).toBeTruthy();
    expect(screen.queryByText(/run-1/i)).toBeNull();
    expect(await screen.findByTestId('eval-summary-line')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Failed 1' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Passed 1' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'All 2' })).toBeTruthy();
    const headers = screen.getAllByRole('columnheader').map((node) => node.textContent?.trim());
    expect(headers[headers.length - 2]).toBe('Score');
    expect(headers[headers.length - 1]).toBe('Feedback');

    const ordered = screen.getAllByTestId('result-example-id').map((node) => node.textContent);
    expect(ordered[0]).toContain('ex-low');
    expect(ordered[1]).toContain('ex-high');

    const lowRow = screen.getByRole('row', { name: /ex-low/i });
    const highRow = screen.getByRole('row', { name: /ex-high/i });
    expect(lowRow.getAttribute('data-severity')).toBe('warning');
    expect(highRow.getAttribute('data-severity')).toBe('ok');
    expect(screen.getByLabelText('Failed case ex-low')).toBeTruthy();
    expect(screen.getByLabelText('Passed case ex-high')).toBeTruthy();
    expect(screen.getByText('✓ All pass')).toBeTruthy();
    expect(screen.queryByText(/route=general/)).toBeNull();
    expect(screen.getByText('Failed: Starts with triage')).toBeTruthy();
    expect(screen.getByText('0.67')).toBeTruthy();
    expect(screen.queryByText('0.6666666666666666')).toBeNull();
  });

  it('filters result table with All/Failed/Passed chips only', async () => {
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-filter',
      counts: { total: 3, val: 1, test: 2 },
      min_test_score: 0.8,
      passed_threshold: false,
      cases: [
        {
          example_id: 'ex-fail',
          split: 'test',
          score: 0.2,
          feedback: null,
          pred_trace_id: 'trace-fail',
          pred_session_id: 'session-1',
          question: 'Failing question'
        },
        {
          example_id: 'ex-test-pass',
          split: 'test',
          score: 0.9,
          feedback: null,
          pred_trace_id: 'trace-test-pass',
          pred_session_id: 'session-1',
          question: 'Passing test question'
        },
        {
          example_id: 'ex-val-pass',
          split: 'val',
          score: 0.95,
          feedback: null,
          pred_trace_id: 'trace-val-pass',
          pred_session_id: 'session-2',
          question: 'Passing val question'
        }
      ]
    });
    vi.mocked(api.fetchTrajectory).mockResolvedValue({
      steps: [],
      trace_id: 'trace-fail',
      session_id: 'session-1'
    } as never);

    render(EvalTab);
    await fireEvent.click(await screen.findByRole('button', { name: 'Run evaluation' }));

    await fireEvent.click(screen.getByRole('button', { name: 'Failed 1' }));
    expect(screen.getByRole('button', { name: 'Failed 1' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'All 3' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByText('ex-fail')).toBeTruthy();
    expect(screen.queryByText('ex-test-pass')).toBeNull();

    await fireEvent.click(screen.getByRole('button', { name: 'Passed 2' }));
    expect(screen.queryByText('ex-fail')).toBeNull();
    expect(screen.getByText('ex-test-pass')).toBeTruthy();
    expect(screen.getByText('ex-val-pass')).toBeTruthy();

    await fireEvent.click(screen.getByRole('button', { name: 'All 3' }));
    expect(screen.getByText('ex-fail')).toBeTruthy();
    expect(screen.getByText('ex-test-pass')).toBeTruthy();
    expect(screen.getByText('ex-val-pass')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Reviewed/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Val\b/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Test\b/i })).toBeNull();
  });

  it('keeps last successful run when next run fails', async () => {
    vi.mocked(api.runEval)
      .mockResolvedValueOnce({
        run_id: 'run-ok',
        counts: { total: 1, val: 1, test: 0 },
        min_test_score: 0.8,
        passed_threshold: true,
        cases: []
      })
      .mockResolvedValueOnce(null);

    render(EvalTab);

    const runButton = await screen.findByRole('button', { name: 'Run evaluation' });
    await waitFor(() => expect(runButton).not.toBeDisabled());
    await fireEvent.click(runButton);
    expect(await screen.findByText('1 case evaluated.')).toBeTruthy();

    await fireEvent.click(runButton);
    expect(await screen.findByText('Eval run failed.')).toBeTruthy();
    expect(screen.getByTestId('eval-summary-line')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'All 0' })).toBeTruthy();
  });

  it('opens trace into trajectory store when clicking a case row', async () => {
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-2',
      counts: { total: 1, val: 0, test: 1 },
      min_test_score: 0.8,
      passed_threshold: false,
      cases: [
        {
          example_id: 'ex-1',
          split: 'test',
          score: 0.2,
          feedback: null,
          pred_trace_id: 'trace-1',
          pred_session_id: 'session-1',
          question: 'Question text'
        }
      ]
    });
    vi.mocked(api.fetchTrajectory).mockResolvedValue({
      steps: [],
      trace_id: 'trace-1',
      session_id: 'session-1'
    } as never);
    vi.mocked(api.fetchEvalCaseComparison).mockResolvedValue({
      example_id: 'ex-1',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-trace-1',
      gold_trajectory: { steps: [{ action: { next_node: 'gold_node' } }] },
      pred_trajectory: { steps: [{ action: { next_node: 'pred_node' } }] }
    } as never);

    render(EvalTab);

    const runButton = await screen.findByRole('button', { name: 'Run evaluation' });
    await waitFor(() => expect(runButton).not.toBeDisabled());
    await fireEvent.click(runButton);
    const rowCell = await screen.findByText('ex-1');
    const row = rowCell.closest('tr');
    expect(row).toBeTruthy();
    await fireEvent.click(row as HTMLTableRowElement);

    expect(api.fetchTrajectory).toHaveBeenCalledWith('trace-1', 'session-1');
    expect(api.fetchEvalCaseComparison).toHaveBeenCalledWith({
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      example_id: 'ex-1',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1'
    });
    expect(trajectoryStoreMock.clearArtifacts).toHaveBeenCalledTimes(1);
    expect(trajectoryStoreMock.setFromPayload).toHaveBeenCalledTimes(1);
    expect(trajectoryStoreMock.setEvalComparisonLoading).toHaveBeenCalledWith(true);
    expect(trajectoryStoreMock.setEvalComparison).toHaveBeenCalled();
    expect(trajectoryStoreMock.setEvalCaseSelection).toHaveBeenCalled();
    expect(trajectoryStoreMock.setTrajectoryViewMode).toHaveBeenCalledWith('divergence');
    expect(screen.queryByRole('button', { name: 'Review trace ex-1' })).toBeNull();
  });

  it('switches run button label while evaluation is in-flight', async () => {
    let resolveRun!: (value: Awaited<ReturnType<typeof api.runEval>>) => void;
    const runPromise = new Promise<Awaited<ReturnType<typeof api.runEval>>>((resolve) => {
      resolveRun = resolve;
    });
    vi.mocked(api.runEval).mockReturnValue(runPromise);

    render(EvalTab);

    const runButton = await screen.findByRole('button', { name: 'Run evaluation' });
    await waitFor(() => expect(runButton).not.toBeDisabled());
    await fireEvent.click(runButton);
    expect(screen.getByRole('button', { name: 'Run evaluation' }).textContent).toContain('Running...');

    resolveRun({
      run_id: 'run-3',
      counts: { total: 0, val: 0, test: 0 },
      min_test_score: 0.8,
      passed_threshold: true,
      cases: []
    });
    expect(await screen.findByText('0 cases evaluated.')).toBeTruthy();
  });

  it('keeps eval state persisted across remounts', async () => {
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-persist',
      counts: { total: 1, val: 1, test: 0 },
      min_test_score: 0.8,
      passed_threshold: true,
      cases: []
    });

    const first = render(EvalTab);
    const runButton = await screen.findByRole('button', { name: 'Run evaluation' });
    await waitFor(() => expect(runButton).not.toBeDisabled());
    await fireEvent.click(runButton);
    expect(await screen.findByText('1 case evaluated.')).toBeTruthy();
    first.unmount();

    render(EvalTab);
    expect(await screen.findByText('1 case evaluated.')).toBeTruthy();
  });
});
