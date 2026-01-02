import { describe, it, expect, beforeEach } from 'vitest';
import { createArtifactsStore } from '$lib/stores';
import type { ArtifactStoredEvent } from '$lib/types';

const artifactsStore = createArtifactsStore();

describe('artifactsStore', () => {
  beforeEach(() => {
    artifactsStore.clear();
  });

  const createMockEvent = (overrides: Partial<ArtifactStoredEvent> = {}): ArtifactStoredEvent => ({
    artifact_id: 'artifact-123',
    mime_type: 'application/pdf',
    size_bytes: 1024,
    filename: 'report.pdf',
    source: { tool: 'pdf_generator' },
    trace_id: 'trace-456',
    session_id: 'session-789',
    ts: Date.now(),
    ...overrides
  });

  describe('initial state', () => {
    it('has empty artifacts', () => {
      expect(artifactsStore.count).toBe(0);
      expect(artifactsStore.list).toEqual([]);
    });

    it('artifacts map is empty', () => {
      expect(artifactsStore.artifacts.size).toBe(0);
    });
  });

  describe('addArtifact', () => {
    it('adds artifact from event', () => {
      const event = createMockEvent();
      artifactsStore.addArtifact(event);

      expect(artifactsStore.count).toBe(1);
      expect(artifactsStore.has('artifact-123')).toBe(true);
    });

    it('stores correct artifact properties', () => {
      const event = createMockEvent({
        artifact_id: 'test-artifact',
        mime_type: 'image/png',
        size_bytes: 2048,
        filename: 'screenshot.png',
        source: { tool: 'screenshot_tool', view_id: 'view-1' }
      });

      artifactsStore.addArtifact(event);
      const artifact = artifactsStore.get('test-artifact');

      expect(artifact).toBeDefined();
      expect(artifact?.id).toBe('test-artifact');
      expect(artifact?.mime_type).toBe('image/png');
      expect(artifact?.size_bytes).toBe(2048);
      expect(artifact?.filename).toBe('screenshot.png');
      expect(artifact?.sha256).toBeNull();
      expect(artifact?.source).toEqual({ tool: 'screenshot_tool', view_id: 'view-1' });
    });

    it('updates existing artifact with same ID', () => {
      const event1 = createMockEvent({
        artifact_id: 'same-id',
        filename: 'old.pdf'
      });
      const event2 = createMockEvent({
        artifact_id: 'same-id',
        filename: 'new.pdf'
      });

      artifactsStore.addArtifact(event1);
      artifactsStore.addArtifact(event2);

      expect(artifactsStore.count).toBe(1);
      expect(artifactsStore.get('same-id')?.filename).toBe('new.pdf');
    });

    it('can add multiple distinct artifacts', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'artifact-1' }));
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'artifact-2' }));
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'artifact-3' }));

      expect(artifactsStore.count).toBe(3);
      expect(artifactsStore.has('artifact-1')).toBe(true);
      expect(artifactsStore.has('artifact-2')).toBe(true);
      expect(artifactsStore.has('artifact-3')).toBe(true);
    });
  });

  describe('list', () => {
    it('returns artifacts as array', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a1', filename: 'file1.pdf' }));
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a2', filename: 'file2.pdf' }));

      const list = artifactsStore.list as Array<{ id: string }>;
      expect(Array.isArray(list)).toBe(true);
      expect(list.length).toBe(2);
      expect(list.map((artifact) => artifact.id)).toContain('a1');
      expect(list.map((artifact) => artifact.id)).toContain('a2');
    });

    it('returns empty array when no artifacts', () => {
      expect(artifactsStore.list).toEqual([]);
    });
  });

  describe('has', () => {
    it('returns true for existing artifact', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'exists' }));
      expect(artifactsStore.has('exists')).toBe(true);
    });

    it('returns false for non-existing artifact', () => {
      expect(artifactsStore.has('not-exists')).toBe(false);
    });
  });

  describe('get', () => {
    it('returns artifact for existing ID', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'get-test', filename: 'test.pdf' }));
      const artifact = artifactsStore.get('get-test');

      expect(artifact).toBeDefined();
      expect(artifact?.filename).toBe('test.pdf');
    });

    it('returns undefined for non-existing ID', () => {
      expect(artifactsStore.get('nonexistent')).toBeUndefined();
    });
  });

  describe('remove', () => {
    it('removes existing artifact and returns true', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'to-remove' }));
      expect(artifactsStore.has('to-remove')).toBe(true);

      const result = artifactsStore.remove('to-remove');

      expect(result).toBe(true);
      expect(artifactsStore.has('to-remove')).toBe(false);
      expect(artifactsStore.count).toBe(0);
    });

    it('returns false for non-existing artifact', () => {
      const result = artifactsStore.remove('not-there');
      expect(result).toBe(false);
    });

    it('only removes specified artifact', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'keep' }));
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'remove' }));

      artifactsStore.remove('remove');

      expect(artifactsStore.has('keep')).toBe(true);
      expect(artifactsStore.has('remove')).toBe(false);
      expect(artifactsStore.count).toBe(1);
    });
  });

  describe('clear', () => {
    it('removes all artifacts', () => {
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a1' }));
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a2' }));
      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a3' }));
      expect(artifactsStore.count).toBe(3);

      artifactsStore.clear();

      expect(artifactsStore.count).toBe(0);
      expect(artifactsStore.list).toEqual([]);
    });

    it('is idempotent', () => {
      artifactsStore.clear();
      artifactsStore.clear();
      expect(artifactsStore.count).toBe(0);
    });
  });

  describe('count', () => {
    it('returns correct count after operations', () => {
      expect(artifactsStore.count).toBe(0);

      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a1' }));
      expect(artifactsStore.count).toBe(1);

      artifactsStore.addArtifact(createMockEvent({ artifact_id: 'a2' }));
      expect(artifactsStore.count).toBe(2);

      artifactsStore.remove('a1');
      expect(artifactsStore.count).toBe(1);

      artifactsStore.clear();
      expect(artifactsStore.count).toBe(0);
    });
  });
});
