import { getContext, setContext } from 'svelte';
import type { StateUpdate, TaskState, TaskStatus, TaskType } from '$lib/types';

const TASKS_STORE_KEY = Symbol('tasks-store');

/**
 * Background task info returned from task-state endpoint.
 */
export interface BackgroundTaskInfo {
  task_id: string;
  description: string | null;
  status: string;
  task_type: string;
  priority: number;
}

/**
 * Response shape from GET /session/{sessionId}/task-state
 */
export interface TaskStateResponse {
  foreground_task_id: string | null;
  foreground_status: string | null;
  background_tasks: BackgroundTaskInfo[];
}

export interface TasksStore {
  readonly tasks: TaskState[];
  readonly count: number;
  readonly foregroundTaskId: string | null;
  readonly foregroundStatus: string | null;
  readonly backgroundTasks: BackgroundTaskInfo[];
  readonly isPolling: boolean;
  readonly hasForegroundRunning: boolean;
  readonly hasActiveBackgroundTasks: boolean;
  readonly shouldShowSteerButton: boolean;
  applyUpdate(update: StateUpdate): void;
  setTasks(items: TaskState[]): void;
  fetchTaskState(sessionId: string): Promise<void>;
  setPolling(polling: boolean): void;
  clear(): void;
}

export function createTasksStore(): TasksStore {
  let tasksById = $state<Record<string, TaskState>>({});
  let foregroundTaskId = $state<string | null>(null);
  let foregroundStatus = $state<string | null>(null);
  let backgroundTasks = $state<BackgroundTaskInfo[]>([]);
  let isPolling = $state(false);

  function ensureTask(taskId: string, sessionId: string): TaskState {
    const existing = tasksById[taskId];
    if (existing) return existing;
    const created: TaskState = {
      task_id: taskId,
      session_id: sessionId,
      status: 'PENDING',
      task_type: 'FOREGROUND',
      priority: 0
    };
    tasksById = { ...tasksById, [taskId]: created };
    return created;
  }

  function applyStatus(task: TaskState, content: Record<string, unknown>) {
    const status = content.status as TaskStatus | undefined;
    if (status) {
      task.status = status;
    }
    const taskType = content.task_type;
    if (typeof taskType === 'string') {
      task.task_type = taskType as TaskState['task_type'];
    }
    const priority = content.priority;
    if (typeof priority === 'number') {
      task.priority = priority;
    }
    const progress = content.progress;
    if (progress && typeof progress === 'object' && !Array.isArray(progress)) {
      task.progress = progress as TaskState['progress'];
    }
    task.updated_at = new Date().toISOString();
  }

  return {
    get tasks() {
      return Object.values(tasksById).sort((a, b) => {
        const at = a.updated_at ? Date.parse(a.updated_at) : 0;
        const bt = b.updated_at ? Date.parse(b.updated_at) : 0;
        return bt - at;
      });
    },
    get count() {
      return Object.keys(tasksById).length;
    },
    get foregroundTaskId() {
      return foregroundTaskId;
    },
    get foregroundStatus() {
      return foregroundStatus;
    },
    get backgroundTasks() {
      return backgroundTasks;
    },
    get isPolling() {
      return isPolling;
    },
    get hasForegroundRunning() {
      return foregroundTaskId !== null && foregroundStatus === 'RUNNING';
    },
    get hasActiveBackgroundTasks() {
      return backgroundTasks.length > 0;
    },
    get shouldShowSteerButton() {
      return foregroundTaskId !== null && foregroundStatus === 'RUNNING';
    },
    applyUpdate(update: StateUpdate) {
      const task = ensureTask(update.task_id, update.session_id);
      if (update.trace_id) {
        task.trace_id = update.trace_id;
      }
      if (update.update_type === 'STATUS_CHANGE') {
        if (update.content && typeof update.content === 'object' && !Array.isArray(update.content)) {
          const content = update.content as Record<string, unknown>;
          applyStatus(task, content);

          // Update foreground tracking based on status changes
          const taskType = content.task_type as string | undefined;
          const status = content.status as string | undefined;
          if (taskType === 'FOREGROUND') {
            if (status === 'RUNNING') {
              foregroundTaskId = update.task_id;
              foregroundStatus = status;
            } else if (status === 'COMPLETE' || status === 'FAILED' || status === 'CANCELLED') {
              if (foregroundTaskId === update.task_id) {
                foregroundTaskId = null;
                foregroundStatus = null;
              }
            } else if (foregroundTaskId === update.task_id) {
              foregroundStatus = status ?? null;
            }
          }

          // Update background tasks tracking
          if (taskType === 'BACKGROUND') {
            const activeStatuses = new Set(['RUNNING', 'PENDING', 'PAUSED']);
            if (status && activeStatuses.has(status)) {
              // Add or update in background tasks
              const existing = backgroundTasks.find(t => t.task_id === update.task_id);
              if (!existing) {
                backgroundTasks = [...backgroundTasks, {
                  task_id: update.task_id,
                  description: task.description ?? null,
                  status: status,
                  task_type: taskType,
                  priority: task.priority
                }];
              } else {
                backgroundTasks = backgroundTasks.map(t =>
                  t.task_id === update.task_id
                    ? { ...t, status: status }
                    : t
                );
              }
            } else {
              // Remove from background tasks
              backgroundTasks = backgroundTasks.filter(t => t.task_id !== update.task_id);
            }
          }
        }
        return;
      }
      if (update.update_type === 'PROGRESS') {
        if (update.content && typeof update.content === 'object' && !Array.isArray(update.content)) {
          task.progress = update.content as TaskState['progress'];
          task.updated_at = new Date().toISOString();
        }
        return;
      }
      if (update.update_type === 'RESULT') {
        const content = update.content as Record<string, unknown> | undefined;
        task.result = update.content;
        if (content && typeof content === 'object') {
          if ('task_patch' in content) {
            task.task_patch = (content as Record<string, unknown>).task_patch;
          }
          if ('patch_id' in content) {
            task.patch_id = (content as Record<string, unknown>).patch_id as string | null;
          }
        }
        task.status = 'COMPLETE';
        task.updated_at = new Date().toISOString();

        // Clear foreground if this was the foreground task
        if (foregroundTaskId === update.task_id) {
          foregroundTaskId = null;
          foregroundStatus = null;
        }
        return;
      }
      if (update.update_type === 'CHECKPOINT') {
        const content = update.content as Record<string, unknown> | undefined;
        if (content && typeof content === 'object') {
          const patchId = content.patch_id as string | undefined;
          if (patchId) {
            task.patch_id = patchId;
            task.updated_at = new Date().toISOString();
          }
        }
        return;
      }
      if (update.update_type === 'ERROR') {
        if (update.content && typeof update.content === 'object' && !Array.isArray(update.content)) {
          task.error = String((update.content as Record<string, unknown>).error ?? 'Error');
        } else {
          task.error = 'Error';
        }
        task.status = 'FAILED';
        task.updated_at = new Date().toISOString();

        // Clear foreground if this was the foreground task
        if (foregroundTaskId === update.task_id) {
          foregroundTaskId = null;
          foregroundStatus = null;
        }
        // Remove from background tasks
        backgroundTasks = backgroundTasks.filter(t => t.task_id !== update.task_id);
      }
    },
    setTasks(items: TaskState[]) {
      const next: Record<string, TaskState> = {};
      for (const item of items) {
        next[item.task_id] = { ...item };
      }
      tasksById = next;
    },
    async fetchTaskState(sessionId: string) {
      try {
        isPolling = true;
        const response = await fetch(`/session/${sessionId}/task-state`);
        if (!response.ok) {
          console.error('Failed to fetch task state:', response.statusText);
          return;
        }
        const data: TaskStateResponse = await response.json();
        foregroundTaskId = data.foreground_task_id;
        foregroundStatus = data.foreground_status;
        backgroundTasks = data.background_tasks;
      } catch (err) {
        console.error('Failed to fetch task state:', err);
      } finally {
        isPolling = false;
      }
    },
    setPolling(polling: boolean) {
      isPolling = polling;
    },
    clear() {
      tasksById = {};
      foregroundTaskId = null;
      foregroundStatus = null;
      backgroundTasks = [];
      isPolling = false;
    }
  };
}

export function setTasksStore(store: TasksStore = createTasksStore()): TasksStore {
  setContext(TASKS_STORE_KEY, store);
  return store;
}

export function getTasksStore(): TasksStore {
  return getContext<TasksStore>(TASKS_STORE_KEY);
}
