import { describe, it, expect, beforeEach, vi } from 'vitest';
import { createInteractionsStore } from '$lib/stores';
import type { ArtifactChunkPayload, PendingInteraction } from '$lib/types';

const interactionsStore = createInteractionsStore();

describe('interactionsStore', () => {
  beforeEach(() => {
    interactionsStore.clear();
    vi.useFakeTimers();
  });

  describe('initial state', () => {
    it('starts with empty artifacts', () => {
      expect(interactionsStore.artifacts).toEqual([]);
    });

    it('starts with no pending interaction', () => {
      expect(interactionsStore.pendingInteraction).toBeNull();
    });

    it('starts with no last artifact', () => {
      expect(interactionsStore.lastArtifact).toBeNull();
    });
  });

  describe('addArtifactChunk', () => {
    it('adds ui_component artifact', () => {
      vi.setSystemTime(new Date('2024-01-01T00:00:00Z'));

      const payload: ArtifactChunkPayload = {
        artifact_type: 'ui_component',
        seq: 1,
        ts: Date.now(),
        chunk: {
          id: 'artifact-1',
          component: 'markdown',
          props: { content: '# Hello' },
          title: 'Greeting'
        }
      };

      interactionsStore.addArtifactChunk(payload);

      expect(interactionsStore.artifacts).toHaveLength(1);
      const [artifact] = interactionsStore.artifacts;
      expect(artifact).toBeDefined();
      expect(artifact).toMatchObject({
        id: 'artifact-1',
        component: 'markdown',
        props: { content: '# Hello' },
        title: 'Greeting',
        seq: 1
      });
    });

    it('ignores non-ui_component artifacts', () => {
      const payload: ArtifactChunkPayload = {
        artifact_type: 'text',
        chunk: { content: 'hello' }
      };

      interactionsStore.addArtifactChunk(payload);

      expect(interactionsStore.artifacts).toHaveLength(0);
    });

    it('ignores chunks without component field', () => {
      const payload: ArtifactChunkPayload = {
        artifact_type: 'ui_component',
        chunk: { props: { value: 1 } }
      };

      interactionsStore.addArtifactChunk(payload);

      expect(interactionsStore.artifacts).toHaveLength(0);
    });

    it('ignores null or non-object chunks', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: null
      });
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: 'string'
      });

      expect(interactionsStore.artifacts).toHaveLength(0);
    });

    it('generates id when not provided', () => {
      vi.setSystemTime(new Date('2024-01-01T12:00:00Z'));

      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          component: 'metric',
          props: { value: 42 }
        }
      });

      const artifact = interactionsStore.artifacts[0]!;
      expect(artifact.id).toMatch(/^ui_\d+$/);
    });

    it('uses message_id from options', () => {
      interactionsStore.addArtifactChunk(
        {
          artifact_type: 'ui_component',
          chunk: { component: 'markdown', props: {} }
        },
        { message_id: 'msg-123' }
      );

      const artifact = interactionsStore.artifacts[0]!;
      expect(artifact.message_id).toBe('msg-123');
    });

    it('uses message_id from meta if not in options', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'markdown', props: {} },
        meta: { message_id: 'meta-msg-456' }
      });

      const artifact = interactionsStore.artifacts[0]!;
      expect(artifact.message_id).toBe('meta-msg-456');
    });

    it('defaults seq to 0', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'json', props: {} }
      });

      const artifact = interactionsStore.artifacts[0]!;
      expect(artifact.seq).toBe(0);
    });

    it('updates lastArtifact', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          id: 'first',
          component: 'markdown',
          props: {}
        }
      });
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          id: 'second',
          component: 'json',
          props: {}
        }
      });

      expect(interactionsStore.lastArtifact?.id).toBe('second');
    });

    it('handles empty props', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'callout' }
      });

      const artifact = interactionsStore.artifacts[0]!;
      expect(artifact.props).toEqual({});
    });

    it('preserves meta data', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'code', props: { code: 'x = 1' } },
        meta: { source: 'llm', model: 'gpt-4' }
      });

      const artifact = interactionsStore.artifacts[0]!;
      expect(artifact.meta).toEqual({
        source: 'llm',
        model: 'gpt-4'
      });
    });
  });

  describe('pendingInteraction', () => {
    const mockInteraction: PendingInteraction = {
      tool_call_id: 'tc-001',
      tool_name: 'show_confirm',
      component: 'confirm',
      props: {
        message: 'Delete this item?',
        confirmLabel: 'Delete',
        cancelLabel: 'Cancel'
      },
      message_id: 'msg-abc',
      resume_token: 'resume-xyz',
      created_at: Date.now()
    };

    it('sets pending interaction', () => {
      interactionsStore.setPendingInteraction(mockInteraction);

      expect(interactionsStore.pendingInteraction).toEqual(mockInteraction);
    });

    it('clears pending interaction with null', () => {
      interactionsStore.setPendingInteraction(mockInteraction);
      interactionsStore.setPendingInteraction(null);

      expect(interactionsStore.pendingInteraction).toBeNull();
    });

    it('clears pending interaction with clearPendingInteraction', () => {
      interactionsStore.setPendingInteraction(mockInteraction);
      interactionsStore.clearPendingInteraction();

      expect(interactionsStore.pendingInteraction).toBeNull();
    });

    it('updates pending interaction partially', () => {
      interactionsStore.setPendingInteraction(mockInteraction);
      interactionsStore.updatePendingInteraction({
        props: { message: 'Updated message' }
      });

      expect(interactionsStore.pendingInteraction?.props).toEqual({
        message: 'Updated message'
      });
      expect(interactionsStore.pendingInteraction?.tool_call_id).toBe('tc-001');
    });

    it('does nothing when updating without pending interaction', () => {
      interactionsStore.updatePendingInteraction({
        props: { message: 'No-op' }
      });

      expect(interactionsStore.pendingInteraction).toBeNull();
    });
  });

  describe('clear', () => {
    it('clears all artifacts', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'markdown', props: {} }
      });
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'json', props: {} }
      });

      interactionsStore.clear();

      expect(interactionsStore.artifacts).toEqual([]);
    });

    it('clears pending interaction', () => {
      interactionsStore.setPendingInteraction({
        tool_call_id: 'tc-001',
        tool_name: 'confirm',
        component: 'confirm',
        props: {},
        created_at: Date.now()
      });

      interactionsStore.clear();

      expect(interactionsStore.pendingInteraction).toBeNull();
    });

    it('clears last artifact', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'markdown', props: {} }
      });

      interactionsStore.clear();

      expect(interactionsStore.lastArtifact).toBeNull();
    });
  });

  describe('multiple artifacts', () => {
    it('maintains artifact order', () => {
      const artifacts = [
        { component: 'markdown', props: { content: 'First' } },
        { component: 'code', props: { code: 'Second' } },
        { component: 'json', props: { data: 'Third' } }
      ];

      artifacts.forEach((chunk, idx) => {
        interactionsStore.addArtifactChunk({
          artifact_type: 'ui_component',
          seq: idx,
          chunk
        });
      });

      expect(interactionsStore.artifacts).toHaveLength(3);
      const [first, second, third] = interactionsStore.artifacts;
      expect(first?.component).toBe('markdown');
      expect(second?.component).toBe('code');
      expect(third?.component).toBe('json');
    });

    it('tracks sequence numbers correctly', () => {
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        seq: 5,
        chunk: { component: 'a', props: {} }
      });
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        seq: 10,
        chunk: { component: 'b', props: {} }
      });

      const [first, second] = interactionsStore.artifacts;
      expect(first?.seq).toBe(5);
      expect(second?.seq).toBe(10);
    });
  });

  describe('interactive component flow', () => {
    it('supports full interactive workflow', () => {
      // 1. Receive interactive component as pending
      const interaction: PendingInteraction = {
        tool_call_id: 'tc-form-001',
        tool_name: 'show_form',
        component: 'form',
        props: {
          fields: [
            { name: 'email', type: 'text', label: 'Email' }
          ],
          submitLabel: 'Submit'
        },
        resume_token: 'resume-token-abc',
        created_at: Date.now()
      };

      interactionsStore.setPendingInteraction(interaction);

      // 2. User submits form - clear pending
      expect(interactionsStore.pendingInteraction).not.toBeNull();
      interactionsStore.clearPendingInteraction();

      // 3. Server responds with result artifact
      interactionsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          component: 'markdown',
          props: { content: 'Form submitted successfully!' }
        }
      });

      expect(interactionsStore.pendingInteraction).toBeNull();
      expect(interactionsStore.artifacts).toHaveLength(1);
    });
  });
});
