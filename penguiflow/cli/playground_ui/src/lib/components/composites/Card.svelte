<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    class?: string;
    children?: Snippet;
  }

  let { class: className = '', children }: Props = $props();
</script>

<div class="card {className}">
  {@render children?.()}
</div>

<style>
  .card {
    background: var(--color-card-bg, #fcfaf7);
    border-radius: var(--radius-lg, 18px);
    box-shadow: 0 12px 32px rgba(17, 17, 17, 0.06);
    border: 1px solid var(--color-border, #f0ebe4);
    padding: 14px;
  }
</style>
