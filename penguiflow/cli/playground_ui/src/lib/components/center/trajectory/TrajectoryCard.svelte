<script lang="ts">
  import { Card } from '$lib/components/layout';
  import { Pill, Empty } from '$lib/components/ui';
  import { sessionStore, timelineStore } from '$lib/stores';
  import Timeline from './Timeline.svelte';
</script>

<Card class="trajectory-card">
  <div class="trajectory-header">
    <div class="title-small">Execution Trajectory</div>
    {#if sessionStore.activeTraceId}
      <Pill variant="subtle" size="small">trace {sessionStore.activeTraceId.slice(0, 8)}</Pill>
    {/if}
  </div>
  {#if timelineStore.isEmpty}
    <Empty
      inline
      title="No trajectory yet"
      subtitle="Send a prompt to see steps."
    />
  {:else}
    {#key sessionStore.activeTraceId}
      <Timeline steps={timelineStore.steps} />
    {/key}
  {/if}
</Card>

<style>
  .trajectory-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }

  .title-small {
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
  }

  :global(.trajectory-card) {
    flex: 0 0 auto;
    max-height: 40%;
    overflow-y: auto;
  }
</style>
