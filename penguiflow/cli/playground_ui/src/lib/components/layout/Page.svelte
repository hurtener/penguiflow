<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    children?: Snippet;
  }

  let { children }: Props = $props();
</script>

<div class="page">
  {@render children?.()}
</div>

<style>
  .page {
    display: grid;
    grid-template-columns: var(--sidebar-left-width, 300px) 1fr var(--sidebar-right-width, 320px);
    gap: var(--page-gap, 16px);
    padding: var(--page-padding, 16px);
    height: 100vh;
    overflow: hidden;
  }

  @media (max-width: 1200px) {
    .page {
      grid-template-columns: 1fr;
      height: auto;
      min-height: 100vh;
      overflow-y: auto;
      overflow-x: hidden;
    }
  }
</style>
