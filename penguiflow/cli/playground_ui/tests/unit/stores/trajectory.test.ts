import { describe, it, expect } from 'vitest';
import { createTrajectoryStore } from '$lib/stores';

describe('trajectoryStore', () => {
  it('keeps the run outcome from the payload', () => {
    // setFromPayload picks fields explicitly, so anything not named here is
    // dropped at the boundary even though the backend already sends it.
    const store = createTrajectoryStore();
    store.setFromPayload({
      query: 'what is policy',
      steps: [],
      finish_reason: 'budget_exhausted',
      final_answer: 'partial answer'
    });

    expect(store.finishReason).toBe('budget_exhausted');
    expect(store.finalAnswer).toBe('partial answer');
  });

  it('defaults the run outcome to null when absent', () => {
    const store = createTrajectoryStore();
    store.setFromPayload({ query: 'q', steps: [] });

    expect(store.finishReason).toBeNull();
    expect(store.finalAnswer).toBeNull();
  });

  it('resets the run outcome on clear', () => {
    const store = createTrajectoryStore();
    store.setFromPayload({
      query: 'q',
      steps: [],
      finish_reason: 'answer_complete',
      final_answer: 'done'
    });
    store.clear();

    expect(store.finishReason).toBeNull();
    expect(store.finalAnswer).toBeNull();
  });
});
