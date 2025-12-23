<script lang="ts">
  import { agentStore } from '$lib/stores';
</script>

<div class="section">
  <div class="section-label">Planner Config</div>
  <div class="tile-grid">
    {#each agentStore.plannerConfig as item (item.label)}
      <div class="tile">
        <div class="tile-label">{item.label}</div>
        <div class="tile-value">{item.value}</div>
      </div>
    {/each}
  </div>
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

  .tile-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 6px;
  }

  .tile {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    padding: 8px;
  }

  .tile-label {
    font-size: 9px;
    color: var(--color-muted, #7a756d);
    margin-bottom: 2px;
  }

  .tile-value {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }
</style>
