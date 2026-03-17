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
    trajectoryStoreMock.evalCaseSelection = null;
    trajectoryStoreMock.evalComparison = null;
    trajectoryStoreMock.evalComparisonLoading = false;
    trajectoryStoreMock.evalComparisonError = null;
    trajectoryStoreMock.trajectoryViewMode = 'actual';
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
});
