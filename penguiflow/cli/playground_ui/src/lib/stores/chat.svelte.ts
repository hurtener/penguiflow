import type { ChatMessage } from '$lib/types';
import { randomId, ANSWER_GATE_SENTINEL } from '$lib/utils';

function createChatStore() {
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

export const chatStore = createChatStore();
