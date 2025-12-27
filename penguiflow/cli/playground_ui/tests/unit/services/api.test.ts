import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  loadMeta,
  loadSpec,
  validateSpec,
  generateProject,
  fetchTrajectory,
  extractFilename,
  downloadArtifact,
  getArtifactMeta
} from '$lib/services/api';

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

  describe('extractFilename', () => {
    it('returns null for null header', () => {
      expect(extractFilename(null)).toBeNull();
    });

    it('returns null for empty header', () => {
      expect(extractFilename('')).toBeNull();
    });

    it('extracts quoted filename', () => {
      expect(extractFilename('attachment; filename="report.pdf"')).toBe('report.pdf');
    });

    it('extracts unquoted filename', () => {
      expect(extractFilename('attachment; filename=report.pdf')).toBe('report.pdf');
    });

    it('handles filename with spaces (quoted)', () => {
      expect(extractFilename('attachment; filename="my report.pdf"')).toBe('my report.pdf');
    });

    it('handles inline disposition', () => {
      expect(extractFilename('inline; filename="image.png"')).toBe('image.png');
    });

    it('returns null for header without filename', () => {
      expect(extractFilename('attachment')).toBeNull();
    });

    it('prefers quoted over unquoted when both present', () => {
      // Edge case: malformed header with both formats
      expect(extractFilename('filename="quoted.pdf"; filename=unquoted.pdf')).toBe('quoted.pdf');
    });
  });

  describe('downloadArtifact', () => {
    let mockCreateElement: ReturnType<typeof vi.fn>;
    let mockAppendChild: ReturnType<typeof vi.fn>;
    let mockRemoveChild: ReturnType<typeof vi.fn>;
    let mockClick: ReturnType<typeof vi.fn>;
    let mockCreateObjectURL: ReturnType<typeof vi.fn>;
    let mockRevokeObjectURL: ReturnType<typeof vi.fn>;
    let mockAnchor: { href: string; download: string; click: ReturnType<typeof vi.fn> };

    beforeEach(() => {
      mockClick = vi.fn();
      mockAnchor = { href: '', download: '', click: mockClick };
      mockCreateElement = vi.fn().mockReturnValue(mockAnchor);
      mockAppendChild = vi.fn();
      mockRemoveChild = vi.fn();
      mockCreateObjectURL = vi.fn().mockReturnValue('blob:http://localhost/test');
      mockRevokeObjectURL = vi.fn();

      // Mock DOM APIs
      global.document.createElement = mockCreateElement;
      global.document.body.appendChild = mockAppendChild;
      global.document.body.removeChild = mockRemoveChild;
      global.URL.createObjectURL = mockCreateObjectURL;
      global.URL.revokeObjectURL = mockRevokeObjectURL;
    });

    it('fetches artifact with correct headers', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers({ 'Content-Disposition': 'attachment; filename="test.pdf"' })
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(fetch).toHaveBeenCalledWith('/artifacts/artifact-123', {
        headers: { 'X-Session-ID': 'session-456' }
      });
    });

    it('triggers download with Content-Disposition filename', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers({ 'Content-Disposition': 'attachment; filename="report.pdf"' })
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(mockAnchor.download).toBe('report.pdf');
      expect(mockClick).toHaveBeenCalled();
    });

    it('uses provided filename over Content-Disposition', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers({ 'Content-Disposition': 'attachment; filename="server.pdf"' })
      });

      await downloadArtifact('artifact-123', 'session-456', 'custom.pdf');

      expect(mockAnchor.download).toBe('custom.pdf');
    });

    it('falls back to artifact ID when no filename available', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers()
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(mockAnchor.download).toBe('artifact-artifact-123');
    });

    it('throws error on failed response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: 'Not Found'
      });

      await expect(downloadArtifact('artifact-123', 'session-456')).rejects.toThrow(
        'Download failed: 404 Not Found'
      );
    });

    it('cleans up object URL after download', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers()
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(mockRevokeObjectURL).toHaveBeenCalledWith('blob:http://localhost/test');
    });

    it('removes anchor element after download', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers()
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(mockAppendChild).toHaveBeenCalledWith(mockAnchor);
      expect(mockRemoveChild).toHaveBeenCalledWith(mockAnchor);
    });
  });

  describe('getArtifactMeta', () => {
    it('fetches artifact metadata successfully', async () => {
      const mockMeta = {
        id: 'artifact-123',
        mime_type: 'application/pdf',
        size_bytes: 1024,
        filename: 'report.pdf',
        sha256: 'abc123',
        source: { tool: 'pdf_generator' }
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockMeta)
      });

      const result = await getArtifactMeta('artifact-123', 'session-456');

      expect(fetch).toHaveBeenCalledWith('/artifacts/artifact-123/meta', {
        headers: { 'X-Session-ID': 'session-456' }
      });
      expect(result).toEqual(mockMeta);
    });

    it('returns null on error response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 404
      });

      const result = await getArtifactMeta('artifact-123', 'session-456');

      expect(result).toBeNull();
    });

    it('returns null on network failure', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const result = await getArtifactMeta('artifact-123', 'session-456');

      expect(result).toBeNull();
    });
  });
});
