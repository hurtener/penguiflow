<script lang="ts">
  import type { StreamingMessage } from '$lib/agui';
  import ToolCall from './ToolCall.svelte';

  export let message: StreamingMessage;
</script>

<div class={`agui-message-row ${message.role === 'user' ? 'user' : 'assistant'}`}>
  <div class={`agui-bubble ${message.role === 'user' ? 'user' : 'assistant'}`}>
    <div class="agui-role">{message.role}</div>
    <div class="agui-message-content">
      {message.content}{#if message.isStreaming}<span class="cursor">|</span>{/if}
    </div>

    {#if message.toolCalls.length > 0}
      <div class="agui-tool-calls">
        {#each message.toolCalls as tc (tc.id)}
          <ToolCall toolCall={tc} />
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .agui-message-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .agui-message-row.user {
    align-items: flex-end;
  }

  .agui-bubble {
    padding: 10px 14px;
    border-radius: 16px;
    max-width: 85%;
    font-size: 13px;
    line-height: 1.5;
  }

  .agui-bubble.user {
    background: var(--color-btn-primary-gradient);
    color: #ffffff;
    border-radius: 16px 16px 4px 16px;
  }

  .agui-bubble.assistant {
    background: #ffffff;
    border: 1px solid var(--color-border);
    border-radius: 16px 16px 16px 4px;
    color: var(--color-text);
  }

  .agui-role {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.6;
    margin-bottom: 4px;
  }

  .agui-message-content {
    white-space: pre-wrap;
  }

  .cursor {
    animation: blink 0.7s infinite;
  }

  @keyframes blink {
    50% {
      opacity: 0;
    }
  }

  .agui-tool-calls {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
</style>
