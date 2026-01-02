<script lang="ts">
  import katex from 'katex';
  import 'katex/dist/katex.min.css';
  import { toError } from '$lib/utils/result';

  interface Props {
    expression?: string;
    displayMode?: boolean;
  }

  let { expression = '', displayMode = true }: Props = $props();

  let error = $state<Error | null>(null);

  const html = $derived.by(() => {
    try {
      const rendered = katex.renderToString(expression || '', {
        displayMode,
        throwOnError: false
      });
      error = null;
      return rendered;
    } catch (err) {
      error = toError(err, 'LaTeX render failed.');
      return '';
    }
  });
</script>

{#if error}
  <div class="renderer-error">{error.message}</div>
{:else}
  <div class="latex">{@html html}</div>
{/if}

<style>
  .latex {
    padding: 0.75rem 1rem;
    overflow-x: auto;
  }

  .renderer-error {
    padding: 0.75rem 1rem;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 0.5rem;
    color: #dc2626;
  }
</style>
