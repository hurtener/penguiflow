<script lang="ts">
  import type { ArtifactRef } from '$lib/types';
  import { downloadArtifact } from '$lib/services/api';
  import { getSessionStore } from '$lib/stores';
  import { Pill } from '$lib/components/primitives';
  import { formatSize, getMimeIcon, getMimeLabel } from '$lib/utils';

  interface Props {
    artifact: ArtifactRef;
  }

  let { artifact }: Props = $props();
  const sessionStore = getSessionStore();
  let downloading = $state(false);
  let error = $state<string | null>(null);

  async function handleDownload() {
    downloading = true;
    error = null;
    try {
      await downloadArtifact(
        artifact.id,
        sessionStore.sessionId,
        artifact.filename ?? undefined
      );
    } catch (e) {
      error = e instanceof Error ? e.message : 'Download failed';
    } finally {
      downloading = false;
    }
  }
</script>

<div class="artifact-item">
  <div class="artifact-info">
    <span class="artifact-icon" data-type={getMimeIcon(artifact.mime_type)}></span>
    <div class="artifact-details">
      <span class="artifact-name" title={artifact.filename || artifact.id}>
        {artifact.filename || artifact.id}
      </span>
      <span class="artifact-meta">
        {formatSize(artifact.size_bytes)}
        {#if artifact.mime_type}
          <Pill size="small" variant="subtle">{getMimeLabel(artifact.mime_type)}</Pill>
        {/if}
      </span>
    </div>
  </div>

  <button
    type="button"
    class="download-btn"
    onclick={handleDownload}
    title={downloading ? 'Downloading...' : 'Download'}
    disabled={downloading}
    aria-label={downloading ? 'Downloading' : `Download ${artifact.filename || 'artifact'}`}
  >
    {#if downloading}
      <span class="spinner"></span>
    {:else}
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M8 12L3 7L4.4 5.55L7 8.15V1H9V8.15L11.6 5.55L13 7L8 12Z" fill="currentColor"/>
        <path d="M2 14V11H4V13H12V11H14V14C14 14.55 13.55 15 13 15H3C2.45 15 2 14.55 2 14Z" fill="currentColor"/>
      </svg>
    {/if}
  </button>

  {#if error}
    <span class="artifact-error" role="alert">{error}</span>
  {/if}
</div>

<style>
  .artifact-item {
    position: relative;
    display: flex;
    align-items: center;
    gap: var(--space-sm, 8px);
    padding: var(--space-sm, 8px) var(--space-md, 12px);
    border-radius: var(--radius-md, 8px);
    background: var(--color-bg, #f9f6f1);
    border: 1px solid var(--color-border, #e8e1d7);
    transition: background 0.15s ease;
  }

  .artifact-item:hover {
    background: var(--color-card-bg, #fcfaf7);
  }

  .artifact-info {
    display: flex;
    align-items: center;
    gap: var(--space-sm, 8px);
    flex: 1;
    min-width: 0;
  }

  .artifact-icon {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
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

  .artifact-icon[data-type="presentation"]::before {
    content: '📽️';
  }

  .artifact-details {
    display: flex;
    flex-direction: column;
    min-width: 0;
    gap: 2px;
  }

  .artifact-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--color-text, #1f1f1f);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .artifact-meta {
    display: flex;
    align-items: center;
    gap: var(--space-xs, 4px);
    font-size: 11px;
    color: var(--color-muted, #7a756d);
  }

  .download-btn {
    width: 32px;
    height: 32px;
    border: none;
    border-radius: var(--radius-sm, 6px);
    background: var(--color-primary, #31a6a0);
    color: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    transition: all 0.15s ease;
  }

  .download-btn:hover:not(:disabled) {
    background: var(--color-primary-hover, #2a918c);
    transform: translateY(-1px);
  }

  .download-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .spinner {
    width: 14px;
    height: 14px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: white;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .artifact-error {
    position: absolute;
    bottom: -20px;
    left: 0;
    right: 0;
    font-size: 11px;
    color: var(--color-error-accent, #b24c4c);
    text-align: center;
  }
</style>
