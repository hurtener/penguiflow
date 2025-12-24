<script lang="ts">
  import { Empty } from '$lib/components/ui';
  import { eventsStore } from '$lib/stores';
  import EventRow from './EventRow.svelte';
</script>

<div class="events-body">
  {#if eventsStore.isEmpty}
    <Empty
      inline
      title="No events yet"
      subtitle="Events will appear during runs."
    />
  {:else}
    {#each eventsStore.events as evt, idx (evt.id)}
      <EventRow event={evt} alt={idx % 2 === 0} />
    {/each}
  {/if}
</div>

<style>
  .events-body {
    max-height: 250px;
    overflow-y: auto;
  }
</style>
