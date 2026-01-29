<script lang="ts">
  import { getAgentStore } from '$lib/stores';
  import ServiceRow from './ServiceRow.svelte';

  const agentStore = getAgentStore();
</script>

<div class="section">
  <div class="section-label">Services</div>
  {#each agentStore.services as svc (svc.name)}
    <ServiceRow service={svc} />
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
