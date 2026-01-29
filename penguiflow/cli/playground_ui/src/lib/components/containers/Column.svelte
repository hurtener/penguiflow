<script lang="ts">
  import type { Snippet } from 'svelte';

  type Position = 'left' | 'center' | 'right';

  interface Props {
    position?: Position;
    class?: string;
    children?: Snippet;
  }

  let { position = 'center', class: className = '', children }: Props = $props();
</script>

<div class="column {position} {className}">
  {@render children?.()}
</div>

<style>
  .column {
    display: flex;
    flex-direction: column;
    gap: var(--space-lg, 12px);
    height: calc(100vh - 32px);
    overflow: hidden;
  }

  @media (max-width: 1200px) {
    .column {
      height: auto;
      min-height: 400px;
      overflow: visible;
    }
  }
</style>
