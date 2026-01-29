import { describe, it, expect } from 'vitest';
import { createTasksStore } from '$lib/stores';

describe('tasksStore', () => {
  it('tracks status updates', () => {
    const store = createTasksStore();
    store.applyUpdate({
      session_id: 'sess',
      task_id: 'task-1',
      update_id: 'u1',
      update_type: 'STATUS_CHANGE',
      content: { status: 'RUNNING', reason: 'running' },
      created_at: new Date().toISOString()
    });
    expect(store.count).toBe(1);
    expect(store.tasks[0].status).toBe('RUNNING');
  });

  it('stores results', () => {
    const store = createTasksStore();
    store.applyUpdate({
      session_id: 'sess',
      task_id: 'task-2',
      update_id: 'u2',
      update_type: 'RESULT',
      content: { payload: { answer: 'done' }, patch_id: 'patch-1' },
      created_at: new Date().toISOString()
    });
    expect(store.tasks[0].status).toBe('COMPLETE');
    expect(store.tasks[0].result).toEqual({ payload: { answer: 'done' }, patch_id: 'patch-1' });
    expect(store.tasks[0].patch_id).toBe('patch-1');
  });

  it('stores progress updates', () => {
    const store = createTasksStore();
    store.applyUpdate({
      session_id: 'sess',
      task_id: 'task-3',
      update_id: 'u3',
      update_type: 'PROGRESS',
      content: { label: 'step', current: 1, total: 3 },
      created_at: new Date().toISOString()
    });
    expect(store.tasks[0].progress).toEqual({ label: 'step', current: 1, total: 3 });
  });
});
