<script lang="ts">
  import type { DisplayEvent } from '$lib/types';
  import { Pill } from '$lib/components/ui';

  interface Props {
    event: DisplayEvent;
    alt?: boolean;
  }

  let { event, alt = false }: Props = $props();

  // Icon and color based on event type
  const typeConfig = {
    step: { icon: '▶', variant: 'ghost' as const },
    tool_call: { icon: '🔧', variant: 'subtle' as const },
    artifact: { icon: '📎', variant: 'accent' as const },
    other: { icon: '•', variant: 'ghost' as const },
  };

  let config = $derived(typeConfig[event.type] ?? typeConfig.other);

  function formatDuration(ms: number | undefined): string {
    if (!ms) return '';
    if (ms < 1000) return `${ms.toFixed(0)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }
</script>

<div class="event-row" class:alt>
  <div class="event-icon">{config.icon}</div>
  <div class="event-main">
    <div class="event-header">
      <span class="event-name">{event.name}</span>
      {#if event.type === 'tool_call'}
        <Pill variant="subtle" size="small">tool</Pill>
      {:else if event.type === 'artifact'}
        <Pill variant="accent" size="small">artifact</Pill>
      {/if}
    </div>
    {#if event.description}
      <div class="event-desc">{event.description}</div>
    {/if}
    {#if event.type === 'tool_call' && event.args}
      <div class="event-detail">
        <span class="label">Args:</span> {event.args}
      </div>
    {/if}
    {#if event.type === 'tool_call' && event.result}
      <div class="event-detail">
        <span class="label">Result:</span> {event.result}
      </div>
    {/if}
  </div>
  {#if event.duration_ms}
    <Pill variant="subtle" size="small">{formatDuration(event.duration_ms)}</Pill>
  {/if}
</div>

<style>
  .event-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 8px;
    border-radius: 8px;
  }

  .event-row.alt {
    background: var(--color-code-bg, #fbf8f3);
  }

  .event-icon {
    font-size: 12px;
    width: 20px;
    text-align: center;
    flex-shrink: 0;
    padding-top: 2px;
  }

  .event-main {
    flex: 1;
    min-width: 0;
  }

  .event-header {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .event-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .event-desc {
    font-size: 10px;
    color: var(--color-muted, #7a756d);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 2px;
  }

  .event-detail {
    font-size: 10px;
    color: var(--color-muted, #7a756d);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 2px;
    font-family: var(--font-mono, monospace);
  }

  .event-detail .label {
    font-weight: 600;
    color: var(--color-text-secondary, #5a5652);
  }
</style>
