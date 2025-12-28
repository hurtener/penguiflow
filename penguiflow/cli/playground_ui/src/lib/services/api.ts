import type { MetaResponse, SpecData, ValidationResult, TrajectoryPayload, ArtifactRef, ComponentRegistryPayload } from '$lib/types';

const BASE_URL = '';  // Same origin

/**
 * Extract filename from Content-Disposition header.
 * Supports both `filename="name"` and `filename=name` formats.
 */
export function extractFilename(header: string | null): string | null {
  if (!header) return null;
  // Try quoted format first: filename="name.ext"
  const quotedMatch = header.match(/filename="(.+?)"/);
  if (quotedMatch) return quotedMatch[1];
  // Try unquoted format: filename=name.ext
  const unquotedMatch = header.match(/filename=([^;\s]+)/);
  if (unquotedMatch) return unquotedMatch[1];
  return null;
}

/**
 * Load agent metadata, config, services, and tool catalog
 */
export async function loadMeta(): Promise<MetaResponse | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/meta`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('meta load failed', err);
    return null;
  }
}

/**
 * Load component registry for rich output lab
 */
export async function loadComponentRegistry(): Promise<ComponentRegistryPayload | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/components`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('component registry load failed', err);
    return null;
  }
}

/**
 * Load spec content and validation status
 */
export async function loadSpec(): Promise<SpecData | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/spec`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('spec load failed', err);
    return null;
  }
}

/**
 * Validate spec content
 */
export async function validateSpec(specText: string): Promise<ValidationResult | null> {
  try {
    const resp = await fetch(`${BASE_URL}/ui/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec_text: specText })
    });
    return await resp.json();
  } catch (err) {
    console.error('validate failed', err);
    return null;
  }
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
  sessionId: string
): Promise<TrajectoryPayload | null> {
  try {
    const resp = await fetch(
      `${BASE_URL}/trajectory/${traceId}?session_id=${encodeURIComponent(sessionId)}`
    );
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    console.error('trajectory fetch failed', err);
    return null;
  }
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
  try {
    const response = await fetch(`${BASE_URL}/artifacts/${artifactId}/meta`, {
      headers: {
        'X-Session-ID': sessionId
      }
    });

    if (!response.ok) {
      console.error('artifact meta fetch failed', response.status);
      return null;
    }

    return await response.json();
  } catch (err) {
    console.error('artifact meta fetch failed', err);
    return null;
  }
}
