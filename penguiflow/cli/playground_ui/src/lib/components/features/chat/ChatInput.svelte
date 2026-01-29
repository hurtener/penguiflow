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

  // Derived: check if foreground task is currently running
  const activeForegroundTask = $derived.by(() => {
    const active = tasksStore.tasks.filter((task: TaskState) => {
      if (task.task_type !== 'FOREGROUND') return false;
      return task.status === 'RUNNING' || task.status === 'PENDING' || task.status === 'PAUSED';
    });
    if (!active.length) return null;
    // Prefer RUNNING task when present, otherwise pick the most recently updated.
    const running = active.find(task => task.status === 'RUNNING');
    if (running) return running;
    return active
      .slice()
      .sort((a, b) => String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? '')))[0];
  });

  // Derived: get active background tasks for steering context
  const activeBackgroundTasks = $derived.by(() => {
    return tasksStore.tasks.filter(
      (task: TaskState) => task.task_type === 'BACKGROUND' &&
        (task.status === 'RUNNING' || task.status === 'PENDING' || task.status === 'PAUSED')
    );
  });

  // Determine if we're in steering mode
  const isSteeringMode = $derived(!!activeForegroundTask);

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
    const backgroundContext = activeBackgroundTasks.map((task: TaskState) => ({
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
</script>

<div class="chat-input" class:steering-mode={isSteeringMode}>
  {#if steeringPending}
    <div class="steering-indicator">
      <span class="steering-dot"></span>
      Steering...
    </div>
  {/if}
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

<style>
  .chat-input {
    display: flex;
    gap: var(--space-md);
    padding: var(--space-md);
    background: #ffffff; /* Intentionally pure white for chat input area */
    border-top: 1px solid var(--color-border);
    position: relative;
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
