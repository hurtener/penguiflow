<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    trajectoryContent?: Snippet;
    eventsContent?: Snippet;
    configContent?: Snippet;
  }

  let { trajectoryContent, eventsContent, configContent }: Props = $props();

  let isOpen = $state(false);
  let activeTab = $state<'trajectory' | 'events' | 'config'>('trajectory');

  const tabs = [
    { id: 'trajectory', label: 'Steps' },
    { id: 'events', label: 'Events' },
    { id: 'config', label: 'Config' }
  ] as const;
</script>

<div class="bottom-panel" class:open={isOpen}>
  <button class="toggle-bar" onclick={() => isOpen = !isOpen}>
    <div class="toggle-handle"></div>
    <span class="toggle-label">{isOpen ? 'Hide Details' : 'Show Details'}</span>
  </button>

  {#if isOpen}
    <div class="panel-tabs">
      {#each tabs as tab (tab.id)}
        <button
          class="panel-tab"
          class:active={activeTab === tab.id}
          onclick={() => activeTab = tab.id}
        >
          {tab.label}
        </button>
      {/each}
    </div>
    <div class="panel-content">
      {#if activeTab === 'trajectory'}
        {@render trajectoryContent?.()}
      {:else if activeTab === 'events'}
        {@render eventsContent?.()}
      {:else}
        {@render configContent?.()}
      {/if}
    </div>
  {/if}
</div>

<style>
  .bottom-panel {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: #ffffff;
    border-top: 1px solid var(--color-border, #e8e1d7);
    box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.08);
    z-index: 90;
    transition: max-height 0.3s ease;
    max-height: 48px;
  }

  .bottom-panel.open {
    max-height: 50vh;
  }

  .toggle-bar {
    width: 100%;
    padding: 12px 16px;
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
  }

  .toggle-handle {
    width: 40px;
    height: 4px;
    background: var(--color-border, #e8e1d7);
    border-radius: 2px;
  }

  .toggle-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-muted, #6b665f);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .panel-tabs {
    display: flex;
    padding: 0 16px;
    gap: 8px;
    border-bottom: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-bg, #f5f1eb);
  }

  .panel-tab {
    flex: 1;
    padding: 10px 12px;
    border: none;
    border-radius: 8px 8px 0 0;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    background: transparent;
    color: var(--color-muted, #6b665f);
    transition: all 0.15s ease;
  }

  .panel-tab.active {
    background: #ffffff;
    color: var(--color-tab-active-text, #106c67);
  }

  .panel-content {
    padding: 16px;
    max-height: calc(50vh - 100px);
    overflow-y: auto;
  }
</style>
