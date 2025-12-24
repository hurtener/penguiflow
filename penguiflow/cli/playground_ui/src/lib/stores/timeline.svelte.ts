import type { TimelineStep, TrajectoryPayload } from '$lib/types';

function createTimelineStore() {
  let steps = $state<TimelineStep[]>([]);
  let artifactStreams = $state<Record<string, unknown[]>>({});

  return {
    get steps() { return steps; },
    get artifactStreams() { return artifactStreams; },

    get isEmpty() { return steps.length === 0; },
    get hasArtifacts() { return Object.keys(artifactStreams).length > 0; },

    setFromPayload(payload: TrajectoryPayload) {
      const rawSteps = payload?.steps ?? [];
      steps = rawSteps.map((step, idx) => {
        const action = step.action ?? {};
        return {
          id: `step-${idx}`,
          name: action.next_node ?? action.plan?.[0]?.node ?? 'step',
          thought: action.thought,
          args: action.args,
          result: step.observation,
          latencyMs: step.latency_ms ?? undefined,
          reflectionScore: step.metadata?.reflection?.score ?? undefined,
          status: step.error ? 'error' : 'ok'
        } as TimelineStep;
      });
    },

    addArtifactChunk(streamId: string, chunk: unknown) {
      const existing = artifactStreams[streamId] ?? [];
      artifactStreams[streamId] = [...existing, chunk];
    },

    clearArtifacts() {
      artifactStreams = {};
    },

    clear() {
      steps = [];
      artifactStreams = {};
    }
  };
}

export const timelineStore = createTimelineStore();
