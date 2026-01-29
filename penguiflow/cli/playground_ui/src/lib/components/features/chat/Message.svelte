<script lang="ts">
  import type { ChatMessage } from '$lib/types';
  import { formatTime } from '$lib/utils';
  import { renderMarkdown } from '$lib/services';
  import ThinkingPanel from './ThinkingPanel.svelte';
  import PauseCard from './PauseCard.svelte';
  import TypingIndicator from './TypingIndicator.svelte';
  import ComponentRenderer from '$lib/renderers/ComponentRenderer.svelte';
  import { getInteractionsStore } from '$lib/stores';
  import type { PendingInteraction } from '$lib/types';

  interface Props {
    message: ChatMessage;
    onInteractionResult?: (interaction: PendingInteraction, result: unknown) => void;
  }

  let { message, onInteractionResult }: Props = $props();

  const hasObservations = $derived(
    message.role === 'agent' && message.observations && message.observations.trim() !== ''
  );

  const interactionsStore = getInteractionsStore();
  const componentArtifacts = $derived(
    interactionsStore.artifacts.filter(a => a.message_id === message.id)
  );

  const pendingInteraction = $derived.by(() => {
    const pending = interactionsStore.pendingInteraction;
    if (!pending || pending.message_id !== message.id) return null;
    return pending;
  });
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
    {#if message.role === 'agent' && componentArtifacts.length > 0}
      <div class="component-artifacts">
        {#each componentArtifacts as artifact (artifact.id)}
          {#if artifact.title}
            <div class="artifact-title">{artifact.title}</div>
          {/if}
          <ComponentRenderer component={artifact.component} props={artifact.props} />
        {/each}
      </div>
    {/if}
    {#if message.role === 'agent' && pendingInteraction}
      <div class="component-interaction">
        <ComponentRenderer
          component={pendingInteraction.component}
          props={pendingInteraction.props}
          onResult={(result) => onInteractionResult?.(pendingInteraction, result)}
        />
      </div>
    {/if}
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
    background: var(--color-btn-primary-gradient);
    color: white;
    border-radius: 16px 16px 4px 16px;
  }

  .bubble.agent {
    background: #ffffff; /* Intentionally pure white for chat bubbles */
    border: 1px solid var(--color-border);
    border-radius: 16px 16px 16px 4px;
    color: var(--color-text);
  }

  .component-artifacts {
    margin-top: 0.5rem;
  }

  .artifact-title {
    font-size: 0.75rem;
    color: #64748b;
    margin: 0.5rem 0 0.25rem;
  }

  .component-interaction {
    margin-top: 0.75rem;
  }

  .meta-row {
    display: flex;
    gap: var(--space-md);
    margin-top: var(--space-xs);
    font-size: 10px;
    color: var(--color-muted);
  }

  .link {
    color: var(--color-primary);
    cursor: pointer;
  }
</style>
