import { describe, it, expect, beforeEach } from 'vitest';
import { createTrajectoryStore } from '$lib/stores';
import type { TrajectoryPayload } from '$lib/types';

const trajectoryStore = createTrajectoryStore();

describe('trajectoryStore', () => {
  beforeEach(() => {
    trajectoryStore.clear();
  });

  describe('initial state', () => {
    it('starts empty', () => {
      expect(trajectoryStore.isEmpty).toBe(true);
      expect(trajectoryStore.steps).toEqual([]);
    });

    it('has no artifacts', () => {
      expect(trajectoryStore.hasArtifacts).toBe(false);
      expect(trajectoryStore.artifactStreams).toEqual({});
    });
  });

  describe('setFromPayload', () => {
    it('parses trajectory steps', () => {
      const payload: TrajectoryPayload = {
        steps: [
          {
            action: { next_node: 'search', thought: 'Looking up info' },
            observation: { text: 'Found results' },
            latency_ms: 100
          },
          {
            action: { plan: [{ node: 'answer' }], thought: 'Responding' },
            observation: { text: 'Done' }
          }
        ]
      };

      trajectoryStore.setFromPayload(payload);

      expect(trajectoryStore.isEmpty).toBe(false);
      expect(trajectoryStore.steps).toHaveLength(2);

      const [first, second] = trajectoryStore.steps;
      expect(first?.name).toBe('search');
      expect(first?.thought).toBe('Looking up info');
      expect(first?.result).toEqual({ text: 'Found results' });
      expect(first?.latencyMs).toBe(100);

      expect(second?.name).toBe('answer');
    });

    it('handles empty payload', () => {
      trajectoryStore.setFromPayload({} as TrajectoryPayload);
      expect(trajectoryStore.steps).toEqual([]);
    });

    it('sets error status for failed steps', () => {
      const payload: TrajectoryPayload = {
        steps: [
          {
            action: { next_node: 'tool' },
            error: true
          }
        ]
      };

      trajectoryStore.setFromPayload(payload);

      const [first] = trajectoryStore.steps;
      expect(first?.status).toBe('error');
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

      trajectoryStore.setFromPayload(payload);

      const [first] = trajectoryStore.steps;
      expect(first?.reflectionScore).toBe(0.85);
    });
  });

  describe('artifact streams', () => {
    it('adds artifact chunks', () => {
      trajectoryStore.addArtifactChunk('stream-1', { data: 'chunk1' });
      trajectoryStore.addArtifactChunk('stream-1', { data: 'chunk2' });

      expect(trajectoryStore.hasArtifacts).toBe(true);
      expect(trajectoryStore.artifactStreams['stream-1']).toHaveLength(2);
    });

    it('handles multiple streams', () => {
      trajectoryStore.addArtifactChunk('stream-a', { a: 1 });
      trajectoryStore.addArtifactChunk('stream-b', { b: 2 });

      expect(Object.keys(trajectoryStore.artifactStreams)).toHaveLength(2);
    });

    it('clears artifacts', () => {
      trajectoryStore.addArtifactChunk('stream-1', { data: 'test' });
      trajectoryStore.clearArtifacts();

      expect(trajectoryStore.hasArtifacts).toBe(false);
      expect(trajectoryStore.artifactStreams).toEqual({});
    });
  });

  describe('clear', () => {
    it('clears steps and artifacts', () => {
      const payload: TrajectoryPayload = {
        steps: [{ action: { next_node: 'test' } }]
      };

      trajectoryStore.setFromPayload(payload);
      trajectoryStore.addArtifactChunk('stream', { data: 'test' });

      trajectoryStore.clear();

      expect(trajectoryStore.isEmpty).toBe(true);
      expect(trajectoryStore.hasArtifacts).toBe(false);
    });
  });
});
