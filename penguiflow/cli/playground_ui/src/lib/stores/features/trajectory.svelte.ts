import { getContext, setContext } from 'svelte';
import type { TimelineStep, TrajectoryPayload } from '$lib/types';

const TRAJECTORY_STORE_KEY = Symbol('trajectory-store');

export interface TrajectoryStore {
  readonly steps: TimelineStep[];
  readonly artifactStreams: Record<string, unknown[]>;
  readonly isEmpty: boolean;
  readonly hasArtifacts: boolean;
  setFromPayload(payload: TrajectoryPayload): void;
  addArtifactChunk(streamId: string, chunk: unknown): void;
  clearArtifacts(): void;
  clear(): void;
}

export function createTrajectoryStore(): TrajectoryStore {
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
        };
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

export function setTrajectoryStore(store: TrajectoryStore = createTrajectoryStore()): TrajectoryStore {
  setContext(TRAJECTORY_STORE_KEY, store);
  return store;
}

export function getTrajectoryStore(): TrajectoryStore {
  return getContext<TrajectoryStore>(TRAJECTORY_STORE_KEY);
}
