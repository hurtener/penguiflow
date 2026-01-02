export type Result<T, E = Error> =
  | { ok: true; data: T }
  | { ok: false; error: E };

export function ok<T>(data: T): Result<T> {
  return { ok: true, data };
}

export function err<E extends Error>(error: E): Result<never, E> {
  return { ok: false, error };
}

export function toError(value: unknown, fallback = 'Unknown error'): Error {
  if (value instanceof Error) return value;
  if (typeof value === 'string' && value.trim()) return new Error(value);
  return new Error(fallback);
}
