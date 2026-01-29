import { getContext, setContext } from 'svelte';
import type { ChatMessage } from '$lib/types';
import { randomId, ANSWER_GATE_SENTINEL } from '$lib/utils';

const CHAT_STORE_KEY = Symbol('chat-store');

export interface ChatStore {
  readonly messages: ChatMessage[];
  input: string;
  readonly isEmpty: boolean;
  addUserMessage(text: string): ChatMessage;
  addAgentMessage(): ChatMessage;
  findMessage(id: string): ChatMessage | undefined;
  updateMessage(id: string, updates: Partial<ChatMessage>): void;
  clearInput(): void;
  clear(): void;
}

export function createChatStore(): ChatStore {
  let messages = $state<ChatMessage[]>([]);
  let input = $state('');

  return {
    get messages() { return messages; },
    get input() { return input; },
    set input(v: string) { input = v; },

    get isEmpty() { return messages.length === 0; },

    addUserMessage(text: string): ChatMessage {
      const msg: ChatMessage = {
        id: randomId(),
        role: 'user',
        text,
        ts: Date.now()
      };
      messages.push(msg);
      return msg;
    },

    addAgentMessage(): ChatMessage {
      const msg: ChatMessage = {
        id: randomId(),
        role: 'agent',
        text: '',
        observations: '',
        showObservations: false,
        isStreaming: true,
        isThinking: false,
        answerStreamDone: false,
        revisionStreamActive: false,
        answerActionSeq: ANSWER_GATE_SENTINEL,
        ts: Date.now()
      };
      messages.push(msg);
      return msg;
    },

    findMessage(id: string): ChatMessage | undefined {
      return messages.find(m => m.id === id);
    },

    updateMessage(id: string, updates: Partial<ChatMessage>) {
      const msg = messages.find(m => m.id === id);
      if (msg) {
        Object.assign(msg, updates);
      }
    },

    clearInput() {
      input = '';
    },

    clear() {
      messages = [];
      input = '';
    }
  };
}

export function setChatStore(store: ChatStore = createChatStore()): ChatStore {
  setContext(CHAT_STORE_KEY, store);
  return store;
}

export function getChatStore(): ChatStore {
  return getContext<ChatStore>(CHAT_STORE_KEY);
}
