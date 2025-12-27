<script lang="ts">
  import { Card } from '$lib/components/layout';
  import { artifactsStore, sessionStore } from '$lib/stores';
  import { downloadArtifact } from '$lib/services/api';
  import ArtifactItem from './ArtifactItem.svelte';

  let downloadingAll = $state(false);

  async function handleDownloadAll() {
    downloadingAll = true;
    for (const artifact of artifactsStore.list) {
      try {
        await downloadArtifact(
          artifact.id,
          sessionStore.sessionId,
          artifact.filename ?? undefined
        );
        // Small delay between downloads to avoid browser blocking
        await new Promise(r => setTimeout(r, 300));
      } catch {
        // Continue with other downloads on error
      }
    }
    downloadingAll = false;
  }
</script>

<Card class="artifacts-card">
  <div class="artifacts-header">
    <h3 class="artifacts-title">
      Artifacts
      {#if artifactsStore.count > 0}
        <span class="count-badge">{artifactsStore.count}</span>
      {/if}
    </h3>
    {#if artifactsStore.count > 1}
      <button
        type="button"
        class="download-all-btn"
        onclick={handleDownloadAll}
        disabled={downloadingAll}
        aria-label={downloadingAll ? 'Downloading all artifacts' : 'Download all artifacts'}
      >
        {#if downloadingAll}
          <span class="spinner"></span>
          Downloading...
        {:else}
          Download All
        {/if}
      </button>
    {/if}
  </div>

  <div class="artifacts-body">
    {#if artifactsStore.count === 0}
      <p class="no-artifacts">
        No artifacts yet. Artifacts will appear here when tools generate downloadable files.
      </p>
    {:else}
      <div class="artifacts-list">
        {#each artifactsStore.list as artifact (artifact.id)}
          <ArtifactItem {artifact} />
        {/each}
      </div>
    {/if}
  </div>
</Card>

<style>
  :global(.artifacts-card) {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    max-height: 300px;
  }

  .artifacts-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-md, 12px);
    flex-shrink: 0;
  }

  .artifacts-title {
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

  .download-all-btn {
    display: flex;
    align-items: center;
    gap: var(--space-xs, 4px);
    padding: var(--space-xs, 4px) var(--space-sm, 8px);
    border-radius: var(--radius-sm, 6px);
    background: var(--color-tab-bg, #f0ebe4);
    color: var(--color-text, #1f1f1f);
    border: none;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.15s ease;
  }

  .download-all-btn:hover:not(:disabled) {
    background: var(--color-border, #e8e1d7);
  }

  .download-all-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(0, 0, 0, 0.2);
    border-top-color: var(--color-text, #1f1f1f);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  .artifacts-body {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .no-artifacts {
    margin: 0;
    padding: var(--space-lg, 16px);
    text-align: center;
    color: var(--color-muted, #7a756d);
    font-size: 13px;
    font-style: italic;
  }

  .artifacts-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-sm, 8px);
  }
</style>
