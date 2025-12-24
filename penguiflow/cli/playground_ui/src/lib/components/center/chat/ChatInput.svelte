<script lang="ts">
  import { chatStore, sessionStore } from '$lib/stores';

  interface Props {
    onsubmit: () => void;
  }

  let { onsubmit }: Props = $props();

  const handleKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onsubmit();
    }
  };
</script>

<div class="chat-input">
  <textarea
    placeholder="Ask your agent something..."
    bind:value={chatStore.input}
    onkeydown={handleKeydown}
  ></textarea>
  <button
    class="send-btn"
    onclick={onsubmit}
    disabled={sessionStore.isSending || !chatStore.input.trim()}
  >
    >
  </button>
</div>

<style>
  .chat-input {
    display: flex;
    gap: var(--space-md);
    padding: var(--space-md);
    background: #ffffff; /* Intentionally pure white for chat input area */
    border-top: 1px solid var(--color-border);
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

  textarea:focus {
    border-color: var(--color-primary);
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
  }

  .send-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
