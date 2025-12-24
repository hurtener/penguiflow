/**
 * Safely parse JSON, returning null on failure
 */
export const safeParse = (raw: string): Record<string, unknown> | null => {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
};

/**
 * Parse JSON and validate it's an object (not array/null)
 * @throws Error if invalid
 */
export const parseJsonObject = (
  raw: string,
  options: { label: string }
): Record<string, unknown> => {
  const { label } = options;
  const trimmed = raw.trim();
  if (!trimmed) return {};

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }

  if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} must be a JSON object.`);
  }

  return parsed as Record<string, unknown>;
};
