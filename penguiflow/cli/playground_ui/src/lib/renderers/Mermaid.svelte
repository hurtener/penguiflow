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
      // Use htmlLabels: false to force native SVG <text> elements instead of
      // foreignObject/HTML divs. This prevents text truncation caused by the
      // hardcoded max-width: 200px constraint in foreignObject child divs.
      // See: https://github.com/mermaid-js/mermaid/issues/4918
      mermaid.initialize({
        startOnLoad: false,
        theme,
        securityLevel: 'loose',
        flowchart: {
          htmlLabels: false,
        },
        sequence: {
          useMaxWidth: true,
        },
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

  .mermaid :global(svg) {
    max-width: 100%;
    height: auto;
  }

  /*
   * Fix text truncation in Mermaid diagrams.
   * Mermaid v9.2+ uses foreignObject with HTML divs that have a hardcoded
   * max-width: 200px constraint, causing text to be clipped.
   * See: https://github.com/mermaid-js/mermaid/issues/4918
   */
  .mermaid :global(foreignObject > div) {
    max-width: none !important;
    overflow: visible !important;
  }

  /* Ensure text in labels doesn't wrap unexpectedly */
  .mermaid :global(.nodeLabel),
  .mermaid :global(.edgeLabel),
  .mermaid :global(.label) {
    white-space: nowrap;
    overflow: visible;
  }

  /* ER diagram entity boxes */
  .mermaid :global(.er.entityBox) {
    overflow: visible;
  }

  .renderer-error {
    padding: 0.75rem 1rem;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 0.5rem;
    color: #dc2626;
  }
</style>
