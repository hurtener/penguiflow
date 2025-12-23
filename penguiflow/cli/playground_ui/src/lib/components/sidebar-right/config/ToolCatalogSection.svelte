<script lang="ts">
  import { agentStore } from '$lib/stores';
  import ToolRow from './ToolRow.svelte';
</script>

<div class="section">
  <div class="section-label">Tool Catalog</div>
  {#each agentStore.catalog as tool (tool.name)}
    <ToolRow {tool} />
  {/each}
</div>

<style>
  .section {
    margin-bottom: 12px;
  }

  .section-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: var(--color-muted, #7a756d);
    margin-bottom: 6px;
  }
</style>
