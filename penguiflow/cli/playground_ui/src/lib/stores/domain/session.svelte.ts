import { getContext, setContext } from 'svelte';
import { randomId } from '$lib/utils';

const SESSION_STORE_KEY = Symbol('session-store');
const RECENT_SESSIONS_STORAGE_KEY = 'penguiflow.playground.recent_sessions.v1';
const RECENT_SESSIONS_MAX = 20;

export interface SessionStore {
  sessionId: string;
  readonly recentSessionIds: string[];
  activeTraceId: string | null;
  isSending: boolean;
  switchSession(sessionId: string): void;
  touchSession(sessionId?: string): void;
  reset(): void;
  newSession(): void;
}

function readRecentSessions(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(RECENT_SESSIONS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((value): value is string => typeof value === 'string')
      .map(value => value.trim())
      .filter(Boolean)
      .slice(0, RECENT_SESSIONS_MAX);
  } catch {
    return [];
  }
}

function writeRecentSessions(values: string[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(RECENT_SESSIONS_STORAGE_KEY, JSON.stringify(values));
  } catch {
    // Best effort only - local storage may be unavailable in some environments.
  }
}

export function createSessionStore(): SessionStore {
  let sessionId = $state(randomId());
  let recentSessionIds = $state<string[]>(readRecentSessions());
  let activeTraceId = $state<string | null>(null);
  let isSending = $state(false);

  function touchSession(nextSessionId?: string): void {
    const target = (nextSessionId ?? sessionId).trim();
    if (!target) return;
    const next = [target, ...recentSessionIds.filter(existing => existing !== target)].slice(0, RECENT_SESSIONS_MAX);
    recentSessionIds = next;
    writeRecentSessions(next);
  }

  function switchSession(nextSessionId: string): void {
    const normalized = nextSessionId.trim();
    if (!normalized) return;
    sessionId = normalized;
    activeTraceId = null;
    touchSession(normalized);
  }

  touchSession(sessionId);

  return {
    get sessionId() { return sessionId; },
    set sessionId(v: string) { sessionId = v; },

    get recentSessionIds() { return recentSessionIds; },

    get activeTraceId() { return activeTraceId; },
    set activeTraceId(v: string | null) { activeTraceId = v; },

    get isSending() { return isSending; },
    set isSending(v: boolean) { isSending = v; },

    switchSession,
    touchSession,

    reset() {
      switchSession(randomId());
      activeTraceId = null;
      isSending = false;
    },

    newSession() {
      switchSession(randomId());
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
