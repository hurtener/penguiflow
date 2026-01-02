<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    onclick?: () => void;
    title?: string;
    children?: Snippet;
  }

  let { onclick, title, children }: Props = $props();
</script>

<button class="icon-btn" {onclick} {title}>
  {@render children?.()}
</button>

<style>
  .icon-btn {
    background: var(--color-btn-ghost-bg, #f2eee8);
    padding: 6px;
    border-radius: 8px;
    font-size: 11px;
    border: 1px solid var(--color-border, #e8e1d7);
    cursor: pointer;
  }

  .icon-btn:hover {
    background: var(--color-btn-ghost-hover, #e8e4de);
  }
</style>
