import { getContext, setContext } from 'svelte';
import type { TimelineStep, TrajectoryPayload, LLMContext, ToolContext, ConversationMemory } from '$lib/types';

const TRAJECTORY_STORE_KEY = Symbol('trajectory-store');

export interface TrajectoryStore {
  readonly steps: TimelineStep[];
  readonly artifactStreams: Record<string, unknown[]>;
  readonly isEmpty: boolean;
  readonly hasArtifacts: boolean;
  readonly hasContext: boolean;
  readonly query: string | null;
  readonly llmContext: LLMContext | null;
  readonly toolContext: ToolContext | null;
  readonly conversationMemory: ConversationMemory | null;
  readonly hasMemory: boolean;
  readonly traceId: string | null;
  readonly sessionId: string | null;
  setFromPayload(payload: TrajectoryPayload): void;
  addArtifactChunk(streamId: string, chunk: unknown): void;
  clearArtifacts(): void;
  clear(): void;
}

export function createTrajectoryStore(): TrajectoryStore {
  let steps = $state<TimelineStep[]>([]);
  let artifactStreams = $state<Record<string, unknown[]>>({});
  let query = $state<string | null>(null);
  let llmContext = $state<LLMContext | null>(null);
  let toolContext = $state<ToolContext | null>(null);
  let traceId = $state<string | null>(null);
  let sessionId = $state<string | null>(null);

  return {
    get steps() { return steps; },
    get artifactStreams() { return artifactStreams; },
    get query() { return query; },
    get llmContext() { return llmContext; },
    get toolContext() { return toolContext; },
    get traceId() { return traceId; },
    get sessionId() { return sessionId; },

    get isEmpty() { return steps.length === 0; },
    get hasArtifacts() { return Object.keys(artifactStreams).length > 0; },
    get hasContext() { return traceId != null || llmContext != null || toolContext != null; },
    get conversationMemory() { return llmContext?.conversation_memory ?? null; },
    get hasMemory() { return llmContext?.conversation_memory != null; },

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
      query = payload?.query ?? null;
      llmContext = payload?.llm_context ?? null;
      toolContext = payload?.tool_context ?? null;
      traceId = payload?.trace_id ?? null;
      sessionId = payload?.session_id ?? null;
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
      query = null;
      llmContext = null;
      toolContext = null;
      traceId = null;
      sessionId = null;
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
