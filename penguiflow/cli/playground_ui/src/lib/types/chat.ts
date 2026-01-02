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
  artifact_id?: string;
  mime_type?: string;
  filename?: string;
  meta?: Record<string, unknown>;
  // Tool call aggregation fields
  tool_call_id?: string;
  tool_call_name?: string;
  args?: string;
  result?: string;
  start_ts?: number;
  end_ts?: number;
};

/** Aggregated event for display in events panel */
export type DisplayEvent = {
  id: string;
  type: 'step' | 'tool_call' | 'artifact' | 'other';
  name: string;
  description?: string;
  duration_ms?: number;
  args?: string;
  result?: string;
  ts: number;
};
