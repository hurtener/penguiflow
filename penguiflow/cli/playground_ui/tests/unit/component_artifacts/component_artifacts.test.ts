import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  componentArtifactsStore,
  type ArtifactChunkPayload,
  type PendingInteraction
} from '$lib/stores/component_artifacts.svelte';

describe('componentArtifactsStore', () => {
  beforeEach(() => {
    componentArtifactsStore.clear();
    vi.useFakeTimers();
  });

  describe('initial state', () => {
    it('starts with empty artifacts', () => {
      expect(componentArtifactsStore.artifacts).toEqual([]);
    });

    it('starts with no pending interaction', () => {
      expect(componentArtifactsStore.pendingInteraction).toBeNull();
    });

    it('starts with no last artifact', () => {
      expect(componentArtifactsStore.lastArtifact).toBeNull();
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

      componentArtifactsStore.addArtifactChunk(payload);

      expect(componentArtifactsStore.artifacts).toHaveLength(1);
      expect(componentArtifactsStore.artifacts[0]).toMatchObject({
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

      componentArtifactsStore.addArtifactChunk(payload);

      expect(componentArtifactsStore.artifacts).toHaveLength(0);
    });

    it('ignores chunks without component field', () => {
      const payload: ArtifactChunkPayload = {
        artifact_type: 'ui_component',
        chunk: { props: { value: 1 } }
      };

      componentArtifactsStore.addArtifactChunk(payload);

      expect(componentArtifactsStore.artifacts).toHaveLength(0);
    });

    it('ignores null or non-object chunks', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: null
      });
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: 'string'
      });

      expect(componentArtifactsStore.artifacts).toHaveLength(0);
    });

    it('generates id when not provided', () => {
      vi.setSystemTime(new Date('2024-01-01T12:00:00Z'));

      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          component: 'metric',
          props: { value: 42 }
        }
      });

      expect(componentArtifactsStore.artifacts[0].id).toMatch(/^ui_\d+$/);
    });

    it('uses message_id from options', () => {
      componentArtifactsStore.addArtifactChunk(
        {
          artifact_type: 'ui_component',
          chunk: { component: 'markdown', props: {} }
        },
        { message_id: 'msg-123' }
      );

      expect(componentArtifactsStore.artifacts[0].message_id).toBe('msg-123');
    });

    it('uses message_id from meta if not in options', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'markdown', props: {} },
        meta: { message_id: 'meta-msg-456' }
      });

      expect(componentArtifactsStore.artifacts[0].message_id).toBe('meta-msg-456');
    });

    it('defaults seq to 0', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'json', props: {} }
      });

      expect(componentArtifactsStore.artifacts[0].seq).toBe(0);
    });

    it('updates lastArtifact', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          id: 'first',
          component: 'markdown',
          props: {}
        }
      });
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          id: 'second',
          component: 'json',
          props: {}
        }
      });

      expect(componentArtifactsStore.lastArtifact?.id).toBe('second');
    });

    it('handles empty props', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'callout' }
      });

      expect(componentArtifactsStore.artifacts[0].props).toEqual({});
    });

    it('preserves meta data', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'code', props: { code: 'x = 1' } },
        meta: { source: 'llm', model: 'gpt-4' }
      });

      expect(componentArtifactsStore.artifacts[0].meta).toEqual({
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
      componentArtifactsStore.setPendingInteraction(mockInteraction);

      expect(componentArtifactsStore.pendingInteraction).toEqual(mockInteraction);
    });

    it('clears pending interaction with null', () => {
      componentArtifactsStore.setPendingInteraction(mockInteraction);
      componentArtifactsStore.setPendingInteraction(null);

      expect(componentArtifactsStore.pendingInteraction).toBeNull();
    });

    it('clears pending interaction with clearPendingInteraction', () => {
      componentArtifactsStore.setPendingInteraction(mockInteraction);
      componentArtifactsStore.clearPendingInteraction();

      expect(componentArtifactsStore.pendingInteraction).toBeNull();
    });

    it('updates pending interaction partially', () => {
      componentArtifactsStore.setPendingInteraction(mockInteraction);
      componentArtifactsStore.updatePendingInteraction({
        props: { message: 'Updated message' }
      });

      expect(componentArtifactsStore.pendingInteraction?.props).toEqual({
        message: 'Updated message'
      });
      expect(componentArtifactsStore.pendingInteraction?.tool_call_id).toBe('tc-001');
    });

    it('does nothing when updating without pending interaction', () => {
      componentArtifactsStore.updatePendingInteraction({
        props: { message: 'No-op' }
      });

      expect(componentArtifactsStore.pendingInteraction).toBeNull();
    });
  });

  describe('clear', () => {
    it('clears all artifacts', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'markdown', props: {} }
      });
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'json', props: {} }
      });

      componentArtifactsStore.clear();

      expect(componentArtifactsStore.artifacts).toEqual([]);
    });

    it('clears pending interaction', () => {
      componentArtifactsStore.setPendingInteraction({
        tool_call_id: 'tc-001',
        tool_name: 'confirm',
        component: 'confirm',
        props: {},
        created_at: Date.now()
      });

      componentArtifactsStore.clear();

      expect(componentArtifactsStore.pendingInteraction).toBeNull();
    });

    it('clears last artifact', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: { component: 'markdown', props: {} }
      });

      componentArtifactsStore.clear();

      expect(componentArtifactsStore.lastArtifact).toBeNull();
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
        componentArtifactsStore.addArtifactChunk({
          artifact_type: 'ui_component',
          seq: idx,
          chunk
        });
      });

      expect(componentArtifactsStore.artifacts).toHaveLength(3);
      expect(componentArtifactsStore.artifacts[0].component).toBe('markdown');
      expect(componentArtifactsStore.artifacts[1].component).toBe('code');
      expect(componentArtifactsStore.artifacts[2].component).toBe('json');
    });

    it('tracks sequence numbers correctly', () => {
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        seq: 5,
        chunk: { component: 'a', props: {} }
      });
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        seq: 10,
        chunk: { component: 'b', props: {} }
      });

      expect(componentArtifactsStore.artifacts[0].seq).toBe(5);
      expect(componentArtifactsStore.artifacts[1].seq).toBe(10);
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

      componentArtifactsStore.setPendingInteraction(interaction);

      // 2. User submits form - clear pending
      expect(componentArtifactsStore.pendingInteraction).not.toBeNull();
      componentArtifactsStore.clearPendingInteraction();

      // 3. Server responds with result artifact
      componentArtifactsStore.addArtifactChunk({
        artifact_type: 'ui_component',
        chunk: {
          component: 'markdown',
          props: { content: 'Form submitted successfully!' }
        }
      });

      expect(componentArtifactsStore.pendingInteraction).toBeNull();
      expect(componentArtifactsStore.artifacts).toHaveLength(1);
    });
  });
});
