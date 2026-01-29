export type TimelineStep = {
  id: string;
  name: string;
  thought?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  latencyMs?: number;
  reflectionScore?: number;
  status?: 'ok' | 'error';
  isParallel?: boolean;
};

export interface TrajectoryStep {
  action?: {
    next_node?: string;
    plan?: { node: string }[];
    thought?: string;
    args?: Record<string, unknown>;
  };
  observation?: Record<string, unknown>;
  latency_ms?: number;
  metadata?: {
    reflection?: { score?: number };
  };
  error?: boolean;
}

/**
 * A single conversation turn stored in short-term memory.
 */
export interface ConversationTurn {
  user: string;
  assistant: string;
  trajectory_digest?: {
    tools_invoked?: string[];
    observations_summary?: string;
    reasoning_summary?: string;
    artifacts_refs?: string[];
  };
}

/**
 * Conversation memory injected into llm_context by ShortTermMemory.
 */
export interface ConversationMemory {
  recent_turns?: ConversationTurn[];
  summary?: string;
  pending_turns?: ConversationTurn[];
}

/**
 * Background task result stored on trajectories.
 */
export interface BackgroundTaskResultPayload {
  task_id: string;
  group_id?: string | null;
  status?: string;
  summary?: string | null;
  payload?: unknown;
  facts?: Record<string, unknown>;
  artifacts?: Record<string, unknown>[];
  consumed?: boolean;
  completed_at?: number;
}

/**
 * LLM context passed to the model (includes conversation_memory if STM enabled).
 */
export interface LLMContext {
  conversation_memory?: ConversationMemory;
  [key: string]: unknown;
}

/**
 * Tool context passed to tools at runtime.
 */
export interface ToolContext {
  session_id?: string;
  trace_id?: string;
  tenant_id?: string;
  user_id?: string;
  [key: string]: unknown;
}

export interface TrajectoryPayload {
  query?: string;
  steps?: TrajectoryStep[];
  llm_context?: LLMContext;
  tool_context?: ToolContext;
  background_results?: Record<string, BackgroundTaskResultPayload>;
  artifacts?: Record<string, unknown>;
  sources?: Record<string, unknown>[];
  metadata?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  trace_id?: string;
  session_id?: string;
}
