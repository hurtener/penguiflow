import { getContext, setContext } from 'svelte';
import type { StateUpdate, TaskState, TaskStatus } from '$lib/types';

const TASKS_STORE_KEY = Symbol('tasks-store');

export interface TasksStore {
  readonly tasks: TaskState[];
  readonly count: number;
  applyUpdate(update: StateUpdate): void;
  setTasks(items: TaskState[]): void;
  clear(): void;
}

export function createTasksStore(): TasksStore {
  let tasksById = $state<Record<string, TaskState>>({});

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
    applyUpdate(update: StateUpdate) {
      const task = ensureTask(update.task_id, update.session_id);
      if (update.trace_id) {
        task.trace_id = update.trace_id;
      }
      if (update.update_type === 'STATUS_CHANGE') {
        if (update.content && typeof update.content === 'object' && !Array.isArray(update.content)) {
          applyStatus(task, update.content as Record<string, unknown>);
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
      }
    },
    setTasks(items: TaskState[]) {
      const next: Record<string, TaskState> = {};
      for (const item of items) {
        next[item.task_id] = { ...item };
      }
      tasksById = next;
    },
    clear() {
      tasksById = {};
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
