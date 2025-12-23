<script lang="ts">
  import { IconButton } from '$lib/components/ui';
  import { eventsStore } from '$lib/stores';
</script>

<div class="events-header">
  <div class="title-small">Planner Events</div>
  <div class="icon-row">
    <IconButton onclick={() => { eventsStore.paused = !eventsStore.paused; }}>
      {eventsStore.paused ? '>' : '||'}
    </IconButton>
    <select
      class="filter"
      onchange={(e) => {
        const value = e.currentTarget.value;
        eventsStore.setFilter(value || null);
      }}
    >
      <option value="">All</option>
      <option value="step">step</option>
      <option value="event">event</option>
      <option value="chunk">chunk</option>
      <option value="artifact_chunk">artifact_chunk</option>
    </select>
  </div>
</div>

<style>
  .events-header {
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

  .icon-row {
    display: flex;
    gap: 6px;
  }

  .filter {
    padding: 4px 8px;
    border-radius: 8px;
    border: 1px solid var(--color-border, #e8e1d7);
    font-size: 10px;
    background: var(--color-code-bg, #fbf8f3);
    cursor: pointer;
  }
</style>
