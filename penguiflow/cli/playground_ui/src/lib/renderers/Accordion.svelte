<script lang="ts">
  import { onMount } from 'svelte';
  import ComponentRenderer from './ComponentRenderer.svelte';
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
      const [first] = Array.from(defaults);
      openIndex = first ?? null;
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
          <Markdown content={item.content} padded={false} />
        {/if}
      </div>
    </details>
  {/each}
</div>

<style>
  .accordion {
    padding: 1rem;
  }

  details {
    border: 1px solid var(--color-border, #f0ebe4);
    border-radius: var(--radius-xl, 16px);
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
    background: var(--color-card-bg, #fcfaf7);
    box-shadow: var(--shadow-subtle, 0 4px 12px rgba(0, 0, 0, 0.04));
  }

  summary {
    cursor: pointer;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }

  .panel {
    margin-top: 0.75rem;
  }
</style>
