<script lang="ts">
  import { Pill } from '$lib/components/ui';
  import { timelineStore } from '$lib/stores';
</script>

{#if timelineStore.hasArtifacts}
  <div class="section">
    <div class="title-small">Artifact Streams</div>
    {#each Object.entries(timelineStore.artifactStreams) as [streamId, chunks] (streamId)}
      <div class="artifact-row">
        <Pill variant="ghost" size="small">{streamId}</Pill>
        <pre>{JSON.stringify(chunks[chunks.length - 1], null, 2)}</pre>
      </div>
    {/each}
  </div>
{/if}

<style>
  .section {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--color-border, #e8e1d7);
  }

  .title-small {
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
    margin-bottom: 8px;
  }

  .artifact-row {
    margin-bottom: 8px;
  }

  pre {
    margin: 6px 0 0 0;
    font-size: 10px;
    font-family: var(--font-mono);
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    padding: 8px;
    max-height: 80px;
    overflow: auto;
  }
</style>
