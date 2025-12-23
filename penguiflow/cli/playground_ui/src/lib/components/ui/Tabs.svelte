<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Tab {
    id: string;
    label: string;
  }

  interface Props {
    tabs: Tab[];
    active: string;
    onchange?: (id: string) => void;
    right?: Snippet;
  }

  let { tabs, active, onchange, right }: Props = $props();
</script>

<div class="tabs">
  {#each tabs as tab (tab.id)}
    <button
      type="button"
      class="tab"
      class:active={active === tab.id}
      onclick={() => onchange?.(tab.id)}
    >
      {tab.label}
    </button>
  {/each}
  {@render right?.()}
</div>

<style>
  .tabs {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 10px;
  }

  .tab {
    padding: 6px 12px;
    border-radius: 10px;
    background: var(--color-tab-bg, #f2eee8);
    font-weight: 600;
    font-size: 12px;
    color: var(--color-tab-text, #5a534a);
    cursor: pointer;
    border: none;
  }

  .tab.active {
    background: var(--color-tab-active-bg, #e8f6f2);
    color: var(--color-tab-active-text, #106c67);
  }
</style>
