export type PauseInfo = {
  reason?: string;
  payload?: Record<string, unknown>;
  resume_token?: string;
};

export type ChatMessage = {
  id: string;
  role: 'user' | 'agent';
  text: string;
  observations?: string;
  showObservations?: boolean;
  isStreaming?: boolean;
  isThinking?: boolean;
  answerStreamDone?: boolean;
  revisionStreamActive?: boolean;
  answerActionSeq?: number | null;
  ts: number;
  traceId?: string;
  latencyMs?: number;
  pause?: PauseInfo;
};

export type PlannerEventPayload = {
  id: string;
  event: string;
  trace_id?: string;
  session_id?: string;
  node?: string;
  latency_ms?: number;
  thought?: string;
  stream_id?: string;
  seq?: number;
  text?: string;
  done?: boolean;
  ts?: number;
  chunk?: unknown;
  artifact_type?: string;
  meta?: Record<string, unknown>;
};
