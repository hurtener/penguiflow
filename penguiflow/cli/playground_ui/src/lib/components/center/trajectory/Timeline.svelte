<script lang="ts">
  import type { TimelineStep } from '$lib/types';
  import TimelineItem from './TimelineItem.svelte';

  interface Props {
    steps: TimelineStep[];
  }

  let { steps }: Props = $props();
</script>

<div class="timeline">
  {#each steps as step (step.id)}
    <TimelineItem {step} />
  {/each}
</div>

<style>
  .timeline {
    padding: 10px;
  }
</style>
