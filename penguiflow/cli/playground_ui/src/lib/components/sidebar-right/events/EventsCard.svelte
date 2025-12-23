<script lang="ts">
  import { Card } from '$lib/components/layout';
  import EventsHeader from './EventsHeader.svelte';
  import EventsBody from './EventsBody.svelte';
  import ArtifactStreams from '../artifacts/ArtifactStreams.svelte';
</script>

<Card class="events-card">
  <EventsHeader />
  <EventsBody />
  <ArtifactStreams />
</Card>

<style>
  :global(.events-card) {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
</style>
