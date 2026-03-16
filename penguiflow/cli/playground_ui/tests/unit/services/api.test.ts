import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  loadMeta,
  loadSpec,
  validateSpec,
  generateProject,
  fetchTrajectory,
  extractFilename,
  downloadArtifact,
  getArtifactMeta,
  listTraces,
  setTraceTags,
  exportEvalDataset,
  loadEvalDataset,
  runEval,
  listEvalDatasets,
  listEvalMetrics,
  fetchEvalCaseComparison
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

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await loadMeta();

      expect(fetch).toHaveBeenCalledWith('/ui/meta');
      expect(result).toEqual(mockData);
    });

    it('returns null on error', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await loadMeta();

      expect(result).toBeNull();
    });

    it('returns null on network failure', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

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

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await loadSpec();

      expect(fetch).toHaveBeenCalledWith('/ui/spec');
      expect(result).toEqual(mockData);
    });

    it('returns null on error', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await loadSpec();

      expect(result).toBeNull();
    });
  });

  describe('validateSpec', () => {
    it('validates spec successfully', async () => {
      const mockResult = { valid: true };

      globalThis.fetch = vi.fn().mockResolvedValue({
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

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockResult)
      });

      const result = await validateSpec('invalid');

      expect(result?.valid).toBe(false);
      expect(result?.errors).toHaveLength(1);
    });

    it('returns null on exception', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('Failed'));

      const result = await validateSpec('test');

      expect(result).toBeNull();
    });
  });

  describe('generateProject', () => {
    it('returns true on success', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: true });

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

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: () => Promise.resolve(errors)
      });

      const result = await generateProject('invalid spec');

      expect(result).toEqual(errors);
    });

    it('returns null on exception', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const result = await generateProject('test');

      expect(result).toBeNull();
    });
  });

  describe('fetchTrajectory', () => {
    it('fetches trajectory successfully', async () => {
      const mockData = {
        steps: [{ action: { next_node: 'test' } }]
      };

      globalThis.fetch = vi.fn().mockResolvedValue({
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
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({})
      });

      await fetchTrajectory('trace', 'session with spaces');

      expect(fetch).toHaveBeenCalledWith(
        '/trajectory/trace?session_id=session%20with%20spaces'
      );
    });

    it('returns null on error', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await fetchTrajectory('trace', 'session');

      expect(result).toBeNull();
    });

    it('returns data on first attempt without retrying', async () => {
      const errorSpy = vi.spyOn(console, 'error');
      const mockData = { steps: [{ action: { next_node: 'n1' } }] };

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(1);
      expect(errorSpy).not.toHaveBeenCalled();
      expect(result).toEqual(mockData);
      errorSpy.mockRestore();
    });

    it('returns data after exactly 1 retry on 404', async () => {
      const errorSpy = vi.spyOn(console, 'error');
      const mockData = { steps: [{ action: { next_node: 'n1' } }] };

      globalThis.fetch = vi.fn()
        .mockResolvedValueOnce({ ok: false, status: 404, statusText: 'Not Found' })
        .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(mockData) });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(2);
      expect(errorSpy).not.toHaveBeenCalled();
      expect(result).toEqual(mockData);
      errorSpy.mockRestore();
    });

    it('returns null after exhausting all retries on persistent 404', async () => {
      const errorSpy = vi.spyOn(console, 'error');

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false, status: 404, statusText: 'Not Found'
      });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(4);
      expect(errorSpy).toHaveBeenCalledTimes(1);
      expect(result).toBeNull();
      errorSpy.mockRestore();
    });

    it('returns null immediately on non-404 error without retrying', async () => {
      const errorSpy = vi.spyOn(console, 'error');

      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false, status: 500, statusText: 'Internal Server Error'
      });

      const result = await fetchTrajectory('trace-1', 'session-1', 3, 0);

      expect(fetch).toHaveBeenCalledTimes(1);
      expect(errorSpy).toHaveBeenCalledTimes(1);
      expect(result).toBeNull();
      errorSpy.mockRestore();
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
      globalThis.document.createElement = mockCreateElement as typeof document.createElement;
      globalThis.document.body.appendChild = mockAppendChild as typeof document.body.appendChild;
      globalThis.document.body.removeChild = mockRemoveChild as typeof document.body.removeChild;
      globalThis.URL.createObjectURL = mockCreateObjectURL as typeof URL.createObjectURL;
      globalThis.URL.revokeObjectURL = mockRevokeObjectURL as typeof URL.revokeObjectURL;
    });

    it('fetches artifact with correct headers', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      globalThis.fetch = vi.fn().mockResolvedValue({
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
      globalThis.fetch = vi.fn().mockResolvedValue({
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
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers({ 'Content-Disposition': 'attachment; filename="server.pdf"' })
      });

      await downloadArtifact('artifact-123', 'session-456', 'custom.pdf');

      expect(mockAnchor.download).toBe('custom.pdf');
    });

    it('falls back to artifact ID when no filename available', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers()
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(mockAnchor.download).toBe('artifact-artifact-123');
    });

    it('throws error on failed response', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
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
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        blob: () => Promise.resolve(mockBlob),
        headers: new Headers()
      });

      await downloadArtifact('artifact-123', 'session-456');

      expect(mockRevokeObjectURL).toHaveBeenCalledWith('blob:http://localhost/test');
    });

    it('removes anchor element after download', async () => {
      const mockBlob = new Blob(['test content'], { type: 'application/pdf' });
      globalThis.fetch = vi.fn().mockResolvedValue({
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

      globalThis.fetch = vi.fn().mockResolvedValue({
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
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 404
      });

      const result = await getArtifactMeta('artifact-123', 'session-456');

      expect(result).toBeNull();
    });

    it('returns null on network failure', async () => {
      globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

      const result = await getArtifactMeta('artifact-123', 'session-456');

      expect(result).toBeNull();
    });
  });

  describe('eval trace tagging api', () => {
    it('lists traces successfully', async () => {
      const mockData = [
        { trace_id: 'trace-1', session_id: 'session-1', tags: ['dataset:alpha'] }
      ];
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await listTraces(25);

      expect(fetch).toHaveBeenCalledWith('/traces?limit=25');
      expect(result).toEqual(mockData);
    });

    it('sets trace tags successfully', async () => {
      const mockData = { trace_id: 'trace-1', session_id: 'session-1', tags: ['dataset:alpha'] };
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await setTraceTags('trace-1', 'session-1', ['dataset:alpha'], ['split:test']);

      expect(fetch).toHaveBeenCalledWith('/traces/trace-1/tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: 'session-1', add: ['dataset:alpha'], remove: ['split:test'] })
      });
      expect(result).toEqual(mockData);
    });

    it('lists eval datasets successfully', async () => {
      const mockData = [
        { path: 'example_app/evals/policy/dataset.jsonl', label: 'policy/dataset.jsonl', is_default: true }
      ];
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await listEvalDatasets();

      expect(fetch).toHaveBeenCalledWith('/eval/datasets/browse');
      expect(result).toEqual(mockData);
    });

    it('returns null when browsing datasets fails', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await listEvalDatasets();

      expect(result).toBeNull();
    });

    it('lists eval metrics successfully', async () => {
      const mockData = [
        {
          metric_spec: 'example_app.evals.metrics:policy_metric',
          label: 'policy_metric',
          source_spec_path: 'example_app/evals/policy/evaluate.spec.json'
        }
      ];
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await listEvalMetrics();

      expect(fetch).toHaveBeenCalledWith('/eval/metrics/browse');
      expect(result).toEqual(mockData);
    });

    it('returns null when browsing metrics fails', async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

      const result = await listEvalMetrics();

      expect(result).toBeNull();
    });

    it('exports eval dataset successfully', async () => {
      const mockData = {
        trace_count: 2,
        dataset_path: '/tmp/project/out/dataset.jsonl',
        manifest_path: '/tmp/project/out/manifest.json'
      };
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await exportEvalDataset({
        include_tags: ['dataset:alpha'],
        output_dir: 'eval/dataset_a'
      });

      expect(fetch).toHaveBeenCalledWith('/eval/datasets/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selector: { include_tags: ['dataset:alpha'], exclude_tags: [], limit: 0 },
          output_dir: 'eval/dataset_a',
          redaction_profile: 'internal_safe'
        })
      });
      expect(result).toEqual(mockData);
    });

    it('loads eval dataset summary successfully', async () => {
      const mockData = {
        dataset_path: '/tmp/project/out/dataset.jsonl',
        counts: { total: 2, by_split: { val: 1, test: 1 } },
        examples: []
      };
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await loadEvalDataset('eval/dataset_a');

      expect(fetch).toHaveBeenCalledWith('/eval/datasets/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset_path: 'eval/dataset_a' })
      });
      expect(result).toEqual(mockData);
    });

    it('runs eval successfully', async () => {
      const mockData = {
        run_id: 'abc123',
        counts: { total: 1, val: 1, test: 0 },
        min_test_score: null,
        passed_threshold: true,
        cases: []
      };
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await runEval({
        dataset_path: 'eval/dataset_a',
        metric_spec: 'my_metric:score',
        min_test_score: 0.7
      });

      expect(fetch).toHaveBeenCalledWith('/eval/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_path: 'eval/dataset_a',
          metric_spec: 'my_metric:score',
          min_test_score: 0.7
        })
      });
      expect(result).toEqual(mockData);
    });

    it('fetches eval case comparison successfully', async () => {
      const mockData = {
        example_id: 'ex-1',
        pred_trace_id: 'trace-1',
        pred_session_id: 'session-1',
        gold_trace_id: 'gold-1',
        gold_trajectory: { steps: [] },
        pred_trajectory: { steps: [] }
      };
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockData)
      });

      const result = await fetchEvalCaseComparison({
        dataset_path: 'eval/dataset_a',
        example_id: 'ex-1',
        pred_trace_id: 'trace-1',
        pred_session_id: 'session-1'
      });

      expect(fetch).toHaveBeenCalledWith('/eval/cases/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_path: 'eval/dataset_a',
          example_id: 'ex-1',
          pred_trace_id: 'trace-1',
          pred_session_id: 'session-1'
        })
      });
      expect(result).toEqual(mockData);
    });
  });
});
