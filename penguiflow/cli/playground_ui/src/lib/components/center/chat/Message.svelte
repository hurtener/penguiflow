<script lang="ts">
  import type { ChatMessage } from '$lib/types';
  import { formatTime } from '$lib/utils';
  import { renderMarkdown } from '$lib/services';
  import ThinkingPanel from './ThinkingPanel.svelte';
  import PauseCard from './PauseCard.svelte';
  import TypingIndicator from './TypingIndicator.svelte';

  interface Props {
    message: ChatMessage;
  }

  let { message }: Props = $props();

  const hasObservations = $derived(
    message.role === 'agent' && message.observations && message.observations.trim() !== ''
  );
</script>

<div class={`message-row ${message.role === 'agent' ? 'agent' : 'user'}`}>
  <div class={`bubble ${message.role}`}>
    {#if hasObservations}
      <ThinkingPanel
        observations={message.observations!}
        open={message.showObservations ?? false}
        isStreaming={message.isStreaming ?? false}
        ontoggle={(open) => { message.showObservations = open; }}
      />
    {/if}
    <div class="markdown-content">{@html renderMarkdown(message.text)}</div>
    {#if message.pause}
      <PauseCard pause={message.pause} />
    {/if}
    {#if message.isStreaming || message.isThinking}
      <TypingIndicator />
    {/if}
  </div>
  <div class="meta-row">
    <span>{formatTime(message.ts)}</span>
    {#if message.traceId}
      <span class="link">#{message.traceId}</span>
    {/if}
  </div>
</div>

<style>
  .message-row {
    margin-bottom: 14px;
  }

  .message-row.user {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
  }

  .bubble {
    padding: 10px 14px;
    border-radius: 16px;
    max-width: 85%;
    font-size: 13px;
    line-height: 1.5;
  }

  .bubble.user {
    background: var(--color-btn-primary-gradient, linear-gradient(135deg, #31a6a0, #1a7c75));
    color: white;
    border-radius: 16px 16px 4px 16px;
  }

  .bubble.agent {
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 16px 16px 16px 4px;
    color: var(--color-text, #1f1f1f);
  }

  .meta-row {
    display: flex;
    gap: 8px;
    margin-top: 4px;
    font-size: 10px;
    color: var(--color-muted, #7a756d);
  }

  .link {
    color: var(--color-primary, #106c67);
    cursor: pointer;
  }
</style>
