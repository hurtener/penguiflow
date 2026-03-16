import type {
  MetaResponse,
  SpecData,
  ValidationResult,
  TrajectoryPayload,
  ArtifactRef,
  ComponentRegistryPayload,
  TaskState,
  TraceSummary,
  EvalDatasetExportResponse,
  EvalDatasetLoadResponse,
  EvalRunResponse,
  EvalDatasetBrowseEntry,
  EvalMetricBrowseEntry,
  EvalCaseComparisonResponse
} from '$lib/types';
import type { Result } from '$lib/utils/result';

const BASE_URL = '';  // Same origin

export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
    public details?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function fetchWithErrorHandling<T>(
  url: string,
  options?: RequestInit
): Promise<Result<T, ApiError>> {
  try {
    const response = options ? await fetch(url, options) : await fetch(url);
    if (!response.ok) {
      return { ok: false, error: new ApiError(response.statusText || 'Request failed', response.status) };
    }
    const data = await response.json();
    return { ok: true, data: data as T };
  } catch (err) {
    return { ok: false, error: new ApiError('Network error', 0, err) };
  }
}

/**
 * Extract filename from Content-Disposition header.
 * Supports both `filename="name"` and `filename=name` formats.
 */
export function extractFilename(header: string | null): string | null {
  if (!header) return null;
  // Try quoted format first: filename="name.ext"
  const quotedMatch = header.match(/filename="(.+?)"/);
  const quoted = quotedMatch?.[1];
  if (quoted) return quoted;
  // Try unquoted format: filename=name.ext
  const unquotedMatch = header.match(/filename=([^;\s]+)/);
  const unquoted = unquotedMatch?.[1];
  if (unquoted) return unquoted;
  return null;
}

/**
 * Load agent metadata, config, services, and tool catalog
 */
export async function loadMeta(): Promise<MetaResponse | null> {
  const result = await fetchWithErrorHandling<MetaResponse>(`${BASE_URL}/ui/meta`);
  if (!result.ok) {
    console.error('meta load failed', result.error);
    return null;
  }
  return result.data;
}

/**
 * Load component registry for rich output lab
 */
export async function loadComponentRegistry(): Promise<ComponentRegistryPayload | null> {
  const result = await fetchWithErrorHandling<ComponentRegistryPayload>(`${BASE_URL}/ui/components`);
  if (!result.ok) {
    console.error('component registry load failed', result.error);
    return null;
  }
  return result.data;
}

/**
 * Load spec content and validation status
 */
export async function loadSpec(): Promise<SpecData | null> {
  const result = await fetchWithErrorHandling<SpecData>(`${BASE_URL}/ui/spec`);
  if (!result.ok) {
    console.error('spec load failed', result.error);
    return null;
  }
  return result.data;
}

/**
 * Validate spec content
 */
export async function validateSpec(specText: string): Promise<ValidationResult | null> {
  const result = await fetchWithErrorHandling<ValidationResult>(`${BASE_URL}/ui/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spec_text: specText })
  });
  if (!result.ok) {
    console.error('validate failed', result.error);
    return null;
  }
  return result.data;
}

/**
 * Generate project from spec
 * @returns true on success, array of errors on failure, null on exception
 */
export async function generateProject(
  specText: string
): Promise<true | Array<{ message: string; line?: number | null }> | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_text: specText })
    });
    if (!resp.ok) {
      return await resp.json();
    }
    return true;
  } catch (err) {
    console.error('generate failed', err);
    return null;
  }
}

/**
 * Fetch execution trajectory for a trace
 */
export async function fetchTrajectory(
  traceId: string,
  sessionId: string,
  retries = 3,
  delayMs = 500
): Promise<TrajectoryPayload | null> {
  const url = `${BASE_URL}/trajectory/${traceId}?session_id=${encodeURIComponent(sessionId)}`;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const result = await fetchWithErrorHandling<TrajectoryPayload>(url);
    if (result.ok) return result.data;
    if (result.error.statusCode !== 404 || attempt === retries) {
      console.error('trajectory fetch failed', result.error);
      return null;
    }
    await new Promise(r => setTimeout(r, delayMs * (attempt + 1)));
  }
  return null;
}

/**
 * Download artifact binary as blob and trigger browser download.
 * @param artifactId - The artifact ID to download
 * @param sessionId - Current session ID for authentication
 * @param filename - Optional filename override (uses Content-Disposition or fallback if not provided)
 * @throws Error if download fails
 */
export async function downloadArtifact(
  artifactId: string,
  sessionId: string,
  filename?: string
): Promise<void> {
  const response = await fetch(`${BASE_URL}/artifacts/${artifactId}`, {
    headers: {
      'X-Session-ID': sessionId
    }
  });

  if (!response.ok) {
    throw new Error(`Download failed: ${response.status} ${response.statusText}`);
  }

  const blob = await response.blob();
  const contentDisposition = response.headers.get('Content-Disposition');
  const inferredFilename = filename
    || extractFilename(contentDisposition)
    || `artifact-${artifactId}`;

  // Trigger browser download
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = inferredFilename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Get artifact metadata without downloading content.
 * @param artifactId - The artifact ID to get metadata for
 * @param sessionId - Current session ID for authentication
 * @returns Artifact metadata or null on failure
 */
export async function getArtifactMeta(
  artifactId: string,
  sessionId: string
): Promise<ArtifactRef | null> {
  const result = await fetchWithErrorHandling<ArtifactRef>(`${BASE_URL}/artifacts/${artifactId}/meta`, {
    headers: {
      'X-Session-ID': sessionId
    }
  });
  if (!result.ok) {
    console.error('artifact meta fetch failed', result.error);
    return null;
  }
  return result.data;
}

/**
 * List artifacts for a session (for hydration on session resume).
 * @param sessionId - The session ID to list artifacts for
 * @param tenantId - Optional tenant ID for scope filtering
 * @param userId - Optional user ID for scope filtering
 * @returns Array of ArtifactRef objects, or null on failure
 */
export async function listArtifacts(
  sessionId: string,
  tenantId?: string,
  userId?: string,
): Promise<ArtifactRef[] | null> {
  const url = new URL(`${BASE_URL}/artifacts`, window.location.origin);
  url.searchParams.set('session_id', sessionId);
  if (tenantId) {
    url.searchParams.set('tenant_id', tenantId);
  }
  if (userId) {
    url.searchParams.set('user_id', userId);
  }
  const result = await fetchWithErrorHandling<ArtifactRef[]>(url.toString());
  if (!result.ok) {
    console.error('artifacts list failed', result.error);
    return null;
  }
  return result.data;
}

export async function steerTask(
  sessionId: string,
  taskId: string,
  eventType: string,
  payload: Record<string, unknown> = {},
  source = 'user'
): Promise<boolean> {
  const result = await fetchWithErrorHandling<{ accepted: boolean }>(`${BASE_URL}/steer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      task_id: taskId,
      event_type: eventType,
      payload,
      source
    })
  });
  if (!result.ok) {
    console.error('steer task failed', result.error);
    return false;
  }
  return Boolean(result.data.accepted);
}

