<script lang="ts">
  import { onMount } from 'svelte';
  import * as echarts from 'echarts';

  interface Props {
    option: echarts.EChartsOption;
    height?: string;
    width?: string;
    theme?: string;
    loading?: boolean;
  }

  let { option, height = '400px', width = '100%', theme = 'light', loading = false }: Props = $props();

  let container: HTMLDivElement;
  let chart = $state<echarts.ECharts | null>(null);
  let resizeObserver: ResizeObserver | null = null;

  onMount(() => {
    chart = echarts.init(container, theme);
    chart.setOption(option);

    if (loading) {
      chart.showLoading();
    }

    resizeObserver = new ResizeObserver(() => {
      chart?.resize();
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver?.disconnect();
      chart?.dispose();
      chart = null;
    };
  });

  $effect(() => {
    if (chart && option) {
      chart.setOption(option, true);
    }
  });

  $effect(() => {
    if (chart) {
      loading ? chart.showLoading() : chart.hideLoading();
    }
  });
</script>

<div
  bind:this={container}
  class="echarts-container"
  style:height
  style:width
></div>

<style>
  .echarts-container {
    min-height: 200px;
  }
</style>
