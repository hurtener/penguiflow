<script lang="ts">
  interface Props {
    label?: string;
    content: unknown;
    maxHeight?: string;
  }

  let { label, content, maxHeight = '100px' }: Props = $props();

  const formatted = $derived(
    typeof content === 'string' ? content : JSON.stringify(content, null, 2)
  );
</script>

<div class="code-block">
  {#if label}
    <div class="label">{label}</div>
  {/if}
  <pre style="max-height: {maxHeight}">{formatted}</pre>
</div>

<style>
  .code-block {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-code-border, #eee5d9);
    border-radius: 8px;
    padding: 6px;
    margin-top: 4px;
  }

  .label {
    text-transform: uppercase;
    letter-spacing: 0.4px;
    font-size: 9px;
    color: var(--color-muted, #8b857c);
    margin-bottom: 3px;
  }

  pre {
    margin: 0;
    font-size: 10px;
    overflow: auto;
    font-family: var(--font-mono);
  }
</style>
