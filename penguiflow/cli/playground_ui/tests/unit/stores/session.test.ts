import { describe, it, expect, beforeEach } from 'vitest';
import { createSessionStore } from '$lib/stores';
import type { SessionStore } from '$lib/stores';

describe('sessionStore', () => {
  let sessionStore: SessionStore;

  beforeEach(() => {
    localStorage.clear();
    sessionStore = createSessionStore();
  });

  describe('initial state', () => {
    it('has a session id', () => {
      expect(sessionStore.sessionId).toBeDefined();
      expect(typeof sessionStore.sessionId).toBe('string');
    });

    it('tracks current session in recent list', () => {
      expect(sessionStore.recentSessionIds[0]).toBe(sessionStore.sessionId);
    });

    it('has no active trace', () => {
      expect(sessionStore.activeTraceId).toBeNull();
    });

    it('is not sending', () => {
      expect(sessionStore.isSending).toBe(false);
    });
  });

  describe('sessionId', () => {
    it('can be set directly', () => {
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

  describe('switchSession', () => {
    it('switches to provided session id', () => {
      sessionStore.switchSession('existing-session');
      expect(sessionStore.sessionId).toBe('existing-session');
    });

    it('moves switched session to front of LRU', () => {
      sessionStore.switchSession('a');
      sessionStore.switchSession('b');
      sessionStore.switchSession('a');

      expect(sessionStore.recentSessionIds.slice(0, 2)).toEqual(['a', 'b']);
    });

    it('clears active trace', () => {
      sessionStore.activeTraceId = 'trace-456';
      sessionStore.switchSession('switch-target');
      expect(sessionStore.activeTraceId).toBeNull();
    });
  });

  describe('touchSession', () => {
    it('updates LRU ordering without switching session', () => {
      const current = sessionStore.sessionId;
      sessionStore.switchSession('other');
      sessionStore.sessionId = current;
      sessionStore.touchSession(current);

      expect(sessionStore.recentSessionIds[0]).toBe(current);
      expect(sessionStore.sessionId).toBe(current);
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
