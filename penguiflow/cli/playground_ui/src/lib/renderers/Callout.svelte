<script lang="ts">
  import Markdown from './Markdown.svelte';

  interface Props {
    content?: string;
    type?: 'info' | 'warning' | 'error' | 'success' | 'tip' | 'note';
    title?: string;
    collapsible?: boolean;
  }

  let { content = '', type = 'info', title = undefined, collapsible = false }: Props = $props();

  let open = $state(true);
</script>

<div class={`callout ${type}`}>
  {#if collapsible}
    <button class="callout-header" type="button" onclick={() => open = !open}>
      <span class="callout-title">{title || type.toUpperCase()}</span>
      <span class="callout-toggle">{open ? '−' : '+'}</span>
    </button>
  {:else}
    <div class="callout-header">
      <span class="callout-title">{title || type.toUpperCase()}</span>
    </div>
  {/if}
  {#if open}
    <div class="callout-body">
      <Markdown content={content} allowHtml={false} padded={false} />
    </div>
  {/if}
</div>

<style>
  .callout {
    border-radius: var(--radius-xl, 16px);
    padding: 0.75rem 1rem;
    border: 1px solid transparent;
    color: var(--color-text-secondary, #3c3a36);
  }

  .callout-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-weight: 600;
    cursor: default;
    background: transparent;
    border: none;
    width: 100%;
    text-align: left;
    padding: 0;
    font-size: inherit;
    color: inherit;
  }

  button.callout-header {
    cursor: pointer;
  }

  .callout-body {
    margin-top: 0.5rem;
  }

  .callout.info {
    background: var(--color-pill-subtle-bg, #eef5f3);
    border-color: var(--color-border, #f0ebe4);
  }

  .callout.warning {
    background: var(--color-pill-ghost-bg, #f4f0ea);
    border-color: var(--color-border, #f0ebe4);
  }

  .callout.error {
    background: var(--color-error-bg, #fdf3f3);
    border-color: var(--color-error-border, #f5dddd);
    color: var(--color-error-text, #9b2d2d);
  }

  .callout.success {
    background: var(--color-pill-subtle-bg, #eef5f3);
    border-color: var(--color-border, #f0ebe4);
  }

  .callout.tip {
    background: var(--color-tab-active-bg, #e8f6f2);
    border-color: var(--color-border, #f0ebe4);
  }

  .callout.note {
    background: var(--color-code-bg, #fbf8f3);
    border-color: var(--color-border, #f0ebe4);
  }
</style>
