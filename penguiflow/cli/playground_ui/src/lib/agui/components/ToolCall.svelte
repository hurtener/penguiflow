<script lang="ts">
  import type { StreamingToolCall } from '../stores';

  export let toolCall: StreamingToolCall;

  $: parsedArgs = (() => {
    try {
      return JSON.parse(toolCall.arguments);
    } catch {
      return null;
    }
  })();
</script>

<div class="agui-tool-call" class:streaming={toolCall.isStreaming}>
  <div class="header">
    <span class="icon">tool</span>
    <strong>{toolCall.name}</strong>
    {#if toolCall.isStreaming}<span class="dots">...</span>{/if}
  </div>

  <pre class="args">
{parsedArgs ? JSON.stringify(parsedArgs, null, 2) : toolCall.arguments}
  </pre>

  {#if toolCall.result}
    <div class="result">
      <div class="label">Result</div>
      <pre>{toolCall.result}</pre>
    </div>
  {/if}
</div>

<style>
  .agui-tool-call {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    padding: 8px;
    font-size: 12px;
  }

  .agui-tool-call.streaming {
    border-style: dashed;
  }

  .header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 6px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .icon {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--color-muted, #7a756d);
  }

  .dots {
    animation: pulse 1s infinite;
  }

  @keyframes pulse {
    50% {
      opacity: 0.4;
    }
  }

  pre {
    margin: 0;
    padding: 6px;
    border-radius: 6px;
    background: rgba(0, 0, 0, 0.04);
    overflow-x: auto;
    font-size: 11px;
  }

  .result {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px dashed var(--color-border, #e8e1d7);
  }

  .label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-muted, #7a756d);
    margin-bottom: 4px;
  }
</style>
