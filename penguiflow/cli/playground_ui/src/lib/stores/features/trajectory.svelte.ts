import { getContext, setContext } from 'svelte';
import type {
  TimelineStep,
  TrajectoryPayload,
  LLMContext,
  ToolContext,
  ConversationMemory,
  EvalCaseComparisonResponse
} from '$lib/types';

type BackgroundResultsMap = NonNullable<TrajectoryPayload['background_results']>;
type BackgroundTaskResultPayload = BackgroundResultsMap[string];

const TRAJECTORY_STORE_KEY = Symbol('trajectory-store');

export interface EvalCaseSelection {
  exampleId: string;
  datasetPath: string;
  predTraceId: string;
  predSessionId: string;
  score: number;
  threshold: number;
}

export interface TrajectoryStore {
  readonly steps: TimelineStep[];
  readonly artifactStreams: Record<string, unknown[]>;
  readonly isEmpty: boolean;
  readonly hasArtifacts: boolean;
  readonly hasContext: boolean;
  readonly query: string | null;
  readonly llmContext: LLMContext | null;
  readonly toolContext: ToolContext | null;
  readonly backgroundResults: BackgroundResultsMap | null;
  readonly hasBackgroundResults: boolean;
  readonly conversationMemory: ConversationMemory | null;
  readonly hasMemory: boolean;
  readonly externalMemory: unknown | null;
  readonly hasExternalMemory: boolean;
  readonly traceId: string | null;
  readonly sessionId: string | null;
  readonly evalCaseSelection: EvalCaseSelection | null;
  readonly evalComparison: EvalCaseComparisonResponse | null;
  readonly evalComparisonLoading: boolean;
  readonly evalComparisonError: string | null;
  readonly trajectoryViewMode: 'actual' | 'reference' | 'divergence';
  setFromPayload(payload: TrajectoryPayload): void;
  setEvalCaseSelection(selection: EvalCaseSelection | null): void;
  setEvalComparison(payload: EvalCaseComparisonResponse | null): void;
  setEvalComparisonLoading(loading: boolean): void;
  setEvalComparisonError(error: string | null): void;
  setTrajectoryViewMode(mode: 'actual' | 'reference' | 'divergence'): void;
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
  let backgroundResults = $state<BackgroundResultsMap | null>(null);
  let traceId = $state<string | null>(null);
  let sessionId = $state<string | null>(null);
  let evalCaseSelection = $state<EvalCaseSelection | null>(null);
  let evalComparison = $state<EvalCaseComparisonResponse | null>(null);
  let evalComparisonLoading = $state(false);
  let evalComparisonError = $state<string | null>(null);
  let trajectoryViewMode = $state<'actual' | 'reference' | 'divergence'>('actual');

  return {
    get steps() { return steps; },
    get artifactStreams() { return artifactStreams; },
    get query() { return query; },
    get llmContext() { return llmContext; },
    get toolContext() { return toolContext; },
    get backgroundResults() { return backgroundResults; },
    get traceId() { return traceId; },
    get sessionId() { return sessionId; },
    get evalCaseSelection() { return evalCaseSelection; },
    get evalComparison() { return evalComparison; },
    get evalComparisonLoading() { return evalComparisonLoading; },
    get evalComparisonError() { return evalComparisonError; },
    get trajectoryViewMode() { return trajectoryViewMode; },

    get isEmpty() { return steps.length === 0; },
    get hasArtifacts() { return Object.keys(artifactStreams).length > 0; },
    get hasBackgroundResults() { return backgroundResults != null && Object.keys(backgroundResults).length > 0; },
    get hasContext() {
      return traceId != null
        || llmContext != null
        || toolContext != null
        || (backgroundResults != null && Object.keys(backgroundResults).length > 0);
    },
    get conversationMemory() { return llmContext?.conversation_memory ?? null; },
    get hasMemory() { return llmContext?.conversation_memory != null; },
    get externalMemory() { return llmContext?.external_memory ?? null; },
    get hasExternalMemory() { return llmContext?.external_memory != null; },

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
      backgroundResults = payload?.background_results ?? null;
      traceId = payload?.trace_id ?? null;
      sessionId = payload?.session_id ?? null;
    },

    setEvalCaseSelection(selection: EvalCaseSelection | null) {
      evalCaseSelection = selection;
      trajectoryViewMode = 'actual';
      if (selection == null) {
        evalComparison = null;
        evalComparisonLoading = false;
        evalComparisonError = null;
      }
    },

    setEvalComparison(payload: EvalCaseComparisonResponse | null) {
      evalComparison = payload;
    },

    setEvalComparisonLoading(loading: boolean) {
      evalComparisonLoading = loading;
    },

    setEvalComparisonError(error: string | null) {
      evalComparisonError = error;
    },

    setTrajectoryViewMode(mode: 'actual' | 'reference' | 'divergence') {
      trajectoryViewMode = mode;
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
      backgroundResults = null;
      traceId = null;
      sessionId = null;
      evalCaseSelection = null;
      evalComparison = null;
      evalComparisonLoading = false;
      evalComparisonError = null;
      trajectoryViewMode = 'actual';
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
