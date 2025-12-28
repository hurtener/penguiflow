<script lang="ts">
  import { onMount } from 'svelte';
  import ComponentRenderer from '../ComponentRenderer.svelte';
  import Markdown from './Markdown.svelte';

  interface AccordionItem {
    title: string;
    content?: string;
    component?: string;
    props?: Record<string, unknown>;
    defaultOpen?: boolean;
  }

  interface Props {
    items?: AccordionItem[];
    allowMultiple?: boolean;
  }

  let { items = [], allowMultiple = false }: Props = $props();

  let openIndex = $state<number | null>(null);
  let openItems = $state<Set<number>>(new Set());

  onMount(() => {
    const defaults = new Set<number>();
    items.forEach((item, idx) => {
      if (item.defaultOpen) {
        defaults.add(idx);
      }
    });
    if (allowMultiple) {
      openItems = defaults;
    } else {
      openIndex = defaults.size ? Array.from(defaults)[0] : null;
    }
  });

  function toggle(idx: number) {
    if (allowMultiple) {
      const next = new Set(openItems);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      openItems = next;
      return;
    }
    openIndex = openIndex === idx ? null : idx;
  }

  function isOpen(idx: number): boolean {
    return allowMultiple ? openItems.has(idx) : openIndex === idx;
  }
</script>

<div class="accordion">
  {#each items as item, idx}
    <details open={isOpen(idx)}>
      <summary onclick={(e: MouseEvent) => { e.preventDefault(); toggle(idx); }}>{item.title}</summary>
      <div class="panel">
        {#if item.component}
          <ComponentRenderer component={item.component} props={item.props ?? {}} />
        {:else if item.content}
          <Markdown content={item.content} />
        {/if}
      </div>
    </details>
  {/each}
</div>

<style>
  .accordion {
    padding: 0.75rem 1rem;
  }

  details {
    border: 1px solid #e5e7eb;
    border-radius: 0.5rem;
    padding: 0.5rem;
    margin-bottom: 0.5rem;
    background: #ffffff;
  }

  summary {
    cursor: pointer;
    font-weight: 600;
  }

  .panel {
    margin-top: 0.75rem;
  }
</style>
