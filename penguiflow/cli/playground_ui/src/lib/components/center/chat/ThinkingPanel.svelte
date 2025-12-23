<script lang="ts">
  import { renderMarkdown } from '$lib/services';

  interface Props {
    observations: string;
    open: boolean;
    isStreaming: boolean;
    ontoggle?: (open: boolean) => void;
  }

  let { observations, open, isStreaming, ontoggle }: Props = $props();

  const handleToggle = (e: Event) => {
    const el = e.currentTarget as HTMLDetailsElement;
    ontoggle?.(el.open);
  };
</script>

<details
  class="thinking-panel"
  {open}
  ontoggle={handleToggle}
>
  <summary class="thinking-summary">{isStreaming ? 'Thinking...' : 'Thought'}</summary>
  <div class="thinking-body">
    <div class="markdown-content">{@html renderMarkdown(observations)}</div>
  </div>
</details>

<style>
  .thinking-panel {
    margin-bottom: 8px;
    background: var(--color-code-bg, #f8f6f2);
    border-radius: 10px;
    border: 1px solid var(--color-border, #e8e1d7);
  }

  .thinking-summary {
    padding: 8px 10px;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-muted, #6b665f);
    cursor: pointer;
    list-style: none;
  }

  .thinking-summary::-webkit-details-marker {
    display: none;
  }

  .thinking-summary::before {
    content: '>';
    display: inline-block;
    margin-right: 6px;
    transition: transform 0.15s;
  }

  details[open] .thinking-summary::before {
    transform: rotate(90deg);
  }

  .thinking-body {
    padding: 0 10px 10px 10px;
    font-size: 12px;
    color: var(--color-text-secondary, #3c3a36);
    max-height: 150px;
    overflow-y: auto;
  }
</style>
