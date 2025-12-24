import { describe, it, expect, beforeEach, vi } from 'vitest';
import { loadMeta, loadSpec, validateSpec, generateProject, fetchTrajectory } from '$lib/services/api';

describe('api service', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  describe('loadMeta', () => {
    it('fetches meta data successfully', async () => {
      const mockData = {
        agent: { name: 'test-agent' },
        tools: [{ name: 'tool1' }]
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await loadMeta();

      expect(fetch).toHaveBeenCalledWith('/ui/meta');
      expect(result).toEqual(mockData);
    });

    it('returns null on error', async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await loadMeta();

      expect(result).toBeNull();
    });

    it('returns null on network failure', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const result = await loadMeta();

      expect(result).toBeNull();
    });
  });

  describe('loadSpec', () => {
    it('fetches spec data successfully', async () => {
      const mockData = {
        content: 'name: agent',
        valid: true
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await loadSpec();

      expect(fetch).toHaveBeenCalledWith('/ui/spec');
      expect(result).toEqual(mockData);
    });

    it('returns null on error', async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await loadSpec();

      expect(result).toBeNull();
    });
  });

  describe('validateSpec', () => {
    it('validates spec successfully', async () => {
      const mockResult = { valid: true };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockResult)
      });

      const result = await validateSpec('name: test');

      expect(fetch).toHaveBeenCalledWith('/ui/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec_text: 'name: test' })
      });
      expect(result).toEqual(mockResult);
    });

    it('returns validation errors', async () => {
      const mockResult = {
        valid: false,
        errors: [{ message: 'Invalid', line: 1 }]
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockResult)
      });

      const result = await validateSpec('invalid');

      expect(result?.valid).toBe(false);
      expect(result?.errors).toHaveLength(1);
    });

    it('returns null on exception', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Failed'));

      const result = await validateSpec('test');

      expect(result).toBeNull();
    });
  });

  describe('generateProject', () => {
    it('returns true on success', async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: true });

      const result = await generateProject('name: test');

      expect(fetch).toHaveBeenCalledWith('/ui/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec_text: 'name: test' })
      });
      expect(result).toBe(true);
    });

    it('returns errors on failure', async () => {
      const errors = [{ message: 'Generation failed', line: 5 }];

      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: () => Promise.resolve(errors)
      });

      const result = await generateProject('invalid spec');

      expect(result).toEqual(errors);
    });

    it('returns null on exception', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const result = await generateProject('test');

      expect(result).toBeNull();
    });
  });

  describe('fetchTrajectory', () => {
    it('fetches trajectory successfully', async () => {
      const mockData = {
        steps: [{ action: { next_node: 'test' } }]
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await fetchTrajectory('trace-123', 'session-456');

      expect(fetch).toHaveBeenCalledWith(
        '/trajectory/trace-123?session_id=session-456'
      );
      expect(result).toEqual(mockData);
    });

    it('encodes session id in URL', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({})
      });

      await fetchTrajectory('trace', 'session with spaces');

      expect(fetch).toHaveBeenCalledWith(
        '/trajectory/trace?session_id=session%20with%20spaces'
      );
    });

    it('returns null on error', async () => {
      global.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await fetchTrajectory('trace', 'session');

      expect(result).toBeNull();
    });
  });
});
