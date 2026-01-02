<script lang="ts">
  import { onMount } from 'svelte';
  import Plotly from 'plotly.js-dist-min';
  import { toError } from '$lib/utils/result';

  type PlotlyData = Record<string, unknown>;
  type PlotlyLayout = Record<string, unknown>;
  type PlotlyConfig = Record<string, unknown>;

  interface Props {
    data?: PlotlyData[];
    layout?: PlotlyLayout;
    config?: PlotlyConfig;
    height?: string;
  }

  let { data = [], layout = {}, config = {}, height = '400px' }: Props = $props();

  let container = $state<HTMLDivElement | null>(null);
  let error = $state<Error | null>(null);

  onMount(() => {
    if (!container) return;
    try {
      Plotly.newPlot(container, data, layout, config);
      error = null;
    } catch (err) {
      error = toError(err, 'Plotly render failed.');
    }
    return () => {
      if (container) {
        Plotly.purge(container);
      }
    };
  });

  $effect(() => {
    if (container && !error) {
      try {
        Plotly.react(container, data, layout, config);
      } catch (err) {
        error = toError(err, 'Plotly update failed.');
      }
    }
  });
</script>

{#if error}
  <div class="renderer-error">{error.message}</div>
{:else}
  <div bind:this={container} class="plotly-container" style:height></div>
{/if}

<style>
  .plotly-container {
    width: 100%;
    min-height: 200px;
  }

  .renderer-error {
    padding: 0.75rem 1rem;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 0.5rem;
    color: #dc2626;
  }
</style>
