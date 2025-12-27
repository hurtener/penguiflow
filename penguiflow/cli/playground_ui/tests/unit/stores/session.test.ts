import { describe, it, expect, beforeEach } from 'vitest';
import { sessionStore } from '$lib/stores/session.svelte';
import { artifactsStore } from '$lib/stores/artifacts.svelte';
import type { ArtifactStoredEvent } from '$lib/types';

describe('sessionStore', () => {
  beforeEach(() => {
    sessionStore.reset();
  });

  describe('initial state', () => {
    it('has a session id', () => {
      expect(sessionStore.sessionId).toBeDefined();
      expect(typeof sessionStore.sessionId).toBe('string');
    });

    it('has no active trace', () => {
      expect(sessionStore.activeTraceId).toBeNull();
    });

    it('is not sending', () => {
      expect(sessionStore.isSending).toBe(false);
    });
  });

  describe('sessionId', () => {
    it('can be set', () => {
      sessionStore.sessionId = 'custom-session-id';
      expect(sessionStore.sessionId).toBe('custom-session-id');
    });
  });

  describe('activeTraceId', () => {
    it('can be set to a string', () => {
      sessionStore.activeTraceId = 'trace-123';
      expect(sessionStore.activeTraceId).toBe('trace-123');
    });

    it('can be set to null', () => {
      sessionStore.activeTraceId = 'trace-123';
      sessionStore.activeTraceId = null;
      expect(sessionStore.activeTraceId).toBeNull();
    });
  });

  describe('isSending', () => {
    it('can be toggled', () => {
      expect(sessionStore.isSending).toBe(false);
      sessionStore.isSending = true;
      expect(sessionStore.isSending).toBe(true);
      sessionStore.isSending = false;
      expect(sessionStore.isSending).toBe(false);
    });
  });

  describe('newSession', () => {
    it('generates new session id', () => {
      const oldId = sessionStore.sessionId;
      sessionStore.newSession();
      expect(sessionStore.sessionId).not.toBe(oldId);
    });

    it('clears active trace', () => {
      sessionStore.activeTraceId = 'trace-456';
      sessionStore.newSession();
      expect(sessionStore.activeTraceId).toBeNull();
    });

    it('preserves isSending state', () => {
      sessionStore.isSending = true;
      sessionStore.newSession();
      // Note: newSession doesn't reset isSending
      expect(sessionStore.isSending).toBe(true);
    });

    it('clears artifacts', () => {
      const mockEvent: ArtifactStoredEvent = {
        artifact_id: 'artifact-123',
        mime_type: 'application/pdf',
        size_bytes: 1024,
        filename: 'test.pdf',
        source: {},
        trace_id: 'trace-1',
        session_id: 'session-1',
        ts: Date.now()
      };
      artifactsStore.addArtifact(mockEvent);
      expect(artifactsStore.count).toBe(1);

      sessionStore.newSession();
      expect(artifactsStore.count).toBe(0);
    });
  });

  describe('reset', () => {
    it('resets all state', () => {
      sessionStore.sessionId = 'custom';
      sessionStore.activeTraceId = 'trace';
      sessionStore.isSending = true;

      const oldId = sessionStore.sessionId;
      sessionStore.reset();

      expect(sessionStore.sessionId).not.toBe(oldId);
      expect(sessionStore.activeTraceId).toBeNull();
      expect(sessionStore.isSending).toBe(false);
    });

    it('clears artifacts', () => {
      const mockEvent: ArtifactStoredEvent = {
        artifact_id: 'artifact-456',
        mime_type: 'image/png',
        size_bytes: 2048,
        filename: 'screenshot.png',
        source: { tool: 'screenshot' },
        trace_id: 'trace-2',
        session_id: 'session-2',
        ts: Date.now()
      };
      artifactsStore.addArtifact(mockEvent);
      expect(artifactsStore.count).toBe(1);

      sessionStore.reset();
      expect(artifactsStore.count).toBe(0);
    });
  });
});
