import { describe, it, expect, beforeEach } from 'vitest';
import { timelineStore } from '$lib/stores/timeline.svelte';
import type { TrajectoryPayload } from '$lib/types';

describe('timelineStore', () => {
  beforeEach(() => {
    timelineStore.clear();
  });

  describe('initial state', () => {
    it('starts empty', () => {
      expect(timelineStore.isEmpty).toBe(true);
      expect(timelineStore.steps).toEqual([]);
    });

    it('has no artifacts', () => {
      expect(timelineStore.hasArtifacts).toBe(false);
      expect(timelineStore.artifactStreams).toEqual({});
    });
  });

  describe('setFromPayload', () => {
    it('parses trajectory steps', () => {
      const payload: TrajectoryPayload = {
        steps: [
          {
            action: { next_node: 'search', thought: 'Looking up info' },
            observation: 'Found results',
            latency_ms: 100
          },
          {
            action: { plan: [{ node: 'answer' }], thought: 'Responding' },
            observation: 'Done'
          }
        ]
      };

      timelineStore.setFromPayload(payload);

      expect(timelineStore.isEmpty).toBe(false);
      expect(timelineStore.steps).toHaveLength(2);

      expect(timelineStore.steps[0].name).toBe('search');
      expect(timelineStore.steps[0].thought).toBe('Looking up info');
      expect(timelineStore.steps[0].result).toBe('Found results');
      expect(timelineStore.steps[0].latencyMs).toBe(100);

      expect(timelineStore.steps[1].name).toBe('answer');
    });

    it('handles empty payload', () => {
      timelineStore.setFromPayload({} as TrajectoryPayload);
      expect(timelineStore.steps).toEqual([]);
    });

    it('sets error status for failed steps', () => {
      const payload: TrajectoryPayload = {
        steps: [
          {
            action: { next_node: 'tool' },
            error: 'Tool failed'
          }
        ]
      };

      timelineStore.setFromPayload(payload);

      expect(timelineStore.steps[0].status).toBe('error');
    });

    it('extracts reflection score from metadata', () => {
      const payload: TrajectoryPayload = {
        steps: [
          {
            action: { next_node: 'check' },
            metadata: { reflection: { score: 0.85 } }
          }
        ]
      };

      timelineStore.setFromPayload(payload);

      expect(timelineStore.steps[0].reflectionScore).toBe(0.85);
    });
  });

  describe('artifact streams', () => {
    it('adds artifact chunks', () => {
      timelineStore.addArtifactChunk('stream-1', { data: 'chunk1' });
      timelineStore.addArtifactChunk('stream-1', { data: 'chunk2' });

      expect(timelineStore.hasArtifacts).toBe(true);
      expect(timelineStore.artifactStreams['stream-1']).toHaveLength(2);
    });

    it('handles multiple streams', () => {
      timelineStore.addArtifactChunk('stream-a', { a: 1 });
      timelineStore.addArtifactChunk('stream-b', { b: 2 });

      expect(Object.keys(timelineStore.artifactStreams)).toHaveLength(2);
    });

    it('clears artifacts', () => {
      timelineStore.addArtifactChunk('stream-1', { data: 'test' });
      timelineStore.clearArtifacts();

      expect(timelineStore.hasArtifacts).toBe(false);
      expect(timelineStore.artifactStreams).toEqual({});
    });
  });

  describe('clear', () => {
    it('clears steps and artifacts', () => {
      const payload: TrajectoryPayload = {
        steps: [{ action: { next_node: 'test' } }]
      };

      timelineStore.setFromPayload(payload);
      timelineStore.addArtifactChunk('stream', { data: 'test' });

      timelineStore.clear();

      expect(timelineStore.isEmpty).toBe(true);
      expect(timelineStore.hasArtifacts).toBe(false);
    });
  });
});
