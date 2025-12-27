import { describe, it, expect } from 'vitest';
import { applyJsonPatch } from '$lib/agui/patch';

describe('applyJsonPatch', () => {
  it('applies JSON Patch operations', () => {
    const doc = { status: 'idle', count: 1 };
    const result = applyJsonPatch(doc, [
      { op: 'replace', path: '/status', value: 'running' },
      { op: 'add', path: '/newField', value: 42 }
    ]);

    expect(result).toEqual({ status: 'running', count: 1, newField: 42 });
  });
});
