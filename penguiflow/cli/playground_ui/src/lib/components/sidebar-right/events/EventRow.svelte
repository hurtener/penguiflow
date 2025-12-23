<script lang="ts">
  import type { PlannerEventPayload } from '$lib/types';
  import { Pill } from '$lib/components/ui';

  interface Props {
    event: PlannerEventPayload;
    alt?: boolean;
  }

  let { event, alt = false }: Props = $props();
</script>

<div class="event-row" class:alt>
  <Pill variant="ghost" size="small">{event.event ?? 'event'}</Pill>
  <div class="event-main">
    <div class="event-name">{event.node ?? 'planner'}</div>
    <div class="muted tiny">{event.thought ?? ''}</div>
  </div>
  {#if event.latency_ms}
    <Pill variant="subtle" size="small">{(event.latency_ms / 1000).toFixed(2)}s</Pill>
  {/if}
</div>

<style>
  .event-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px;
    border-radius: 8px;
  }

  .event-row.alt {
    background: var(--color-code-bg, #fbf8f3);
  }

  .event-main {
    flex: 1;
    min-width: 0;
  }

  .event-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }

  .muted {
    color: var(--color-muted, #7a756d);
  }

  .tiny {
    font-size: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
