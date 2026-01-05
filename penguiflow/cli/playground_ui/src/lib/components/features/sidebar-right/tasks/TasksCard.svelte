<script lang="ts">
  import { Card } from '$lib/components/composites';
  import { Pill } from '$lib/components/primitives';
  import { getSessionStore, getTasksStore } from '$lib/stores';
  import { applyContextPatch, steerTask } from '$lib/services/api';
  import type { TaskState } from '$lib/types';

  const tasksStore = getTasksStore();
  const sessionStore = getSessionStore();

  // Track which task is expanded for details view
  let expandedTaskId = $state<string | null>(null);

  // Derived: separate foreground and background tasks
  const foregroundTasks = $derived.by(() => {
    return tasksStore.tasks.filter((task: TaskState) => task.task_type === 'FOREGROUND');
  });

  const backgroundTasks = $derived.by(() => {
    return tasksStore.tasks.filter((task: TaskState) => task.task_type === 'BACKGROUND');
  });

  // Derived: check if there's a running foreground task
  const hasForegroundRunning = $derived.by(() => {
    return foregroundTasks.some((task: TaskState) => task.status === 'RUNNING');
  });

  // Derived: count of active background tasks
  const activeBackgroundCount = $derived.by(() => {
    return backgroundTasks.filter(
      (task: TaskState) => task.status === 'RUNNING' || task.status === 'PENDING' || task.status === 'PAUSED'
    ).length;
  });

  function statusClass(status: string): string {
    return `status-${status.toLowerCase()}`;
  }

  function toggleExpanded(taskId: string) {
    expandedTaskId = expandedTaskId === taskId ? null : taskId;
  }

  async function cancelTask(taskId: string, event: MouseEvent) {
    event.stopPropagation();
    await steerTask(sessionStore.sessionId, taskId, 'CANCEL', { reason: 'user_cancelled' });
  }

  async function pauseTask(taskId: string, event: MouseEvent) {
    event.stopPropagation();
    await steerTask(sessionStore.sessionId, taskId, 'PAUSE', {});
  }

  async function resumeTask(taskId: string, event: MouseEvent) {
    event.stopPropagation();
    await steerTask(sessionStore.sessionId, taskId, 'RESUME', {});
  }

  async function bumpPriority(taskId: string, delta: number, event: MouseEvent) {
    event.stopPropagation();
    const task = tasksStore.tasks.find((item: TaskState) => item.task_id === taskId);
    const current = task?.priority ?? 0;
    await steerTask(sessionStore.sessionId, taskId, 'PRIORITIZE', { priority: current + delta });
  }

  async function applyPatch(taskId: string, patchId: string, event: MouseEvent) {
    event.stopPropagation();
    const ok = await applyContextPatch(sessionStore.sessionId, patchId, 'apply');
    if (ok) {
      const task = tasksStore.tasks.find((item: TaskState) => item.task_id === taskId);
      if (task) {
        task.patch_id = null;
      }
    }
  }

  function progressPercent(progress?: { current?: number; total?: number }) {
    if (!progress?.total || progress.total <= 0 || progress.current == null) return null;
    return Math.min(100, Math.max(0, Math.round((progress.current / progress.total) * 100)));
  }

  function formatTimestamp(ts?: string): string {
    if (!ts) return '';
    const date = new Date(ts);
    return date.toLocaleTimeString();
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
    {#if hasForegroundRunning}
      <span class="foreground-indicator">
        <span class="running-dot"></span>
        Active
      </span>
    {/if}
  </div>

  <div class="tasks-body">
    {#if tasksStore.count === 0}
      <p class="no-tasks">No active tasks yet.</p>
    {:else}
      <!-- Foreground Tasks Section -->
      {#if foregroundTasks.length > 0}
        <div class="tasks-section">
          <div class="section-header">
            <span class="section-label">Foreground</span>
          </div>
          <div class="tasks-list">
            {#each foregroundTasks as task (task.task_id)}
              <div
                class="task-row"
                class:expanded={expandedTaskId === task.task_id}
                class:task-running={task.status === 'RUNNING'}
                onclick={() => toggleExpanded(task.task_id)}
                role="button"
                tabindex="0"
                onkeydown={(e) => e.key === 'Enter' && toggleExpanded(task.task_id)}
              >
                <div class="task-main">
                  <div class="task-header-row">
                    <div class="task-title">{task.description ?? `Task ${task.task_id.slice(0, 8)}`}</div>
                    <Pill variant="ghost" size="small" class={statusClass(task.status)}>
                      {task.status}
                    </Pill>
                  </div>
                  <div class="task-meta">
                    <span class="task-id">{task.task_id.slice(0, 8)}</span>
                    {#if task.updated_at}
                      <span class="task-time">{formatTimestamp(task.updated_at)}</span>
                    {/if}
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
                        ></div>
                      </div>
                    </div>
                  {/if}
                  <!-- Expanded Details -->
                  {#if expandedTaskId === task.task_id}
                    <div class="task-details">
                      {#if task.trace_id}
                        <div class="detail-row">
                          <span class="detail-label">Trace:</span>
                          <span class="detail-value mono">{task.trace_id.slice(0, 12)}...</span>
                        </div>
                      {/if}
                      <div class="detail-row">
                        <span class="detail-label">Priority:</span>
                        <span class="detail-value">{task.priority}</span>
                      </div>
                      {#if task.error}
                        <div class="detail-row error">
                          <span class="detail-label">Error:</span>
                          <span class="detail-value">{task.error}</span>
                        </div>
                      {/if}
                      {#if task.result}
                        <div class="task-result">
                          <div class="task-result-title">Result</div>
                          <div class="task-result-body">
                            {JSON.stringify(task.result).slice(0, 240)}{JSON.stringify(task.result).length > 240 ? '...' : ''}
                          </div>
                        </div>
                      {/if}
                    </div>
                  {/if}
                </div>
                <div class="task-actions">
                  {#if task.status === 'RUNNING' || task.status === 'PAUSED'}
                    <button type="button" class="task-btn danger" onclick={(e) => cancelTask(task.task_id, e)}>
                      Cancel
                    </button>
                  {/if}
                  {#if task.status === 'RUNNING'}
                    <button type="button" class="task-btn" onclick={(e) => pauseTask(task.task_id, e)}>
                      Pause
                    </button>
                  {/if}
                  {#if task.status === 'PAUSED'}
                    <button type="button" class="task-btn primary" onclick={(e) => resumeTask(task.task_id, e)}>
                      Resume
                    </button>
                  {/if}
                  {#if task.patch_id}
                    <button type="button" class="task-btn primary" onclick={(e) => applyPatch(task.task_id, task.patch_id!, e)}>
                      Apply
                    </button>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}

      <!-- Background Tasks Section -->
      {#if backgroundTasks.length > 0}
        <div class="tasks-section">
          <div class="section-header">
            <span class="section-label">Background</span>
            {#if activeBackgroundCount > 0}
              <span class="section-count">{activeBackgroundCount} active</span>
            {/if}
          </div>
          <div class="tasks-list">
            {#each backgroundTasks as task (task.task_id)}
              <div
                class="task-row"
                class:expanded={expandedTaskId === task.task_id}
                class:task-running={task.status === 'RUNNING'}
                class:task-paused={task.status === 'PAUSED'}
                onclick={() => toggleExpanded(task.task_id)}
                role="button"
                tabindex="0"
                onkeydown={(e) => e.key === 'Enter' && toggleExpanded(task.task_id)}
              >
                <div class="task-main">
                  <div class="task-header-row">
                    <div class="task-title">{task.description ?? `Task ${task.task_id.slice(0, 8)}`}</div>
                    <Pill variant="ghost" size="small" class={statusClass(task.status)}>
                      {task.status}
                    </Pill>
                  </div>
                  <div class="task-meta">
                    <span class="task-id">{task.task_id.slice(0, 8)}</span>
                    {#if task.priority !== 0}
                      <span class="task-priority">P{task.priority}</span>
                    {/if}
                    {#if task.updated_at}
                      <span class="task-time">{formatTimestamp(task.updated_at)}</span>
                    {/if}
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
                        ></div>
                      </div>
                    </div>
                  {/if}
                  <!-- Expanded Details -->
                  {#if expandedTaskId === task.task_id}
                    <div class="task-details">
                      {#if task.trace_id}
                        <div class="detail-row">
                          <span class="detail-label">Trace:</span>
                          <span class="detail-value mono">{task.trace_id.slice(0, 12)}...</span>
                        </div>
                      {/if}
                      <div class="detail-row">
                        <span class="detail-label">Priority:</span>
                        <span class="detail-value">{task.priority}</span>
                      </div>
                      {#if task.error}
                        <div class="detail-row error">
                          <span class="detail-label">Error:</span>
                          <span class="detail-value">{task.error}</span>
                        </div>
                      {/if}
                      {#if task.result}
                        <div class="task-result">
                          <div class="task-result-title">Result</div>
                          <div class="task-result-body">
                            {JSON.stringify(task.result).slice(0, 240)}{JSON.stringify(task.result).length > 240 ? '...' : ''}
                          </div>
                        </div>
                      {/if}
                    </div>
                  {/if}
                </div>
                <div class="task-actions">
                  <div class="priority-controls">
                    <button
                      type="button"
                      class="task-btn small"
                      aria-label="Increase task priority"
                      onclick={(e) => bumpPriority(task.task_id, 1, e)}
                    >
                      +
                    </button>
                    <button
                      type="button"
                      class="task-btn small"
                      aria-label="Decrease task priority"
                      onclick={(e) => bumpPriority(task.task_id, -1, e)}
                    >
                      -
                    </button>
                  </div>
                  {#if task.status === 'RUNNING' || task.status === 'PAUSED'}
                    <button type="button" class="task-btn danger" onclick={(e) => cancelTask(task.task_id, e)}>
                      X
                    </button>
                  {/if}
                  {#if task.status === 'RUNNING'}
                    <button type="button" class="task-btn" onclick={(e) => pauseTask(task.task_id, e)}>
                      ||
                    </button>
                  {/if}
                  {#if task.status === 'PAUSED'}
                    <button type="button" class="task-btn primary" onclick={(e) => resumeTask(task.task_id, e)}>
                      >
                    </button>
                  {/if}
                  {#if task.patch_id}
                    <button type="button" class="task-btn primary" onclick={(e) => applyPatch(task.task_id, task.patch_id!, e)}>
                      Apply
                    </button>
                  {/if}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    {/if}
  </div>
</Card>

<style>
  :global(.tasks-card) {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    max-height: 380px;
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

  .foreground-indicator {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-weight: 600;
    color: #f59e0b;
    background: #fffbf0;
    padding: 4px 10px;
    border-radius: var(--radius-md, 8px);
    border: 1px solid #fcd34d;
  }

  .running-dot {
    width: 8px;
    height: 8px;
    background: #f59e0b;
    border-radius: 50%;
    animation: pulse 1s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
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

  .tasks-section {
    margin-bottom: var(--space-md, 12px);
  }

  .tasks-section:last-child {
    margin-bottom: 0;
  }

  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-sm, 8px);
    padding-bottom: 4px;
    border-bottom: 1px solid var(--color-border, #e8e1d7);
  }

  .section-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-muted, #7a756d);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .section-count {
    font-size: 10px;
    color: var(--color-primary, #31a6a0);
    font-weight: 500;
  }

  .tasks-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 8px);
  }

  .task-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: var(--space-sm, 8px);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: var(--radius-md, 8px);
    background: var(--color-card-bg, #fffdf9);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .task-row:hover {
    border-color: var(--color-primary, #31a6a0);
    background: #fefdfb;
  }

  .task-row.expanded {
    border-color: var(--color-primary, #31a6a0);
  }

  .task-row.task-running {
    border-left: 3px solid #f59e0b;
  }

  .task-row.task-paused {
    border-left: 3px solid #9ca3af;
    opacity: 0.85;
  }

  .task-main {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
    min-width: 0;
  }

  .task-header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .task-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .task-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--color-muted, #7a756d);
    font-size: 11px;
    flex-wrap: wrap;
  }

  .task-id {
    font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  }

  .task-time {
    color: var(--color-muted, #7a756d);
  }

  .task-priority {
    font-weight: 600;
    color: var(--color-primary, #31a6a0);
    background: rgba(49, 166, 160, 0.1);
    padding: 1px 4px;
    border-radius: 3px;
  }

  .task-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  .priority-controls {
    display: flex;
    flex-direction: row;
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

  .task-details {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px dashed var(--color-border, #e8e1d7);
  }

  .detail-row {
    display: flex;
    gap: 8px;
    font-size: 11px;
    margin-bottom: 4px;
  }

  .detail-label {
    color: var(--color-muted, #7a756d);
    font-weight: 500;
    min-width: 60px;
  }

  .detail-value {
    color: var(--color-text, #1f1f1f);
  }

  .detail-value.mono {
    font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  }

  .detail-row.error .detail-value {
    color: #c62828;
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
    font-size: 10px;
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

  .task-btn.small {
    padding: 2px 6px;
    font-size: 10px;
    font-weight: 600;
  }

  .task-btn.primary {
    background: var(--color-primary, #31a6a0);
    color: white;
  }

  .task-btn.primary:hover:not(:disabled) {
    background: #2a9089;
  }

  .task-btn.danger {
    background: #fef2f2;
    color: #c62828;
  }

  .task-btn.danger:hover:not(:disabled) {
    background: #fee2e2;
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

  :global(.status-running) {
    color: #f59e0b !important;
  }

  :global(.status-pending) {
    color: #6b7280 !important;
  }

  :global(.status-paused) {
    color: #9ca3af !important;
  }

  :global(.status-complete) {
    color: #2e7d32 !important;
  }

  :global(.status-failed),
  :global(.status-cancelled) {
    color: #c62828 !important;
  }
</style>
