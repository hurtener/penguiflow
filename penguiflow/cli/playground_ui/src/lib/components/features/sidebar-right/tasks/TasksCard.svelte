<script lang="ts">
  import { Card } from '$lib/components/composites';
  import { Pill } from '$lib/components/primitives';
  import { getSessionStore, getTasksStore } from '$lib/stores';
  import { applyContextPatch, steerTask } from '$lib/services/api';

  const tasksStore = getTasksStore();
  const sessionStore = getSessionStore();

  function statusClass(status: string): string {
    return `status-${status.toLowerCase()}`;
  }

  async function cancelTask(taskId: string) {
    await steerTask(sessionStore.sessionId, taskId, 'CANCEL', { reason: 'user_cancelled' });
  }

  async function pauseTask(taskId: string) {
    await steerTask(sessionStore.sessionId, taskId, 'PAUSE', {});
  }

  async function resumeTask(taskId: string) {
    await steerTask(sessionStore.sessionId, taskId, 'RESUME', {});
  }

  async function bumpPriority(taskId: string, delta: number) {
    const task = tasksStore.tasks.find(item => item.task_id === taskId);
    const current = task?.priority ?? 0;
    await steerTask(sessionStore.sessionId, taskId, 'PRIORITIZE', { priority: current + delta });
  }

  async function applyPatch(taskId: string, patchId: string) {
    const ok = await applyContextPatch(sessionStore.sessionId, patchId, 'apply');
    if (ok) {
      const task = tasksStore.tasks.find(item => item.task_id === taskId);
      if (task) {
        task.patch_id = null;
      }
    }
  }

  function progressPercent(progress?: { current?: number; total?: number }) {
    if (!progress?.total || progress.total <= 0 || progress.current == null) return null;
    return Math.min(100, Math.max(0, Math.round((progress.current / progress.total) * 100)));
  }
</script>

<Card class="tasks-card">
  <div class="tasks-header">
    <h3 class="tasks-title">
      Tasks
      {#if tasksStore.count > 0}
        <span class="count-badge">{tasksStore.count}</span>
      {/if}
    </h3>
  </div>

  <div class="tasks-body">
    {#if tasksStore.count === 0}
      <p class="no-tasks">No active tasks yet.</p>
    {:else}
      <div class="tasks-list">
        {#each tasksStore.tasks as task (task.task_id)}
          <div class="task-row">
            <div class="task-main">
              <div class="task-title">{task.description ?? `Task ${task.task_id.slice(0, 8)}`}</div>
              <div class="task-meta">
                <Pill variant="subtle" size="small">{task.task_type}</Pill>
                <span class="task-id">{task.task_id.slice(0, 8)}</span>
              </div>
              {#if task.progress}
                <div class="task-progress">
                  <div class="progress-meta">
                    <span>{task.progress.label ?? 'Progress'}</span>
                    {#if progressPercent(task.progress)}
                      <span>{progressPercent(task.progress)}%</span>
                    {/if}
                  </div>
                  <div class="progress-bar">
                    <div
                      class="progress-bar-fill"
                      style={`width: ${progressPercent(task.progress) ?? 0}%`}
                    />
                  </div>
                </div>
              {/if}
              {#if task.result}
                <div class="task-result">
                  <div class="task-result-title">Result</div>
                  <div class="task-result-body">
                    {JSON.stringify(task.result).slice(0, 240)}{JSON.stringify(task.result).length > 240 ? '…' : ''}
                  </div>
                </div>
              {/if}
            </div>
            <div class="task-actions">
              <Pill variant="ghost" size="small" class={statusClass(task.status)}>
                {task.status}
              </Pill>
              <div class="priority-controls">
                <button
                  type="button"
                  class="task-btn"
                  aria-label="Increase task priority"
                  onclick={() => bumpPriority(task.task_id, 1)}
                >
                  ▲
                </button>
                <button
                  type="button"
                  class="task-btn"
                  aria-label="Decrease task priority"
                  onclick={() => bumpPriority(task.task_id, -1)}
                >
                  ▼
                </button>
              </div>
              {#if task.status === 'RUNNING' || task.status === 'PAUSED'}
                <button type="button" class="task-btn" onclick={() => cancelTask(task.task_id)}>
                  Cancel
                </button>
              {/if}
              {#if task.status === 'RUNNING'}
                <button type="button" class="task-btn" onclick={() => pauseTask(task.task_id)}>
                  Pause
                </button>
              {/if}
              {#if task.status === 'PAUSED'}
                <button type="button" class="task-btn" onclick={() => resumeTask(task.task_id)}>
                  Resume
                </button>
              {/if}
              {#if task.patch_id}
                <button type="button" class="task-btn" onclick={() => applyPatch(task.task_id, task.patch_id!)}>
                  Apply
                </button>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</Card>

<style>
  :global(.tasks-card) {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    max-height: 320px;
  }

  .tasks-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-md, 12px);
  }

  .tasks-title {
    margin: 0;
    font-size: 14px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
    display: flex;
    align-items: center;
    gap: var(--space-sm, 8px);
  }

  .count-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 20px;
    height: 20px;
    padding: 0 6px;
    font-size: 11px;
    font-weight: 600;
    background: var(--color-primary, #31a6a0);
    color: white;
    border-radius: 10px;
  }

  .tasks-body {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .no-tasks {
    margin: 0;
    padding: var(--space-lg, 16px);
    text-align: center;
    color: var(--color-muted, #7a756d);
  }

  .tasks-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 8px);
  }

  .task-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-sm, 8px);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: var(--radius-md, 8px);
    background: var(--color-card-bg, #fffdf9);
  }

  .task-main {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .task-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }

  .task-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--color-muted, #7a756d);
    font-size: 11px;
  }

  .task-id {
    font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  }

  .task-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .priority-controls {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .task-progress {
    margin-top: 8px;
  }

  .progress-meta {
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: var(--color-muted, #7a756d);
    margin-bottom: 4px;
  }

  .progress-bar {
    height: 6px;
    background: #f0ebe4;
    border-radius: 999px;
    overflow: hidden;
  }

  .progress-bar-fill {
    height: 100%;
    background: var(--color-primary, #31a6a0);
    transition: width 0.3s ease;
  }

  .task-result {
    margin-top: 8px;
    font-size: 11px;
    color: var(--color-muted, #7a756d);
  }

  .task-result-title {
    font-weight: 600;
    margin-bottom: 4px;
    color: var(--color-text, #1f1f1f);
  }

  .task-result-body {
    background: #f6f2ec;
    padding: 6px;
    border-radius: 6px;
    font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .task-btn {
    border: none;
    background: var(--color-tab-bg, #f0ebe4);
    color: var(--color-text, #1f1f1f);
    padding: 4px 8px;
    border-radius: var(--radius-sm, 6px);
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .task-btn:hover:not(:disabled) {
    background: var(--color-border, #e8e1d7);
  }

  .task-btn:focus-visible {
    outline: 2px solid var(--color-primary, #31a6a0);
    outline-offset: 2px;
  }

  .task-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .status-running {
    color: var(--color-primary, #31a6a0);
  }

  .status-complete {
    color: #2e7d32;
  }

  .status-failed,
  .status-cancelled {
    color: #c62828;
  }
</style>
