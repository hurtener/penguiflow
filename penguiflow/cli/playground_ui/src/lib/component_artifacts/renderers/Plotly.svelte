<script lang="ts">
  import { onMount } from 'svelte';
  import Plotly from 'plotly.js-dist-min';

  interface Props {
    data?: Plotly.Data[];
    layout?: Partial<Plotly.Layout>;
    config?: Partial<Plotly.Config>;
    height?: string;
  }

  let { data = [], layout = {}, config = {}, height = '400px' }: Props = $props();

  let container: HTMLDivElement;

  onMount(() => {
    Plotly.newPlot(container, data, layout, config);
    return () => {
      Plotly.purge(container);
    };
  });

  $effect(() => {
    if (container) {
      Plotly.react(container, data, layout, config);
    }
  });
</script>

<div bind:this={container} class="plotly-container" style:height></div>

<style>
  .plotly-container {
    width: 100%;
    min-height: 200px;
  }
</style>
