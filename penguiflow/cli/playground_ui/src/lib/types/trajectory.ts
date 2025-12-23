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

export interface TrajectoryPayload {
  steps?: TrajectoryStep[];
}
