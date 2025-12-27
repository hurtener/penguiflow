import { randomId } from '$lib/utils';
import { artifactsStore } from './artifacts.svelte';

function createSessionStore() {
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
      artifactsStore.clear();
    },

    newSession() {
      sessionId = randomId();
      activeTraceId = null;
      artifactsStore.clear();
    }
  };
}

export const sessionStore = createSessionStore();
