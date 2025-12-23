<script lang="ts">
  import type { Snippet } from 'svelte';
  import { agentStore } from '$lib/stores';

  interface Props {
    infoContent?: Snippet;
    specContent?: Snippet;
    actionsContent?: Snippet;
  }

  let { infoContent, specContent, actionsContent }: Props = $props();

  let isOpen = $state(false);
  let activeTab = $state<'info' | 'spec' | 'actions'>('info');

  const tabs = [
    { id: 'info', label: 'Info' },
    { id: 'spec', label: 'Spec' },
    { id: 'actions', label: 'Actions' }
  ] as const;

  const closeDrawer = () => { isOpen = false; };
</script>

<header class="mobile-header">
  <div class="header-bar">
    <button class="menu-btn" onclick={() => isOpen = !isOpen} aria-label="Toggle menu">
      <span class="hamburger" class:open={isOpen}>
        <span></span>
        <span></span>
        <span></span>
      </span>
    </button>
    <h1 class="agent-name">{agentStore.meta.name}</h1>
    <div class="header-spacer"></div>
  </div>

  {#if isOpen}
    <div class="drawer">
      <div class="drawer-tabs">
        {#each tabs as tab (tab.id)}
          <button
            class="drawer-tab"
            class:active={activeTab === tab.id}
            onclick={() => activeTab = tab.id}
          >
            {tab.label}
          </button>
        {/each}
      </div>
      <div class="drawer-content">
        {#if activeTab === 'info'}
          {@render infoContent?.()}
        {:else if activeTab === 'spec'}
          {@render specContent?.()}
        {:else}
          {@render actionsContent?.()}
        {/if}
      </div>
    </div>
  {/if}
</header>

{#if isOpen}
  <button class="backdrop" onclick={closeDrawer} aria-label="Close menu"></button>
{/if}

<style>
  .mobile-header {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 100;
    background: #ffffff;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  }

  .header-bar {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    gap: 12px;
  }

  .menu-btn {
    width: 40px;
    height: 40px;
    border: none;
    background: var(--color-tab-bg, #f2eee8);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
  }

  .hamburger {
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 18px;
  }

  .hamburger span {
    display: block;
    height: 2px;
    background: var(--color-text, #1f1f1f);
    border-radius: 1px;
    transition: all 0.2s ease;
  }

  .hamburger.open span:nth-child(1) {
    transform: rotate(45deg) translate(4px, 4px);
  }

  .hamburger.open span:nth-child(2) {
    opacity: 0;
  }

  .hamburger.open span:nth-child(3) {
    transform: rotate(-45deg) translate(4px, -4px);
  }

  .agent-name {
    font-size: 16px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .header-spacer {
    width: 40px;
  }

  .drawer {
    background: #ffffff;
    border-top: 1px solid var(--color-border, #e8e1d7);
    max-height: calc(100vh - 64px);
    overflow-y: auto;
  }

  .drawer-tabs {
    display: flex;
    padding: 8px 16px;
    gap: 8px;
    border-bottom: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-bg, #f5f1eb);
  }

  .drawer-tab {
    flex: 1;
    padding: 8px 12px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    background: transparent;
    color: var(--color-muted, #6b665f);
    transition: all 0.15s ease;
  }

  .drawer-tab.active {
    background: var(--color-tab-active-bg, #e8f6f2);
    color: var(--color-tab-active-text, #106c67);
  }

  .drawer-content {
    padding: 16px;
  }

  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.3);
    z-index: 99;
    border: none;
    cursor: pointer;
  }
</style>
