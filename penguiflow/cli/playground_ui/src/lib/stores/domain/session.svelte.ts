import { getContext, setContext } from 'svelte';
import { randomId } from '$lib/utils';

const SESSION_STORE_KEY = Symbol('session-store');

export interface SessionStore {
  sessionId: string;
  activeTraceId: string | null;
  isSending: boolean;
  reset(): void;
  newSession(): void;
}

export function createSessionStore(): SessionStore {
  let sessionId = $state(randomId());
  let activeTraceId = $state<string | null>(null);
  let isSending = $state(false);

  return {
    get sessionId() { return sessionId; },
    set sessionId(v: string) { sessionId = v; },

    get activeTraceId() { return activeTraceId; },
    set activeTraceId(v: string | null) { activeTraceId = v; },

    get isSending() { return isSending; },
    set isSending(v: boolean) { isSending = v; },

    reset() {
      sessionId = randomId();
      activeTraceId = null;
      isSending = false;
    },

    newSession() {
      sessionId = randomId();
      activeTraceId = null;
    }
  };
}

export function setSessionStore(store: SessionStore = createSessionStore()): SessionStore {
  setContext(SESSION_STORE_KEY, store);
  return store;
}

export function getSessionStore(): SessionStore {
  return getContext<SessionStore>(SESSION_STORE_KEY);
}
