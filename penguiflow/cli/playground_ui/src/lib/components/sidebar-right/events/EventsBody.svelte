<script lang="ts">
  import { Empty } from '$lib/components/ui';
  import { eventsStore } from '$lib/stores';
  import EventRow from './EventRow.svelte';

  // Use displayEvents for filtered/aggregated view
  let displayEvents = $derived(eventsStore.displayEvents);
</script>

<div class="events-body">
  {#if displayEvents.length === 0}
    <Empty
      inline
      title="No events yet"
      subtitle="Events will appear during runs."
    />
  {:else}
    {#each displayEvents as evt, idx (evt.id)}
      <EventRow event={evt} alt={idx % 2 === 0} />
    {/each}
  {/if}
</div>

<style>
  .events-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }
</style>
