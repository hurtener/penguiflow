import type { Result } from '$lib/utils/result';
import { ok, err } from '$lib/utils/result';

export function validateObjectProps(
  props: unknown,
  requiredKeys: string[] = []
): Result<Record<string, unknown>> {
  if (!props || typeof props !== 'object' || Array.isArray(props)) {
    return err(new Error('Props must be an object.'));
  }
  const record = props as Record<string, unknown>;
  for (const key of requiredKeys) {
    if (!(key in record)) {
      return err(new Error(`Missing required prop: ${key}`));
    }
  }
  return ok(record);
}

export function requireString(
  props: Record<string, unknown>,
  key: string
): Result<Record<string, unknown>> {
  const value = props[key];
  if (typeof value !== 'string' || !value.trim()) {
    return err(new Error(`Invalid or missing string prop: ${key}`));
  }
  return ok(props);
}

export function requireRecord(
  props: Record<string, unknown>,
  key: string
): Result<Record<string, unknown>> {
  const value = props[key];
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return err(new Error(`Invalid or missing object prop: ${key}`));
  }
  return ok(props);
}
