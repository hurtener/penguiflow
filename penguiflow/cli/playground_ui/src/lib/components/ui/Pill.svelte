<script lang="ts">
  import type { Snippet } from 'svelte';

  type Variant = 'default' | 'subtle' | 'ghost';
  type Size = 'default' | 'small';

  interface Props {
    variant?: Variant;
    size?: Size;
    class?: string;
    children?: Snippet;
  }

  let { variant = 'default', size = 'default', class: className = '', children }: Props = $props();
</script>

<span class="pill {variant} {size} {className}">
  {@render children?.()}
</span>

<style>
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 10px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 11px;
  }

  .subtle {
    background: var(--color-pill-subtle-bg, #eef5f3);
    color: var(--color-pill-subtle-text, #1f6c68);
  }

  .ghost {
    background: var(--color-pill-ghost-bg, #f4f0ea);
    color: var(--color-pill-ghost-text, #514c45);
    border: 1px solid var(--color-pill-ghost-border, #ebe5dd);
  }

  .small {
    padding: 3px 7px;
    font-size: 10px;
  }
</style>
