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
      <Markdown content={content} allowHtml={false} />
    </div>
  {/if}
</div>

<style>
  .callout {
    border-radius: 0.5rem;
    padding: 0.75rem 1rem;
    border: 1px solid transparent;
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
    background: #eff6ff;
    border-color: #bfdbfe;
  }

  .callout.warning {
    background: #fef9c3;
    border-color: #fde047;
  }

  .callout.error {
    background: #fee2e2;
    border-color: #fecaca;
  }

  .callout.success {
    background: #dcfce7;
    border-color: #86efac;
  }

  .callout.tip {
    background: #ecfccb;
    border-color: #bef264;
  }

  .callout.note {
    background: #f8fafc;
    border-color: #cbd5f5;
  }
</style>
