<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    label: string;
    hint?: string;
    full?: boolean;
    children?: Snippet;
  }

  let { label, hint, full = false, children }: Props = $props();
</script>

<div class="setup-field" class:full>
  <div class="label">{label}</div>
  {@render children?.()}
  {#if hint}
    <div class="muted tiny">{hint}</div>
  {/if}
</div>

<style>
  .setup-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .setup-field.full {
    grid-column: 1 / -1;
  }

  .label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-secondary, #3c3a36);
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }

  .muted {
    color: var(--color-muted, #7a756d);
  }

  .tiny {
    font-size: 10px;
  }
</style>
