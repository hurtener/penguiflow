import type { PlannerEventPayload } from '$lib/types';
import { randomId, MAX_EVENTS } from '$lib/utils';

function createEventsStore() {
  let events = $state<PlannerEventPayload[]>([]);
  let filter = $state<Set<string>>(new Set());
  let paused = $state(false);

  return {
    get events() { return events; },
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
    }
  };
}

export const eventsStore = createEventsStore();
