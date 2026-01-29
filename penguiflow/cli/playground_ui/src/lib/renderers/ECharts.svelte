<script lang="ts">
  import { onMount } from 'svelte';
  import * as echarts from 'echarts';
  import { toError } from '$lib/utils/result';

  interface Props {
    option: echarts.EChartsOption;
    height?: string;
    width?: string;
    theme?: string;
    loading?: boolean;
  }

  let { option, height = '400px', width = '100%', theme = 'light', loading = false }: Props = $props();

  let container = $state<HTMLDivElement | null>(null);
  let chart = $state<echarts.ECharts | null>(null);
  let resizeObserver: ResizeObserver | null = null;
  let error = $state<Error | null>(null);

  onMount(() => {
    if (!container) return;
    try {
      chart = echarts.init(container, theme);
      chart.setOption(option);

      if (loading) {
        chart.showLoading();
      }

      resizeObserver = new ResizeObserver(() => {
        chart?.resize();
      });
      resizeObserver.observe(container);
    } catch (err) {
      error = toError(err, 'ECharts render failed.');
    }

    return () => {
      resizeObserver?.disconnect();
      chart?.dispose();
      chart = null;
    };
  });

  $effect(() => {
    if (chart && option && !error) {
      try {
        chart.setOption(option, true);
      } catch (err) {
        error = toError(err, 'ECharts update failed.');
      }
    }
  });

  $effect(() => {
    if (chart && !error) {
      loading ? chart.showLoading() : chart.hideLoading();
    }
  });
</script>

{#if error}
  <div class="renderer-error">{error.message}</div>
{:else}
  <div
    bind:this={container}
    class="echarts-container"
    style:height
    style:width
  ></div>
{/if}

<style>
  .echarts-container {
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
