export type UpdateType =
  | 'THINKING'
  | 'PROGRESS'
  | 'TOOL_CALL'
  | 'RESULT'
  | 'ERROR'
  | 'CHECKPOINT'
  | 'STATUS_CHANGE'
  | 'NOTIFICATION';

export type TaskStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PAUSED'
  | 'COMPLETE'
  | 'FAILED'
  | 'CANCELLED';

export type TaskType = 'FOREGROUND' | 'BACKGROUND';

export interface StateUpdate {
  session_id: string;
  task_id: string;
  trace_id?: string | null;
  update_id: string;
  update_type: UpdateType;
  content: unknown;
  step_index?: number | null;
  total_steps?: number | null;
  created_at: string;
}

export interface TaskState {
  task_id: string;
  session_id: string;
  status: TaskStatus;
  task_type: TaskType;
  priority: number;
  trace_id?: string | null;
  description?: string | null;
  result?: unknown;
  task_patch?: unknown;
  patch_id?: string | null;
  error?: string | null;
  progress?: {
    label?: string;
    current?: number;
    total?: number;
    details?: Record<string, unknown>;
  };
  updated_at?: string;
  created_at?: string;
}
