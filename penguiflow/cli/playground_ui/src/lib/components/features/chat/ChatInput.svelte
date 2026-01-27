<script lang="ts">
  import { getChatStore, getSessionStore, getTasksStore } from '$lib/stores';
  import { sendSteeringMessage } from '$lib/services/session-stream';
  import type { TaskState } from '$lib/types';

  interface Props {
    onsubmit: () => void;
  }

  let { onsubmit }: Props = $props();
  const chatStore = getChatStore();
  const sessionStore = getSessionStore();
  const tasksStore = getTasksStore();

  // Track pending steering acknowledgment
  let steeringPending = $state(false);

  // Derived: resolve active foreground task from session task-state
  const activeForegroundTask = $derived.by(() => {
    const taskId = tasksStore.foregroundTaskId;
    if (!taskId) return null;
    return tasksStore.tasks.find((task: TaskState) => task.task_id === taskId) ?? null;
  });

  const steeringAvailable = $derived.by(() => {
    if (!activeForegroundTask) return false;
    const status = tasksStore.foregroundStatus ?? activeForegroundTask.status;
    return status === 'RUNNING' || status === 'PENDING' || status === 'PAUSED';
  });

  // Steering context uses background task-state snapshot (avoids misclassification).
  const activeBackgroundTasks = $derived(tasksStore.backgroundTasks);

  let inputMode = $state<'steer' | 'new'>('new');
  let modeOverride = $state(false);

  $effect(() => {
    if (!steeringAvailable) {
      inputMode = 'new';
      modeOverride = false;
      return;
    }
    if (!modeOverride && !chatStore.input.trim()) {
      inputMode = 'steer';
    }
  });

  const isSteeringMode = $derived(steeringAvailable && inputMode === 'steer');

  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleSubmit = async () => {
    if (isSteeringMode) {
      await handleSteeringMessage();
    } else {
      onsubmit();
    }
  };

  const handleSteeringMessage = async () => {
    const message = chatStore.input.trim();
    if (!message || !activeForegroundTask) return;

    steeringPending = true;

    // Build context about active background tasks
    const backgroundContext = activeBackgroundTasks.map(task => ({
      task_id: task.task_id,
      description: task.description || '',
      status: task.status,
      task_type: task.task_type,
      priority: task.priority ?? 0
    }));

    try {
      const accepted = await sendSteeringMessage(
        sessionStore.sessionId,
        activeForegroundTask.task_id,
        message,
        backgroundContext
      );

      if (accepted) {
        // Clear input on successful steering
        chatStore.clearInput();
      }
    } finally {
      // Short delay to show pending state before clearing
      setTimeout(() => {
        steeringPending = false;
      }, 300);
    }
  };

  // Button is disabled only when there's no input text
  // In steering mode, we keep input enabled even while "sending"
  const isButtonDisabled = $derived.by(() => {
    if (isSteeringMode) {
      return !chatStore.input.trim() || steeringPending;
    }
    return sessionStore.isSending || !chatStore.input.trim();
  });

  const selectMode = (mode: 'steer' | 'new') => {
    inputMode = mode;
    modeOverride = true;
  };
</script>

<div class="chat-input" class:steering-mode={isSteeringMode}>
  {#if steeringAvailable}
    <div class="input-modes">
      <span class="mode-label">Mode</span>
      <div class="mode-toggle">
        <button
          type="button"
          class="mode-btn"
          class:active={inputMode === 'new'}
          onclick={() => selectMode('new')}
        >
          New message
        </button>
        <button
          type="button"
          class="mode-btn"
          class:active={inputMode === 'steer'}
          onclick={() => selectMode('steer')}
        >
          Steer task
        </button>
      </div>
    </div>
  {/if}
  {#if steeringPending}
    <div class="steering-indicator">
      <span class="steering-dot"></span>
      Steering...
    </div>
  {/if}
  <div class="input-row">
    <textarea
      placeholder={isSteeringMode ? "Steer the agent with new instructions..." : "Ask your agent something..."}
      bind:value={chatStore.input}
      onkeydown={handleKeydown}
    ></textarea>
    <button
      class="send-btn"
      class:steer-btn={isSteeringMode}
      onclick={handleSubmit}
      disabled={isButtonDisabled}
      title={isSteeringMode ? "Send steering instruction to running task" : "Send message"}
    >
      {isSteeringMode ? "!" : ">"}
    </button>
  </div>
</div>

<style>
  .chat-input {
    display: flex;
    gap: var(--space-sm);
    padding: var(--space-md);
    background: #ffffff; /* Intentionally pure white for chat input area */
    border-top: 1px solid var(--color-border);
    position: relative;
    flex-direction: column;
  }

  .input-row {
    display: flex;
    gap: var(--space-md);
    align-items: stretch;
  }

  .input-modes {
    display: flex;
    align-items: center;
    gap: var(--space-sm);
    font-size: 11px;
    color: var(--color-muted);
  }

  .mode-label {
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .mode-toggle {
    display: inline-flex;
    gap: 6px;
  }

  .mode-btn {
    border: 1px solid var(--color-border);
    background: var(--color-bg, #f9f6f1);
    color: var(--color-text);
    padding: 4px 8px;
    border-radius: 999px;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .mode-btn.active {
    background: var(--color-btn-primary-gradient);
    color: #ffffff;
    border-color: transparent;
  }

  .chat-input.steering-mode {
    border-top-color: #f59e0b;
    background: #fffbf0;
  }

  .steering-indicator {
    position: absolute;
    top: -24px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: #f59e0b;
    background: #fffbf0;
    padding: 4px 12px;
    border-radius: var(--radius-md) var(--radius-md) 0 0;
    border: 1px solid #f59e0b;
    border-bottom: none;
  }

  .steering-dot {
    width: 6px;
    height: 6px;
    background: #f59e0b;
    border-radius: 50%;
    animation: pulse 1s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  textarea {
    flex: 1;
    resize: none;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    padding: var(--space-md) 14px;
    font-size: 13px;
    background: #ffffff; /* Intentionally pure white for input field */
    min-height: 44px;
    max-height: 120px;
    outline: none;
  }

  .steering-mode textarea {
    border-color: #fcd34d;
  }

  textarea:focus {
    border-color: var(--color-primary);
  }

  .steering-mode textarea:focus {
    border-color: #f59e0b;
  }

  .send-btn {
    width: 44px;
    height: 44px;
    border-radius: var(--radius-lg);
    background: var(--color-btn-primary-gradient);
    color: white;
    font-size: 18px;
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
  }

  .send-btn.steer-btn {
    background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
    font-weight: 700;
  }

  .send-btn.steer-btn:hover:not(:disabled) {
    background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);
  }

  .send-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
