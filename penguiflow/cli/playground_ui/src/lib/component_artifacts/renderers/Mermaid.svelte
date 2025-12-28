<script lang="ts">
  import { onDestroy } from 'svelte';
  import mermaid from 'mermaid';

  interface Props {
    code: string;
    theme?: string;
  }

  let { code, theme = 'default' }: Props = $props();

  let container: HTMLDivElement;
  let renderId = `mermaid-${Math.random().toString(36).slice(2)}`;
  let currentPromise: Promise<void> | null = null;

  const renderDiagram = async () => {
    if (!container || !code) return;
    mermaid.initialize({ startOnLoad: false, theme });
    const { svg } = await mermaid.render(renderId, code);
    container.innerHTML = svg;
  };

  $effect(() => {
    if (code && container) {
      const task = renderDiagram();
      currentPromise = task;
    }
  });

  onDestroy(() => {
    if (currentPromise) {
      currentPromise = null;
    }
  });
</script>

<div class="mermaid" bind:this={container}></div>

<style>
  .mermaid {
    padding: 1rem;
    overflow-x: auto;
  }
</style>
