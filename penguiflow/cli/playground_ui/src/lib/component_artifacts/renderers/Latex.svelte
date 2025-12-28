<script lang="ts">
  import katex from 'katex';
  import 'katex/dist/katex.min.css';

  interface Props {
    expression?: string;
    displayMode?: boolean;
  }

  let { expression = '', displayMode = true }: Props = $props();

  const html = $derived(katex.renderToString(expression || '', {
    displayMode,
    throwOnError: false
  }));
</script>

<div class="latex">{@html html}</div>

<style>
  .latex {
    padding: 0.75rem 1rem;
    overflow-x: auto;
  }
</style>
