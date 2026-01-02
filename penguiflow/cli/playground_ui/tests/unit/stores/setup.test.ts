import { describe, it, expect, beforeEach } from 'vitest';
import { createSetupStore } from '$lib/stores';

const setupStore = createSetupStore();

describe('setupStore', () => {
  beforeEach(() => {
    setupStore.reset();
  });

  describe('initial state', () => {
    it('has default tenant id', () => {
      expect(setupStore.tenantId).toBe('playground-tenant');
    });

    it('has default user id', () => {
      expect(setupStore.userId).toBe('playground-user');
    });

    it('has empty tool context', () => {
      expect(setupStore.toolContextRaw).toBe('{}');
    });

    it('has empty llm context', () => {
      expect(setupStore.llmContextRaw).toBe('{}');
    });

    it('has no error', () => {
      expect(setupStore.error).toBeNull();
    });

    it('defaults to SSE protocol', () => {
      expect(setupStore.useAgui).toBe(false);
    });
  });

  describe('setters', () => {
    it('sets tenant id', () => {
      setupStore.tenantId = 'new-tenant';
      expect(setupStore.tenantId).toBe('new-tenant');
    });

    it('sets user id', () => {
      setupStore.userId = 'new-user';
      expect(setupStore.userId).toBe('new-user');
    });

    it('sets tool context raw', () => {
      setupStore.toolContextRaw = '{"key": "value"}';
      expect(setupStore.toolContextRaw).toBe('{"key": "value"}');
    });

    it('sets llm context raw', () => {
      setupStore.llmContextRaw = '{"model": "gpt-4"}';
      expect(setupStore.llmContextRaw).toBe('{"model": "gpt-4"}');
    });

    it('sets AG-UI toggle', () => {
      setupStore.useAgui = true;
      expect(setupStore.useAgui).toBe(true);
    });
  });

  describe('parseContexts', () => {
    it('returns parsed contexts with tenant and user', () => {
      const result = setupStore.parseContexts();
      
      expect(result).not.toBeNull();
      expect(result?.toolContext.tenant_id).toBe('playground-tenant');
      expect(result?.toolContext.user_id).toBe('playground-user');
      expect(result?.llmContext).toEqual({});
    });

    it('merges extra tool context', () => {
      setupStore.toolContextRaw = '{"custom_key": "custom_value"}';
      const result = setupStore.parseContexts();
      
      expect(result?.toolContext.custom_key).toBe('custom_value');
      expect(result?.toolContext.tenant_id).toBe('playground-tenant');
    });

    it('parses llm context', () => {
      setupStore.llmContextRaw = '{"temperature": 0.7}';
      const result = setupStore.parseContexts();
      
      expect(result?.llmContext).toEqual({ temperature: 0.7 });
    });

    it('returns null and sets error for invalid tool context', () => {
      setupStore.toolContextRaw = 'not json';
      const result = setupStore.parseContexts();
      
      expect(result).toBeNull();
      expect(setupStore.error).toContain('Tool context');
    });

    it('returns null and sets error for invalid llm context', () => {
      setupStore.llmContextRaw = '[1, 2, 3]';
      const result = setupStore.parseContexts();
      
      expect(result).toBeNull();
      expect(setupStore.error).toContain('LLM context');
    });

    it('clears error on success', () => {
      setupStore.error = 'Previous error';
      setupStore.parseContexts();
      
      expect(setupStore.error).toBeNull();
    });
  });

  describe('clearError', () => {
    it('clears the error', () => {
      setupStore.error = 'Some error';
      setupStore.clearError();
      expect(setupStore.error).toBeNull();
    });
  });

  describe('reset', () => {
    it('resets all values to defaults', () => {
      setupStore.tenantId = 'custom-tenant';
      setupStore.userId = 'custom-user';
      setupStore.toolContextRaw = '{"key": "value"}';
      setupStore.llmContextRaw = '{"model": "test"}';
      setupStore.error = 'Some error';
      setupStore.useAgui = true;
      
      setupStore.reset();
      
      expect(setupStore.tenantId).toBe('playground-tenant');
      expect(setupStore.userId).toBe('playground-user');
      expect(setupStore.toolContextRaw).toBe('{}');
      expect(setupStore.llmContextRaw).toBe('{}');
      expect(setupStore.error).toBeNull();
      expect(setupStore.useAgui).toBe(false);
    });
  });
});
