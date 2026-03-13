import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import EvalTab from '$lib/components/features/eval/EvalTab.svelte';
import * as api from '$lib/services/api';

const trajectoryStoreMock = {
  clearArtifacts: vi.fn(),
  setFromPayload: vi.fn()
};

vi.mock('$lib/stores', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/stores')>();
  return {
    ...original,
    getTrajectoryStore: vi.fn(() => trajectoryStoreMock)
  };
});

vi.mock('$lib/services/api', () => ({
  exportEvalDataset: vi.fn(),
  loadEvalDataset: vi.fn(),
  runEval: vi.fn(),
  fetchTrajectory: vi.fn(),
  listEvalDatasets: vi.fn(),
  listEvalMetrics: vi.fn()
}));

describe('EvalTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listEvalDatasets).mockResolvedValue([]);
    trajectoryStoreMock.clearArtifacts.mockReset();
    trajectoryStoreMock.setFromPayload.mockReset();
  });

  it('shows dataset export/browse entry points and semantics guidance', async () => {
    vi.mocked(api.listEvalDatasets).mockResolvedValue([
      { path: 'example_app/evals/policy/dataset.jsonl', label: 'policy/dataset.jsonl', is_default: true }
    ]);
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);

    render(EvalTab);

    expect(await screen.findByText('Export from tagged traces')).toBeTruthy();
    expect(screen.getByText('Browse datasets')).toBeTruthy();
    expect(screen.getByText('policy/dataset.jsonl')).toBeTruthy();
    expect(screen.getByText('Preview reads the dataset from disk and does not import traces into memory.')).toBeTruthy();
  });

  it('focuses on eval workflow and excludes trace management section', async () => {
    vi.mocked(api.listEvalDatasets).mockResolvedValue([]);
    vi.mocked(api.listEvalMetrics).mockResolvedValue([]);

    render(EvalTab);

    expect(screen.queryByText('Trace Selection')).toBeNull();
    expect(await screen.findByText('Dataset')).toBeTruthy();
    expect(screen.getByText('Run')).toBeTruthy();
    expect(screen.getByText('Results')).toBeTruthy();
  });

  it('submits dataset export and shows output paths', async () => {
    vi.mocked(api.exportEvalDataset).mockResolvedValue({
      trace_count: 3,
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      manifest_path: 'examples/evals/policy/manifest.json'
    });

    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Export include tags'), { target: { value: 'split:val, dataset:policy' } });
    await fireEvent.input(screen.getByLabelText('Export exclude tags'), { target: { value: 'bad' } });
    await fireEvent.input(screen.getByLabelText('Export output directory'), { target: { value: 'examples/evals/policy' } });
    await fireEvent.input(screen.getByLabelText('Export limit'), { target: { value: '20' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Export dataset' }));

    expect(api.exportEvalDataset).toHaveBeenCalledWith({
      include_tags: ['split:val', 'dataset:policy'],
      exclude_tags: ['bad'],
      output_dir: 'examples/evals/policy',
      limit: 20
    });
    expect(await screen.findByText('Exported 3 traces.')).toBeTruthy();
    expect(screen.getByText('examples/evals/policy/dataset.jsonl')).toBeTruthy();
  });

  it('prefills dataset path from export and loads dataset summary', async () => {
    vi.mocked(api.exportEvalDataset).mockResolvedValue({
      trace_count: 2,
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      manifest_path: 'examples/evals/policy/manifest.json'
    });
    vi.mocked(api.loadEvalDataset).mockResolvedValue({
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      manifest_path: 'examples/evals/policy/manifest.json',
      counts: {
        total: 12,
        by_split: {
          val: 8,
          test: 4
        }
      },
      examples: [
        {
          example_id: 'case-1',
          split: 'val',
          question: 'What policy applies?'
        }
      ]
    });

    render(EvalTab);

    await fireEvent.click(screen.getByRole('button', { name: 'Export dataset' }));

    const datasetPathInput = screen.getByLabelText('Selected dataset path') as HTMLInputElement;
    expect(datasetPathInput.value).toBe('examples/evals/policy/dataset.jsonl');

    await fireEvent.click(screen.getByRole('button', { name: 'Preview dataset' }));

    expect(api.loadEvalDataset).toHaveBeenCalledWith('examples/evals/policy/dataset.jsonl');
    expect(await screen.findByText('Loaded 12 examples.')).toBeTruthy();
    expect(screen.getByText('val: 8')).toBeTruthy();
    expect(screen.getByText('test: 4')).toBeTruthy();
    expect(screen.getByText('What policy applies?')).toBeTruthy();
  });

  it('auto-previews when selecting a browsed dataset', async () => {
    vi.mocked(api.listEvalDatasets).mockResolvedValue([
      { path: 'example_app/evals/policy/dataset.jsonl', label: 'policy/dataset.jsonl', is_default: true }
    ]);
    vi.mocked(api.listEvalMetrics).mockResolvedValue([]);
    vi.mocked(api.loadEvalDataset).mockResolvedValue({
      dataset_path: 'example_app/evals/policy/dataset.jsonl',
      manifest_path: null,
      counts: { total: 1, by_split: { val: 1 } },
      examples: [{ example_id: 'ex-1', split: 'val', question: 'q1' }]
    });

    render(EvalTab);

    await fireEvent.click(await screen.findByRole('button', { name: 'Select dataset policy/dataset.jsonl' }));

    expect(api.loadEvalDataset).toHaveBeenCalledWith('example_app/evals/policy/dataset.jsonl');
    expect(await screen.findByText('Loaded 1 examples.')).toBeTruthy();
  });

  it('shows inline errors for export and load failures', async () => {
    vi.mocked(api.exportEvalDataset).mockResolvedValue(null);
    vi.mocked(api.loadEvalDataset).mockResolvedValue(null);

    render(EvalTab);

    await fireEvent.click(screen.getByRole('button', { name: 'Export dataset' }));
    expect(await screen.findByText('Dataset export failed.')).toBeTruthy();

    const datasetPathInput = screen.getByLabelText('Selected dataset path');
    await fireEvent.input(datasetPathInput, { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Preview dataset' }));
    expect(await screen.findByText('Dataset load failed.')).toBeTruthy();
    expect((screen.getByLabelText('Selected dataset path') as HTMLInputElement).value).toBe('examples/evals/policy/dataset.jsonl');
  });

  it('shows run guidance and staged dataset path', async () => {
    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    expect(screen.getByText('Run evaluates the currently staged dataset path with your metric spec.')).toBeTruthy();
    expect(screen.getByText('Staged dataset: examples/evals/policy/dataset.jsonl')).toBeTruthy();
  });

  it('submits run config and renders run summary with cases', async () => {
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-1',
      counts: {
        total: 2,
        val: 1,
        test: 1
      },
      min_test_score: 0.8,
      passed_threshold: false,
      cases: [
        {
          example_id: 'ex-1',
          split: 'test',
          score: 0.7,
          feedback: 'Missing a policy citation.',
          pred_trace_id: 'trace-a',
          pred_session_id: 'session-a',
          question: 'What is our refund policy?'
        },
        {
          example_id: 'ex-2',
          split: 'val',
          score: 0.95,
          feedback: null,
          pred_trace_id: 'trace-b',
          pred_session_id: 'session-b',
          question: 'How to submit an expense?'
        }
      ]
    });

    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.change(screen.getByLabelText('Metric selection'), { target: { value: 'example_app.evals.metrics:policy_metric' } });
    await fireEvent.input(screen.getByLabelText('Run min test score'), { target: { value: '0.8' } });
    await fireEvent.input(screen.getByLabelText('Run max cases'), { target: { value: '25' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));

    expect(api.runEval).toHaveBeenCalledWith({
      dataset_path: 'examples/evals/policy/dataset.jsonl',
      metric_spec: 'example_app.evals.metrics:policy_metric',
      min_test_score: 0.8,
      max_cases: 25
    });

    expect(await screen.findByText('Run run-1')).toBeTruthy();
    expect(screen.getByText('Total: 2 | val: 1 | test: 1')).toBeTruthy();
    expect(screen.getByText('Min test score: 0.8')).toBeTruthy();
    expect(screen.getByText('Passed threshold: no')).toBeTruthy();
    expect(screen.getByText('Case ex-1')).toBeTruthy();
    expect(screen.getByText('TEST • 0.7')).toBeTruthy();
    expect(screen.getByText('What is our refund policy?')).toBeTruthy();
    expect(screen.getByText('Missing a policy citation.')).toBeTruthy();
  });

  it('shows running state and completion banner after run finishes', async () => {
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    let resolveRun: (value: unknown) => void;
    const runPromise = new Promise((resolve) => {
      resolveRun = resolve;
    });
    vi.mocked(api.runEval).mockReturnValue(runPromise as Promise<any>);

    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.change(screen.getByLabelText('Metric selection'), { target: { value: 'example_app.evals.metrics:policy_metric' } });

    const runButton = screen.getByRole('button', { name: 'Run evaluation' });
    await fireEvent.click(runButton);

    expect(runButton).toBeDisabled();
    expect(runButton.textContent).toContain('Running evaluation');

    resolveRun({
      run_id: 'run-4',
      counts: { total: 3, val: 2, test: 1 },
      min_test_score: 0.8,
      passed_threshold: true,
      cases: []
    });
    expect(await screen.findByText('Run completed')).toBeTruthy();
    expect(screen.getByText('3 cases evaluated')).toBeTruthy();
  });

  it('blocks run when dataset path is missing', async () => {
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);

    render(EvalTab);

    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));

    expect(api.runEval).not.toHaveBeenCalled();
    expect(await screen.findByText('Dataset path is required before running eval.')).toBeTruthy();
  });

  it('preserves run config values after a failed run', async () => {
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    vi.mocked(api.runEval).mockResolvedValue(null);

    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.change(screen.getByLabelText('Metric selection'), { target: { value: 'example_app.evals.metrics:policy_metric' } });
    await fireEvent.input(screen.getByLabelText('Run min test score'), { target: { value: '0.9' } });
    await fireEvent.input(screen.getByLabelText('Run max cases'), { target: { value: '15' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));

    expect(await screen.findByText('Eval run failed.')).toBeTruthy();
    expect((screen.getByLabelText('Metric selection') as HTMLSelectElement).value).toBe(
      'example_app.evals.metrics:policy_metric'
    );
    expect((screen.getByLabelText('Run min test score') as HTMLInputElement).value).toBe('0.9');
    expect((screen.getByLabelText('Run max cases') as HTMLInputElement).value).toBe('15');
  });

  it('shows worst-case rows first by score and opens trace into trajectory store', async () => {
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-2',
      counts: { total: 2, val: 1, test: 1 },
      min_test_score: 0.8,
      passed_threshold: false,
      cases: [
        {
          example_id: 'ex-high',
          split: 'val',
          score: 0.95,
          feedback: null,
          pred_trace_id: 'trace-high',
          pred_session_id: 'session-high',
          question: 'High score question'
        },
        {
          example_id: 'ex-low',
          split: 'test',
          score: 0.3,
          feedback: 'Needs better evidence.',
          pred_trace_id: 'trace-low',
          pred_session_id: 'session-low',
          question: 'Low score question'
        }
      ]
    });
    vi.mocked(api.fetchTrajectory).mockResolvedValue({
      steps: [],
      trace_id: 'trace-low',
      session_id: 'session-low'
    } as never);

    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));

    const exampleLabels = screen.getAllByTestId('result-example-id').map((node) => node.textContent);
    expect(exampleLabels[0]).toContain('ex-low');
    expect(exampleLabels[1]).toContain('ex-high');

    await fireEvent.click(screen.getByRole('button', { name: 'Review trace ex-low' }));

    expect(api.fetchTrajectory).toHaveBeenCalledWith('trace-low', 'session-low');
    expect(trajectoryStoreMock.clearArtifacts).toHaveBeenCalledTimes(1);
    expect(trajectoryStoreMock.setFromPayload).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Trajectory loaded below/)).toBeTruthy();
  });

  it('shows row-level error when open trace fails', async () => {
    vi.mocked(api.listEvalMetrics).mockResolvedValue([
      {
        metric_spec: 'example_app.evals.metrics:policy_metric',
        label: 'policy_metric',
        source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
      }
    ]);
    vi.mocked(api.runEval).mockResolvedValue({
      run_id: 'run-3',
      counts: { total: 1, val: 0, test: 1 },
      min_test_score: 0.8,
      passed_threshold: false,
      cases: [
        {
          example_id: 'ex-fail',
          split: 'test',
          score: 0.1,
          feedback: 'Bad answer',
          pred_trace_id: 'trace-fail',
          pred_session_id: 'session-fail',
          question: 'Failed question'
        }
      ]
    });
    vi.mocked(api.fetchTrajectory).mockResolvedValue(null);

    render(EvalTab);

    await fireEvent.input(screen.getByLabelText('Selected dataset path'), { target: { value: 'examples/evals/policy/dataset.jsonl' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Review trace ex-fail' }));

    expect(await screen.findByText('Failed to open trace for ex-fail.')).toBeTruthy();
  });

  it('shows clear Results empty-state guidance before any run', async () => {
    render(EvalTab);

    expect(await screen.findByText('Completed eval runs appear here after you execute Run evaluation.')).toBeTruthy();
  });

  it('uses light-theme root class for Eval tab', async () => {
    const { container } = render(EvalTab);

    expect(container.querySelector('.eval-tab')?.classList.contains('eval-light')).toBe(true);
  });

  it('renders a scrollable eval body container', async () => {
    const { container } = render(EvalTab);

    expect(container.querySelector('.eval-body')).toBeTruthy();
    expect(container.querySelector('[data-testid="eval-body"]')).toBeTruthy();
  });
});
