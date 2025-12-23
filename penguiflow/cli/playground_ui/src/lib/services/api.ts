import type { MetaResponse, SpecData, ValidationResult, TrajectoryPayload } from '$lib/types';

const BASE_URL = '';  // Same origin

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
