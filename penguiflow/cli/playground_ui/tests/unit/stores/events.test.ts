import { describe, it, expect, beforeEach } from 'vitest';
import { createEventsStore } from '$lib/stores';

const eventsStore = createEventsStore();

describe('eventsStore', () => {
  beforeEach(() => {
    eventsStore.clear();
    eventsStore.paused = false;
    eventsStore.setFilter(null);
  });

  describe('initial state', () => {
    it('starts empty', () => {
      expect(eventsStore.isEmpty).toBe(true);
      expect(eventsStore.events).toEqual([]);
    });

    it('has no filter', () => {
      expect(eventsStore.filter.size).toBe(0);
    });

    it('is not paused', () => {
      expect(eventsStore.paused).toBe(false);
    });
  });

  describe('addEvent', () => {
    it('adds an event', () => {
      eventsStore.addEvent({ step: 1 }, 'STEP_START');

      expect(eventsStore.isEmpty).toBe(false);
      expect(eventsStore.events).toHaveLength(1);
      const first = eventsStore.events[0]!;
      expect(first.event).toBe('STEP_START');
    });

    it('prepends new events', () => {
      eventsStore.addEvent({ step: 1 }, 'STEP_START');
      eventsStore.addEvent({ step: 2 }, 'STEP_END');

      const [first, second] = eventsStore.events;
      expect(first?.event).toBe('STEP_END');
      expect(second?.event).toBe('STEP_START');
    });

    it('prevents duplicate events', () => {
      const eventData = { event: 'TEST', step: 1, ts: 12345 };

      eventsStore.addEvent(eventData, 'TEST');
      eventsStore.addEvent(eventData, 'TEST');

      expect(eventsStore.events).toHaveLength(1);
    });

    it('generates unique ids', () => {
      eventsStore.addEvent({ step: 1, ts: 1 }, 'STEP_START');
      eventsStore.addEvent({ step: 2, ts: 2 }, 'STEP_END');

      const ids = (eventsStore.events as Array<{ id: string }>).map((evt) => evt.id);
      expect(new Set(ids).size).toBe(ids.length);
    });

    it('enforces max event limit', () => {
      for (let i = 0; i < 250; i++) {
        eventsStore.addEvent({ step: i, ts: i }, 'TEST');
      }

      expect(eventsStore.events.length).toBeLessThanOrEqual(200);
    });
  });

  describe('filter', () => {
    it('sets filter to specific event type', () => {
      eventsStore.setFilter('STEP_START');
      expect(eventsStore.filter.has('STEP_START')).toBe(true);
      expect(eventsStore.filter.size).toBe(1);
    });

    it('clears filter when null', () => {
      eventsStore.setFilter('STEP_START');
      eventsStore.setFilter(null);
      expect(eventsStore.filter.size).toBe(0);
    });
  });

  describe('shouldProcess', () => {
    it('returns true when not paused and no filter', () => {
      expect(eventsStore.shouldProcess('ANY_EVENT')).toBe(true);
    });

    it('returns false when paused', () => {
      eventsStore.paused = true;
      expect(eventsStore.shouldProcess('ANY_EVENT')).toBe(false);
    });

    it('returns true for matching filter', () => {
      eventsStore.setFilter('STEP_START');
      expect(eventsStore.shouldProcess('STEP_START')).toBe(true);
    });

    it('returns false for non-matching filter', () => {
      eventsStore.setFilter('STEP_START');
      expect(eventsStore.shouldProcess('STEP_END')).toBe(false);
    });
  });

  describe('clear', () => {
    it('clears all events', () => {
      eventsStore.addEvent({ step: 1 }, 'STEP_START');
      eventsStore.addEvent({ step: 2 }, 'STEP_END');

      eventsStore.clear();

      expect(eventsStore.events).toEqual([]);
      expect(eventsStore.isEmpty).toBe(true);
    });
  });
});
