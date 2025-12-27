import type { PlannerEventPayload, DisplayEvent } from '$lib/types';
import { randomId, MAX_EVENTS } from '$lib/utils';

/** Events to filter out from display (shown elsewhere in UI) */
const HIDDEN_EVENTS = new Set([
  'thinking',
  'revision',
  'tool_call_start',
  'tool_call_args',
  'tool_call_end',
  'tool_call_result',
  'llm_stream_chunk',
]);

/** Events that indicate step boundaries */
const STEP_EVENTS = new Set(['step', 'step_start', 'step_complete']);

interface ToolCallState {
  id: string;
  name: string;
  args: string;
  result: string;
  startTs: number;
  endTs?: number;
}

function createEventsStore() {
  let events = $state<PlannerEventPayload[]>([]);
  let filter = $state<Set<string>>(new Set());
  let paused = $state(false);

  // Track tool calls for aggregation
  let toolCalls = $state<Map<string, ToolCallState>>(new Map());

  /** Convert raw events to display events, filtering and aggregating */
  function getDisplayEvents(): DisplayEvent[] {
    const display: DisplayEvent[] = [];
    const seenToolCalls = new Set<string>();

    for (const evt of events) {
      const eventType = evt.event?.toLowerCase() ?? '';

      // Skip hidden events
      if (HIDDEN_EVENTS.has(eventType)) {
        continue;
      }

      // Handle step events
      if (STEP_EVENTS.has(eventType) || eventType === 'event') {
        // Only show step_complete or steps with latency (completed steps)
        if (eventType === 'step_complete' || evt.latency_ms) {
          display.push({
            id: evt.id,
            type: 'step',
            name: evt.node ?? 'step',
            description: evt.thought ? truncate(evt.thought, 100) : undefined,
            duration_ms: evt.latency_ms,
            ts: evt.ts ?? Date.now(),
          });
        }
        continue;
      }

      // Handle artifact events
      if (eventType === 'artifact_stored' || eventType === 'artifact_chunk') {
        display.push({
          id: evt.id,
          type: 'artifact',
          name: (evt as any).artifact_id ?? 'artifact',
          description: (evt as any).mime_type,
          ts: evt.ts ?? Date.now(),
        });
        continue;
      }

      // Other events - show as-is but filter duplicates
      display.push({
        id: evt.id,
        type: 'other',
        name: eventType,
        description: evt.thought ? truncate(evt.thought, 80) : undefined,
        ts: evt.ts ?? Date.now(),
      });
    }

    // Add completed tool calls from aggregation map
    for (const [tcId, tc] of toolCalls) {
      if (tc.endTs && !seenToolCalls.has(tcId)) {
        display.push({
          id: `tc_${tcId}`,
          type: 'tool_call',
          name: tc.name,
          args: tc.args ? truncate(tc.args, 100) : undefined,
          result: tc.result ? truncate(tc.result, 100) : undefined,
          duration_ms: tc.endTs - tc.startTs,
          ts: tc.startTs,
        });
        seenToolCalls.add(tcId);
      }
    }

    // Sort by timestamp descending (newest first)
    display.sort((a, b) => b.ts - a.ts);

    return display;
  }

  return {
    get events() { return events; },
    get displayEvents() { return getDisplayEvents(); },
    get filter() { return filter; },
    get paused() { return paused; },
    set paused(v: boolean) { paused = v; },

    get isEmpty() { return events.length === 0; },

    setFilter(eventType: string | null) {
      if (!eventType) {
        filter = new Set();
      } else {
        filter = new Set([eventType]);
      }
    },

    addEvent(data: Record<string, unknown>, eventType: string) {
      // Handle tool call aggregation
      const toolCallId = data.tool_call_id as string | undefined;
      const lowerType = eventType.toLowerCase();

      if (toolCallId) {
        if (lowerType === 'tool_call_start') {
          toolCalls.set(toolCallId, {
            id: toolCallId,
            name: (data.tool_call_name as string) ?? 'tool',
            args: '',
            result: '',
            startTs: Date.now(),
          });
        } else if (lowerType === 'tool_call_args') {
          const tc = toolCalls.get(toolCallId);
          if (tc) {
            tc.args += (data.delta as string) ?? '';
          }
        } else if (lowerType === 'tool_call_result') {
          const tc = toolCalls.get(toolCallId);
          if (tc) {
            tc.result = (data.content as string) ?? '';
          }
        } else if (lowerType === 'tool_call_end') {
          const tc = toolCalls.get(toolCallId);
          if (tc) {
            tc.endTs = Date.now();
          }
        }
      }

      // Check for duplicates
      const eventKey = `${data.event ?? ''}|${data.step ?? ''}|${data.ts ?? ''}`;
      const isDuplicate = events.some(
        e => `${e.event ?? ''}|${e.step ?? ''}|${e.ts ?? ''}` === eventKey
      );

      if (!isDuplicate) {
        events.unshift({ id: randomId(), ...data, event: eventType } as PlannerEventPayload);
        if (events.length > MAX_EVENTS) {
          events.length = MAX_EVENTS;
        }
      }
    },

    shouldProcess(eventType: string): boolean {
      if (paused) return false;
      if (filter.size === 0) return true;
      return filter.has(eventType);
    },

    clear() {
      events = [];
      toolCalls = new Map();
    }
  };
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 3) + '...';
}

export const eventsStore = createEventsStore();
