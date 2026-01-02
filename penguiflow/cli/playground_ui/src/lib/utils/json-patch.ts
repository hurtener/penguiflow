import { applyPatch, type Operation } from 'fast-json-patch';

export function applyJsonPatch<T>(document: T, patch: Operation[]): T {
  const result = applyPatch(document, patch, true, false);
  return result.newDocument as T;
}
