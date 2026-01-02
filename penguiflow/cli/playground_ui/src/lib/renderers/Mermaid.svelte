<script lang="ts">
  import { onDestroy } from 'svelte';
  import mermaid from 'mermaid';
  import { toError } from '$lib/utils/result';

  type MermaidTheme = 'default' | 'dark' | 'forest' | 'neutral' | 'base' | 'null';

  interface Props {
    code: string;
    theme?: MermaidTheme;
  }

  let { code, theme = 'default' }: Props = $props();

  let container = $state<HTMLDivElement | null>(null);
  let renderId = `mermaid-${Math.random().toString(36).slice(2)}`;
  let currentPromise: Promise<void> | null = null;
  let error = $state<Error | null>(null);

  const renderDiagram = async () => {
    if (!container || !code) return;
    try {
      mermaid.initialize({
        startOnLoad: false,
        theme,
        flowchart: {
          useMaxWidth: false,
          htmlLabels: true,
          curve: 'basis',
        },
        securityLevel: 'loose',
      });
      const { svg } = await mermaid.render(renderId, code);
      container.innerHTML = svg;
      error = null;
    } catch (err) {
      error = toError(err, 'Mermaid render failed.');
    }
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

{#if error}
  <div class="renderer-error">{error.message}</div>
{:else}
  <div class="mermaid" bind:this={container}></div>
{/if}

<style>
  .mermaid {
    padding: 1rem;
    overflow-x: auto;
    min-width: 0;
  }

  /* Ensure SVG scales and text is visible */
  .mermaid :global(svg) {
    max-width: 100%;
    height: auto;
  }

  /* Ensure node text isn't clipped */
  .mermaid :global(.node rect),
  .mermaid :global(.node circle),
  .mermaid :global(.node ellipse),
  .mermaid :global(.node polygon),
  .mermaid :global(.node path) {
    stroke-width: 1px;
  }

  .mermaid :global(.nodeLabel) {
    white-space: nowrap;
  }

  .mermaid :global(.edgeLabel) {
    background-color: white;
    padding: 2px 4px;
  }

  .renderer-error {
    padding: 0.75rem 1rem;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 0.5rem;
    color: #dc2626;
  }
</style>
