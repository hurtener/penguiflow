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
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 0.75rem;
    padding: 0.75rem;
  }

  .grid-title {
    font-size: 0.875rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
  }

  .grid-item.equal {
    display: flex;
    flex-direction: column;
  }
</style>
