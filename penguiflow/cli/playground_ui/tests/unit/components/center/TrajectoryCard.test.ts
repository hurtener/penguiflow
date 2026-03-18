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
  sessionId: 'session-1',
  evalCaseSelection: null,
  evalComparison: null,
  evalComparisonLoading: false,
  evalComparisonError: null,
  trajectoryViewMode: 'actual',
  setTrajectoryViewMode: vi.fn()
};

const notificationsStoreMock = {
  items: [],
  add: vi.fn(),
  remove: vi.fn(),
  clear: vi.fn()
};

const clipboardWriteTextMock = vi.fn<(text: string) => Promise<void>>();

vi.mock('$lib/stores', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/stores')>();
  return {
    ...original,
    getSessionStore: vi.fn(() => sessionStoreMock),
    getTrajectoryStore: vi.fn(() => trajectoryStoreMock),
    getNotificationsStore: vi.fn(() => notificationsStoreMock)
  };
});

vi.mock('$lib/services/api', () => ({
  listTraces: vi.fn(),
  setTraceTags: vi.fn()
}));

describe('TrajectoryCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clipboardWriteTextMock.mockReset();
    notificationsStoreMock.add.mockReset();
    Object.assign(navigator, {
      clipboard: {
        writeText: clipboardWriteTextMock
      }
    });
    trajectoryStoreMock.evalCaseSelection = null;
    trajectoryStoreMock.evalComparison = null;
    trajectoryStoreMock.evalComparisonLoading = false;
    trajectoryStoreMock.evalComparisonError = null;
    trajectoryStoreMock.trajectoryViewMode = 'actual';
    trajectoryStoreMock.steps = [];
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

  it('shows divergence in timeline style with section checks and red diffs', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-1',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.2,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.trajectoryViewMode = 'divergence';
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-1',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-1',
      gold_trajectory: {
        steps: [
          {
            action: { next_node: 'classify', args: { policy: 'refund', meta: { tier: 'gold', mode: 'strict' } } },
            observation: { label: 'A' }
          },
          { action: { next_node: 'draft' }, observation: { answer: 'gold' } }
        ]
      },
      pred_trajectory: {
        steps: [
          {
            action: { next_node: 'classify', args: { policy: 'returns', meta: { tier: 'gold', mode: 'flex' } } },
            observation: { label: 'A' }
          },
          { action: { next_node: 'search' }, observation: { answer: 'pred' } }
        ]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test']
      }
    ]);

    render(TrajectoryCard);

    expect(await screen.findByRole('button', { name: 'Actual trajectory' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Reference trajectory' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Trajectory divergence' })).toBeTruthy();
    expect(screen.getAllByText('args').length).toBeGreaterThan(0);
    expect(screen.getByText('meta')).toBeTruthy();
    expect(screen.getByText('✓ tier')).toBeTruthy();
    expect(screen.getByText('policy: refund -> returns')).toBeTruthy();
    expect(screen.getByText('mode: strict -> flex')).toBeTruthy();
    expect(screen.getByText('draft -> search')).toBeTruthy();
    expect(screen.queryByText('Branch diverged. Comparison stops here.')).toBeNull();
    expect(screen.queryByText('Step 1')).toBeNull();
    expect(screen.queryByText('✓ state')).toBeNull();
    expect(screen.queryByText('✓ node')).toBeNull();
    expect(screen.queryByText('✓ value')).toBeNull();
    expect(screen.queryByText('reference')).toBeNull();
    expect(screen.queryByText('actual')).toBeNull();
    expect(document.querySelectorAll('details[open]').length).toBeGreaterThan(0);
    expect(screen.getAllByText('✓ result').length).toBeGreaterThan(0);
    expect(screen.queryByText('First meaningful divergence at step 1')).toBeNull();
    expect(screen.queryByRole('table')).toBeNull();

    await fireEvent.click(screen.getByRole('button', { name: 'Reference trajectory' }));
    expect(trajectoryStoreMock.setTrajectoryViewMode).toHaveBeenCalledWith('reference');
  });

  it('shows full arg key diffs including reason when args are partially different', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-2',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.2,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.trajectoryViewMode = 'divergence';
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-2',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-2',
      gold_trajectory: {
        steps: [
          { action: { next_node: 'decide', args: { route: 'safe', reason: 'policy risk high' } }, observation: { ok: true } },
          { action: { next_node: 'decide', args: { route: 'handoff', reason: 'escalate' } }, observation: { ok: true } }
        ]
      },
      pred_trajectory: {
        steps: [
          { action: { next_node: 'decide', args: { route: 'safe', reason: 'alt wording' } }, observation: { ok: true } },
          { action: { next_node: 'decide', args: { route: 'reply', reason: 'alt wording' } }, observation: { ok: true } }
        ]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test']
      }
    ]);

    render(TrajectoryCard);

    expect(await screen.findByText('route: handoff -> reply')).toBeTruthy();
    expect(screen.getByText('reason: escalate -> alt wording')).toBeTruthy();
    expect(screen.queryByText('output')).toBeNull();
    expect(screen.getAllByText('✓ result').length).toBeGreaterThan(0);
  });

  it('shows full output key diffs including reason when output is partially different', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-3',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.2,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.trajectoryViewMode = 'divergence';
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-3',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-3',
      gold_trajectory: {
        steps: [
          { action: { next_node: 'assess' }, observation: { route: 'safe', reason: 'policy text A' } },
          { action: { next_node: 'assess' }, observation: { route: 'handoff', reason: 'escalate' } }
        ]
      },
      pred_trajectory: {
        steps: [
          { action: { next_node: 'assess' }, observation: { route: 'safe', reason: 'policy text B' } },
          { action: { next_node: 'assess' }, observation: { route: 'reply', reason: 'alt wording' } }
        ]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test']
      }
    ]);

    render(TrajectoryCard);

    expect(await screen.findByText('route: handoff -> reply')).toBeTruthy();
    expect(screen.getByText('reason: escalate -> alt wording')).toBeTruthy();
    expect(screen.queryByText('output')).toBeNull();
    expect(screen.getAllByText('result').length).toBeGreaterThan(0);
  });

  it('treats primitive output changes as meaningful divergence', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-4',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.2,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.trajectoryViewMode = 'divergence';
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-4',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-4',
      gold_trajectory: {
        steps: [{ action: { next_node: 'score' }, observation: 'approved' }]
      },
      pred_trajectory: {
        steps: [{ action: { next_node: 'score' }, observation: 'rejected' }]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test']
      }
    ]);

    render(TrajectoryCard);

    expect(await screen.findByText('result: approved -> rejected')).toBeTruthy();
  });

  it('preserves array granularity in divergence details instead of list summary', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-5',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.1,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.trajectoryViewMode = 'divergence';
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-5',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-5',
      gold_trajectory: {
        steps: [
          {
            action: {
              next_node: 'review',
              args: {
                artifacts: [
                  { id: 'doc-a', score: 0.9 },
                  { id: 'doc-b', score: 0.7 }
                ]
              }
            },
            observation: { verdict: 'safe' }
          }
        ]
      },
      pred_trajectory: {
        steps: [
          {
            action: {
              next_node: 'review',
              args: {
                artifacts: [
                  { id: 'doc-a', score: 0.95 },
                  { id: 'doc-c', score: 0.7 }
                ]
              }
            },
            observation: { verdict: 'safe' }
          }
        ]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: ['split:test']
      }
    ]);

    render(TrajectoryCard);

    expect(await screen.findByText('artifacts')).toBeTruthy();
    expect(screen.queryByText('[0]')).toBeNull();
    expect(screen.queryByText('[1]')).toBeNull();
    expect(screen.getByText('score: 0.9 -> 0.95')).toBeTruthy();
    expect(screen.getByText('id: doc-b -> doc-c')).toBeTruthy();
    expect(screen.queryByText(/list\(/i)).toBeNull();
  });

  it('copies actual trajectory as full JSON payload', async () => {
    trajectoryStoreMock.steps = [
      {
        id: 'step-1',
        name: 'triage_query',
        thought: 'route to bug flow',
        args: { route: 'bug' },
        result: { status: 'ok' },
        latencyMs: 120,
        reflectionScore: undefined,
        status: 'ok'
      }
    ];

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: []
      }
    ]);

    render(TrajectoryCard);
    expect(screen.getByRole('button', { name: 'Copy trajectory text' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Copy actual trajectory text' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Copy reference trajectory text' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Copy divergence text' })).toBeNull();
    await fireEvent.click(screen.getByRole('button', { name: 'Copy trajectory text' }));

    expect(clipboardWriteTextMock).toHaveBeenCalledTimes(1);
    const copied = JSON.parse(clipboardWriteTextMock.mock.calls[0][0]) as {
      mode: string;
      trace_id: string;
      session_id: string;
      trajectory: { steps: Array<{ action: { next_node: string }; observation: { status: string } }> };
    };
    expect(copied.mode).toBe('actual');
    expect(copied.trace_id).toBe('trace-1');
    expect(copied.session_id).toBe('session-1');
    expect(copied.trajectory.steps[0]?.action.next_node).toBe('triage_query');
    expect(copied.trajectory.steps[0]?.observation.status).toBe('ok');
    expect(notificationsStoreMock.add).toHaveBeenCalledWith('Copied actual trajectory.', 'success');
  });

  it('copies reference trajectory as full JSON payload', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-copy-ref',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.2,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-copy-ref',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-copy-ref',
      gold_trajectory: {
        steps: [{ action: { next_node: 'triage_query', thought: 'gold thought', args: { route: 'bug' } }, observation: { ok: true } }]
      },
      pred_trajectory: {
        steps: [{ action: { next_node: 'triage_query', thought: 'pred thought', args: { route: 'bug' } }, observation: { ok: true } }]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: []
      }
    ]);

    render(TrajectoryCard);
    await fireEvent.click(screen.getByRole('button', { name: 'Reference trajectory' }));
    expect(trajectoryStoreMock.setTrajectoryViewMode).toHaveBeenCalledWith('reference');
    trajectoryStoreMock.trajectoryViewMode = 'reference';
    await fireEvent.click(screen.getByRole('button', { name: 'Copy trajectory text' }));

    expect(clipboardWriteTextMock).toHaveBeenCalledTimes(1);
    const copied = JSON.parse(clipboardWriteTextMock.mock.calls[0][0]) as {
      mode: string;
      gold_trace_id: string;
      trajectory: { steps: Array<{ action: { thought: string; next_node: string } }> };
    };
    expect(copied.mode).toBe('reference');
    expect(copied.gold_trace_id).toBe('gold-copy-ref');
    expect(copied.trajectory.steps[0]?.action.next_node).toBe('triage_query');
    expect(copied.trajectory.steps[0]?.action.thought).toBe('gold thought');
    expect(notificationsStoreMock.add).toHaveBeenCalledWith('Copied reference trajectory.', 'success');
  });

  it('copies divergence as structured diff JSON', async () => {
    trajectoryStoreMock.evalCaseSelection = {
      exampleId: 'ex-copy-diff',
      datasetPath: 'examples/evals/policy/dataset.jsonl',
      predTraceId: 'trace-1',
      predSessionId: 'session-1',
      score: 0.2,
      threshold: 0.8
    } as never;
    trajectoryStoreMock.trajectoryViewMode = 'divergence';
    trajectoryStoreMock.evalComparison = {
      example_id: 'ex-copy-diff',
      pred_trace_id: 'trace-1',
      pred_session_id: 'session-1',
      gold_trace_id: 'gold-copy-diff',
      gold_trajectory: {
        steps: [{ action: { next_node: 'classify', args: { route: 'safe' } }, observation: { verdict: 'allow' } }]
      },
      pred_trajectory: {
        steps: [{ action: { next_node: 'classify', args: { route: 'risky' } }, observation: { verdict: 'allow' } }]
      }
    } as never;

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: []
      }
    ]);

    render(TrajectoryCard);
    await fireEvent.click(screen.getByRole('button', { name: 'Copy trajectory text' }));

    expect(clipboardWriteTextMock).toHaveBeenCalledTimes(1);
    const copied = JSON.parse(clipboardWriteTextMock.mock.calls[0][0]) as {
      mode: string;
      summary: { changed_step_count: number };
      steps: Array<{
        index: number;
        status: string;
        changes: Array<{ path: string; reference: unknown; actual: unknown }>;
      }>;
    };
    expect(copied.mode).toBe('divergence');
    expect(copied.summary.changed_step_count).toBe(1);
    expect(copied.steps[0]?.index).toBe(1);
    expect(copied.steps[0]?.status).toBe('changed');
    expect(copied.steps[0]?.changes[0]?.path).toBe('action.args.route');
    expect(copied.steps[0]?.changes[0]?.reference).toBe('safe');
    expect(copied.steps[0]?.changes[0]?.actual).toBe('risky');
    expect(notificationsStoreMock.add).toHaveBeenCalledWith('Copied divergence diff.', 'success');
  });

  it('shows warning when clipboard API is unavailable', async () => {
    trajectoryStoreMock.steps = [
      {
        id: 'step-1',
        name: 'triage_query',
        thought: 'route to bug flow',
        args: { route: 'bug' },
        result: { status: 'ok' },
        latencyMs: 120,
        reflectionScore: undefined,
        status: 'ok'
      }
    ];

    vi.mocked(api.listTraces).mockResolvedValue([
      {
        trace_id: 'trace-1',
        session_id: 'session-1',
        tags: []
      }
    ]);

    Object.assign(navigator, { clipboard: undefined });

    render(TrajectoryCard);
    await fireEvent.click(screen.getByRole('button', { name: 'Copy trajectory text' }));

    expect(notificationsStoreMock.add).toHaveBeenCalledWith('Clipboard is unavailable in this environment.', 'warning');
  });
});
