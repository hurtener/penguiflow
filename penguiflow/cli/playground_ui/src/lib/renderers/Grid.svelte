<script lang="ts">
  import ComponentRenderer from './ComponentRenderer.svelte';

  interface GridItem {
    component: string;
    props: Record<string, unknown>;
    colSpan?: number;
    rowSpan?: number;
    title?: string;
  }

  interface Props {
    columns?: number;
    gap?: string;
    items?: GridItem[];
    equalHeight?: boolean;
  }

  let {
    columns = 2,
    gap = '1rem',
    items = [],
    equalHeight = true
  }: Props = $props();
</script>

<div
  class="grid"
  style={`grid-template-columns: repeat(${columns}, minmax(0, 1fr)); gap: ${gap};`}
>
  {#each items as item}
    <div
      class={`grid-item ${equalHeight ? 'equal' : ''}`}
      style={`grid-column: span ${item.colSpan ?? 1}; grid-row: span ${item.rowSpan ?? 1};`}
    >
      {#if item.title}
        <div class="grid-title">{item.title}</div>
      {/if}
      <ComponentRenderer component={item.component} props={item.props} />
    </div>
  {/each}
</div>

<style>
  .grid {
    display: grid;
    width: 100%;
  }

  .grid-item {
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #f0ebe4);
    border-radius: var(--radius-2xl, 18px);
    padding: 1rem;
    box-shadow: var(--shadow-subtle, 0 4px 12px rgba(0, 0, 0, 0.04));
  }

  .grid-title {
    font-size: 0.875rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
    color: var(--color-text, #1f1f1f);
  }

  .grid-item.equal {
    display: flex;
    flex-direction: column;
  }
</style>
