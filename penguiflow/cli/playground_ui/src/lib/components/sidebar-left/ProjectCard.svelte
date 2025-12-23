<script lang="ts">
  import { Card } from '$lib/components/layout';
  import { Pill } from '$lib/components/ui';
  import { agentStore } from '$lib/stores';
</script>

<Card class="project-card">
  <div class="row space-between align-center">
    <div>
      <div class="title">{agentStore.meta.name}</div>
      <div class="muted">{agentStore.meta.description}</div>
    </div>
    <Pill variant="subtle">{agentStore.meta.version || agentStore.meta.template}</Pill>
  </div>
  <div class="badges">
    {#each agentStore.meta.flags as flag, idx (idx)}
      <Pill variant="ghost">{flag}</Pill>
    {/each}
  </div>
  <div class="stats-row">
    <div class="stat">
      <div class="stat-label">TOOLS</div>
      <div class="stat-value">{agentStore.meta.tools}</div>
    </div>
    <div class="stat">
      <div class="stat-label">FLOWS</div>
      <div class="stat-value">{agentStore.meta.flows}</div>
    </div>
    <div class="stat">
      <div class="stat-label">SVCS</div>
      <div class="stat-value">{agentStore.services.length}</div>
    </div>
  </div>
</Card>

<style>
  .row {
    display: flex;
  }

  .space-between {
    justify-content: space-between;
  }

  .align-center {
    align-items: center;
  }

  .title {
    font-size: 18px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
    margin-bottom: 2px;
  }

  .muted {
    font-size: 12px;
    color: var(--color-muted, #6b665f);
  }

  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }

  .stats-row {
    display: flex;
    gap: 16px;
    margin-top: 14px;
    padding-top: 10px;
    border-top: 1px solid var(--color-border, #f0ebe4);
  }

  .stat {
    flex: 1;
    text-align: center;
  }

  .stat-label {
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-size: 9px;
    color: var(--color-muted, #7a756d);
    margin-bottom: 3px;
  }

  .stat-value {
    font-weight: 700;
    font-size: 18px;
    color: var(--color-primary, #106c67);
  }
</style>
