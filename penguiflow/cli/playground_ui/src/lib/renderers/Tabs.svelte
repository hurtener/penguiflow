<script lang="ts">
  import ComponentRenderer from './ComponentRenderer.svelte';
  import Markdown from './Markdown.svelte';

  interface TabItem {
    id?: string;
    label: string;
    icon?: string;
    component?: string;
    props?: Record<string, unknown>;
    content?: string;
    disabled?: boolean;
  }

  interface Props {
    tabs?: TabItem[];
    defaultTab?: number;
    variant?: 'line' | 'enclosed' | 'pills';
  }

  let { tabs = [], defaultTab = 0, variant = 'line' }: Props = $props();

  let active = $state(0);

  // Sync active tab when defaultTab prop changes
  $effect(() => {
    active = defaultTab;
  });

  const activeTab = $derived(tabs[active]);
</script>

<div class={`tabs ${variant}`}>
  <div class="tab-list">
    {#each tabs as tab, idx}
      <button
        class={`tab ${idx === active ? 'active' : ''}`}
        disabled={tab.disabled}
        onclick={() => active = idx}
      >
        {#if tab.icon}<span class="icon">{tab.icon}</span>{/if}
        {tab.label}
      </button>
    {/each}
  </div>

  <div class="tab-panel">
    {#if activeTab?.component}
      <ComponentRenderer component={activeTab.component} props={activeTab.props ?? {}} />
    {:else if activeTab?.content}
      <Markdown content={activeTab.content} />
    {:else}
      <div class="empty">No content.</div>
    {/if}
  </div>
</div>

<style>
  .tabs {
    padding: 1rem;
  }

  .tab-list {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .tab {
    padding: 0.4rem 0.75rem;
    border-radius: 0.5rem;
    border: 1px solid #e5e7eb;
    background: #ffffff;
    cursor: pointer;
    font-size: 0.8rem;
  }

  .tab.active {
    background: #2563eb;
    color: #ffffff;
    border-color: #2563eb;
  }

  .tab-panel {
    margin-top: 1rem;
  }

  .tabs.pills .tab {
    border-radius: 999px;
  }

  .tabs.enclosed {
    border: 1px solid #e5e7eb;
    border-radius: 0.75rem;
  }
</style>
