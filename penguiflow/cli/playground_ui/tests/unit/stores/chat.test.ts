import { describe, it, expect, beforeEach } from 'vitest';
import { chatStore } from '$lib/stores/chat.svelte';

describe('chatStore', () => {
  beforeEach(() => {
    chatStore.clear();
  });

  describe('initial state', () => {
    it('starts empty', () => {
      expect(chatStore.isEmpty).toBe(true);
      expect(chatStore.messages).toEqual([]);
    });

    it('has empty input', () => {
      expect(chatStore.input).toBe('');
    });
  });

  describe('addUserMessage', () => {
    it('adds a user message', () => {
      const msg = chatStore.addUserMessage('Hello world');
      
      expect(chatStore.isEmpty).toBe(false);
      expect(chatStore.messages).toHaveLength(1);
      expect(msg.role).toBe('user');
      expect(msg.text).toBe('Hello world');
      expect(msg.id).toBeDefined();
      expect(msg.ts).toBeDefined();
    });

    it('adds multiple messages', () => {
      chatStore.addUserMessage('First');
      chatStore.addUserMessage('Second');
      
      expect(chatStore.messages).toHaveLength(2);
      expect(chatStore.messages[0].text).toBe('First');
      expect(chatStore.messages[1].text).toBe('Second');
    });
  });

  describe('addAgentMessage', () => {
    it('adds an agent message with streaming state', () => {
      const msg = chatStore.addAgentMessage();
      
      expect(msg.role).toBe('agent');
      expect(msg.text).toBe('');
      expect(msg.isStreaming).toBe(true);
      expect(msg.isThinking).toBe(false);
    });
  });

  describe('findMessage', () => {
    it('finds message by id', () => {
      const msg = chatStore.addUserMessage('Test');
      const found = chatStore.findMessage(msg.id);

      expect(found?.id).toBe(msg.id);
      expect(found?.text).toBe(msg.text);
    });

    it('returns undefined for unknown id', () => {
      const found = chatStore.findMessage('nonexistent');
      expect(found).toBeUndefined();
    });
  });

  describe('updateMessage', () => {
    it('updates message properties', () => {
      const msg = chatStore.addAgentMessage();
      chatStore.updateMessage(msg.id, { text: 'Updated text', isStreaming: false });
      
      const updated = chatStore.findMessage(msg.id);
      expect(updated?.text).toBe('Updated text');
      expect(updated?.isStreaming).toBe(false);
    });

    it('does nothing for unknown id', () => {
      chatStore.addUserMessage('Test');
      chatStore.updateMessage('nonexistent', { text: 'Updated' });
      
      expect(chatStore.messages[0].text).toBe('Test');
    });
  });

  describe('input management', () => {
    it('sets input value', () => {
      chatStore.input = 'New input';
      expect(chatStore.input).toBe('New input');
    });

    it('clears input', () => {
      chatStore.input = 'Some text';
      chatStore.clearInput();
      expect(chatStore.input).toBe('');
    });
  });

  describe('clear', () => {
    it('clears all messages and input', () => {
      chatStore.addUserMessage('Message 1');
      chatStore.addAgentMessage();
      chatStore.input = 'Draft';
      
      chatStore.clear();
      
      expect(chatStore.messages).toEqual([]);
      expect(chatStore.input).toBe('');
      expect(chatStore.isEmpty).toBe(true);
    });
  });
});