export async function listTasks(sessionId: string, status?: string): Promise<TaskState[] | null> {
  const url = new URL(`${BASE_URL}/tasks`, window.location.origin);
  url.searchParams.set('session_id', sessionId);
  if (status) {
    url.searchParams.set('status', status);
  }
  const result = await fetchWithErrorHandling<TaskState[]>(url.toString());
  if (!result.ok) {
    console.error('tasks fetch failed', result.error);
    return null;
  }
  return result.data;
}

export async function applyContextPatch(
  sessionId: string,
  patchId: string,
  action: 'apply' | 'reject' = 'apply'
): Promise<boolean> {
  const result = await fetchWithErrorHandling<{ ok: boolean }>(
    `${BASE_URL}/sessions/${sessionId}/apply-context-patch`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patch_id: patchId, action })
    }
  );
  if (!result.ok) {
    console.error('apply context patch failed', result.error);
    return false;
  }
  return Boolean(result.data.ok);
}

export async function listTraces(limit = 50): Promise<TraceSummary[] | null> {
  const result = await fetchWithErrorHandling<TraceSummary[]>(`${BASE_URL}/traces?limit=${encodeURIComponent(String(limit))}`);
  if (!result.ok) {
    console.error('traces list failed', result.error);
    return null;
  }
  return result.data;
}

export async function setTraceTags(
  traceId: string,
  sessionId: string,
  add: string[] = [],
  remove: string[] = []
): Promise<TraceSummary | null> {
  const result = await fetchWithErrorHandling<TraceSummary>(`${BASE_URL}/traces/${encodeURIComponent(traceId)}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, add, remove })
  });
  if (!result.ok) {
    console.error('set trace tags failed', result.error);
    return null;
  }
  return result.data;
}

export async function exportEvalDataset(params: {
  include_tags: string[];
  output_dir: string;
  redaction_profile?: string;
  exclude_tags?: string[];
  limit?: number;
}): Promise<EvalDatasetExportResponse | null> {
  const result = await fetchWithErrorHandling<EvalDatasetExportResponse>(`${BASE_URL}/eval/datasets/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      selector: {
        include_tags: params.include_tags,
        exclude_tags: params.exclude_tags ?? [],
        limit: params.limit ?? 0
      },
      output_dir: params.output_dir,
      redaction_profile: params.redaction_profile ?? 'internal_safe'
    })
  });
  if (!result.ok) {
    console.error('eval dataset export failed', result.error);
    return null;
  }
  return result.data;
}

export async function listEvalDatasets(): Promise<EvalDatasetBrowseEntry[] | null> {
  const result = await fetchWithErrorHandling<EvalDatasetBrowseEntry[]>(`${BASE_URL}/eval/datasets/browse`);
  if (!result.ok) {
    console.error('eval dataset browse failed', result.error);
    return null;
  }
  return result.data;
}

export async function listEvalMetrics(): Promise<EvalMetricBrowseEntry[] | null> {
  const result = await fetchWithErrorHandling<EvalMetricBrowseEntry[]>(`${BASE_URL}/eval/metrics/browse`);
  if (!result.ok) {
    console.error('eval metric browse failed', result.error);
    return null;
  }
  return result.data;
}

export async function loadEvalDataset(datasetPath: string): Promise<EvalDatasetLoadResponse | null> {
  const result = await fetchWithErrorHandling<EvalDatasetLoadResponse>(`${BASE_URL}/eval/datasets/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_path: datasetPath })
  });
  if (!result.ok) {
    console.error('eval dataset load failed', result.error);
    return null;
  }
  return result.data;
}

export async function runEval(payload: {
  dataset_path: string;
  metric_spec: string;
  min_test_score?: number;
  max_cases?: number;
}): Promise<EvalRunResponse | null> {
  const result = await fetchWithErrorHandling<EvalRunResponse>(`${BASE_URL}/eval/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!result.ok) {
    console.error('eval run failed', result.error);
    return null;
  }
  return result.data;
}

export async function fetchEvalCaseComparison(payload: {
  dataset_path: string;
  example_id: string;
  pred_trace_id: string;
  pred_session_id: string;
}): Promise<EvalCaseComparisonResponse | null> {
  const result = await fetchWithErrorHandling<EvalCaseComparisonResponse>(`${BASE_URL}/eval/cases/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!result.ok) {
    console.error('eval case comparison failed', result.error);
    return null;
  }
  return result.data;
}
