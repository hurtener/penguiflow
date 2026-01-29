import { describe, it, expect, beforeEach } from 'vitest';
import { createSessionStore } from '$lib/stores';

const sessionStore = createSessionStore();

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

  });
});
