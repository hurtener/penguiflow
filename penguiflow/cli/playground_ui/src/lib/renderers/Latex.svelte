<script lang="ts">
  import katex from 'katex';
  import 'katex/dist/katex.min.css';
  import { toError } from '$lib/utils/result';

  interface Props {
    expression?: string;
    displayMode?: boolean;
  }

  let { expression = '', displayMode = true }: Props = $props();

  // Return result object instead of mutating state inside derived
  const result = $derived.by(() => {
    try {
      const html = katex.renderToString(expression || '', {
        displayMode,
        throwOnError: false
      });
      return { ok: true as const, html };
    } catch (err) {
      return { ok: false as const, error: toError(err, 'LaTeX render failed.') };
    }
  });
</script>

{#if !result.ok}
  <div class="renderer-error">{result.error.message}</div>
{:else}
  <div class="latex">{@html result.html}</div>
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
