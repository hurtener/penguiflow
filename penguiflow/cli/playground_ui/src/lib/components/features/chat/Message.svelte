<script lang="ts">
  import type { ArtifactRef } from '$lib/types/artifacts';
  import type { ChatMessage } from '$lib/types/chat';
  import { formatSize, formatTime, getMimeIcon, getMimeLabel } from '$lib/utils';
  import { renderMarkdown } from '$lib/services';
  import { downloadArtifact } from '$lib/services/api';
  import { getSessionStore } from '$lib/stores';
  import ThinkingPanel from './ThinkingPanel.svelte';
  import PauseCard from './PauseCard.svelte';
  import TypingIndicator from './TypingIndicator.svelte';
  import ComponentRenderer from '$lib/renderers/ComponentRenderer.svelte';
  import { getInteractionsStore } from '$lib/stores';
  import type { PendingInteraction } from '$lib/types/component_artifacts';

  interface Props {
    message: ChatMessage & { artifacts?: ArtifactRef[] };
    onInteractionResult?: (interaction: PendingInteraction, result: unknown) => void;
  }

  let { message, onInteractionResult }: Props = $props();

  const hasObservations = $derived(
    message.role === 'agent' && message.observations && message.observations.trim() !== ''
  );

  const interactionsStore = getInteractionsStore();
  const sessionStore = getSessionStore();
  let downloadingId = $state<string | null>(null);
  let downloadError = $state<string | null>(null);
  const componentArtifacts = $derived(
    interactionsStore.artifacts.filter(a => a.message_id === message.id)
  );

  const pendingInteraction = $derived.by(() => {
    const pending = interactionsStore.pendingInteraction;
    if (!pending || pending.message_id !== message.id) return null;
    return pending;
  });

  async function handleDownload(artifactId: string, filename?: string | null) {
    downloadingId = artifactId;
    downloadError = null;
    if (!sessionStore.sessionId) {
      downloadError = 'Session not available.';
      downloadingId = null;
      return;
    }
    try {
      await downloadArtifact(artifactId, sessionStore.sessionId, filename ?? undefined);
    } catch (err) {
      downloadError = err instanceof Error ? err.message : 'Download failed.';
    } finally {
      downloadingId = null;
    }
  }
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
    {#if message.role === 'agent' && message.artifacts?.length}
      <div class="message-artifacts">
        <div class="artifacts-label">Attachments</div>
        <div class="artifacts-list">
          {#each message.artifacts as artifact (artifact.id)}
            <button
              type="button"
              class="artifact-chip"
              onclick={() => handleDownload(artifact.id, artifact.filename)}
              disabled={downloadingId === artifact.id}
              title={artifact.filename || artifact.id}
            >
              <span class="artifact-icon" data-type={getMimeIcon(artifact.mime_type)}></span>
              <span class="artifact-name">{artifact.filename || artifact.id}</span>
              <span class="artifact-meta">
                {formatSize(artifact.size_bytes)}
                {#if artifact.mime_type}
                  <span class="artifact-pill">{getMimeLabel(artifact.mime_type)}</span>
                {/if}
              </span>
              {#if downloadingId === artifact.id}
                <span class="artifact-spinner"></span>
              {/if}
            </button>
          {/each}
        </div>
        {#if downloadError}
          <div class="artifact-error" role="alert">{downloadError}</div>
        {/if}
      </div>
    {/if}
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

  .message-artifacts {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .artifacts-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--color-muted);
  }

  .artifacts-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .artifact-chip {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 8px 10px;
    border-radius: 10px;
    border: 1px solid var(--color-border);
    background: var(--color-bg, #f9f6f1);
    color: var(--color-text);
    text-align: left;
    cursor: pointer;
    transition: background 0.15s ease;
  }

  .artifact-chip:hover:not(:disabled) {
    background: var(--color-card-bg, #fcfaf7);
  }

  .artifact-chip:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .artifact-icon {
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    flex-shrink: 0;
  }

  .artifact-icon::before {
    content: '📄';
  }

  .artifact-icon[data-type="image"]::before {
    content: '🖼️';
  }

  .artifact-icon[data-type="pdf"]::before {
    content: '📕';
  }

  .artifact-icon[data-type="spreadsheet"]::before {
    content: '📊';
  }

  .artifact-name {
    flex: 1;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .artifact-meta {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
    color: var(--color-muted);
  }

  .artifact-pill {
    padding: 2px 6px;
    border-radius: 999px;
    background: var(--color-tab-bg, #f0ebe4);
    color: var(--color-text);
    font-size: 9px;
    text-transform: uppercase;
  }

  .artifact-spinner {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    border: 2px solid rgba(0, 0, 0, 0.2);
    border-top-color: var(--color-text);
    animation: spin 0.8s linear infinite;
  }

  .artifact-error {
    font-size: 11px;
    color: #b91c1c;
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
