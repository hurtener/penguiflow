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
  <button
    type="button"
    class="toggle-bar"
    onclick={() => isOpen = !isOpen}
    aria-label={isOpen ? 'Hide details panel' : 'Show details panel'}
    aria-expanded={isOpen}
  >
    <div class="toggle-handle"></div>
    <span class="toggle-label">{isOpen ? 'Hide Details' : 'Show Details'}</span>
  </button>

  {#if isOpen}
    <div class="panel-tabs">
      {#each tabs as tab (tab.id)}
        <button
          type="button"
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
    flex-shrink: 0;
    background: var(--color-card-bg);
    border-top: 1px solid var(--color-border);
    box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.08);
    display: flex;
    flex-direction: column;
    max-height: var(--mobile-panel-collapsed);
    transition: max-height 0.3s ease;
  }

  .bottom-panel.open {
    max-height: var(--mobile-panel-expanded);
    flex: 0 0 auto;
  }

  .toggle-bar {
    width: 100%;
    padding: var(--space-lg) var(--space-xl);
    border: none;
    background: transparent;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-sm);
    flex-shrink: 0;
  }

  .toggle-handle {
    width: 40px;
    height: 4px;
    background: var(--color-border);
    border-radius: 2px;
  }

  .toggle-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .panel-tabs {
    display: flex;
    padding: 0 var(--space-xl);
    gap: var(--space-md);
    border-bottom: 1px solid var(--color-border);
    background: var(--color-bg);
    flex-shrink: 0;
  }

  .panel-tab {
    flex: 1;
    padding: var(--space-md) var(--space-lg);
    border: none;
    border-radius: var(--radius-sm) var(--radius-sm) 0 0;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    background: transparent;
    color: var(--color-muted);
    transition: all 0.15s ease;
  }

  .panel-tab.active {
    background: var(--color-card-bg);
    color: var(--color-tab-active-text);
  }

  .panel-content {
    flex: 1;
    padding: var(--space-xl);
    overflow-y: auto;
    min-height: 0;
  }

  /* Override card styles within mobile panel for consistent sizing */
  .panel-content :global(.card) {
    max-height: none;
    height: auto;
    overflow: visible;
  }

  .panel-content :global(.trajectory-card),
  .panel-content :global(.events-card),
  .panel-content :global(.config-card) {
    flex: none;
    max-height: none;
    min-height: auto;
    overflow: visible;
  }
</style>
