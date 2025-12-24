import { describe, it, expect } from 'vitest';
import { safeParse, parseJsonObject } from '$lib/utils/json';

describe('safeParse', () => {
  it('parses valid JSON object', () => {
    const result = safeParse('{"a": 1, "b": "two"}');
    expect(result).toEqual({ a: 1, b: 'two' });
  });

  it('parses valid JSON array', () => {
    const result = safeParse('[1, 2, 3]');
    expect(result).toEqual([1, 2, 3]);
  });

  it('returns null for invalid JSON', () => {
    expect(safeParse('not json')).toBeNull();
  });

  it('returns null for empty string', () => {
    expect(safeParse('')).toBeNull();
  });

  it('returns null for malformed JSON', () => {
    expect(safeParse('{a: 1}')).toBeNull();
  });
});

describe('parseJsonObject', () => {
  it('parses valid object JSON', () => {
    const result = parseJsonObject('{"key": "value", "num": 42}', { label: 'Test' });
    expect(result).toEqual({ key: 'value', num: 42 });
  });

  it('returns empty object for empty string', () => {
    expect(parseJsonObject('', { label: 'Test' })).toEqual({});
  });

  it('returns empty object for whitespace-only string', () => {
    expect(parseJsonObject('   ', { label: 'Test' })).toEqual({});
  });

  it('throws for array JSON', () => {
    expect(() => parseJsonObject('[1, 2, 3]', { label: 'Config' }))
      .toThrow('Config must be a JSON object.');
  });

  it('throws for null JSON', () => {
    expect(() => parseJsonObject('null', { label: 'Settings' }))
      .toThrow('Settings must be a JSON object.');
  });

  it('throws for invalid JSON', () => {
    expect(() => parseJsonObject('not json', { label: 'Data' }))
      .toThrow('Data must be valid JSON.');
  });

  it('throws for primitive JSON', () => {
    expect(() => parseJsonObject('"string"', { label: 'Value' }))
      .toThrow('Value must be a JSON object.');
  });

  it('handles nested objects', () => {
    const input = '{"outer": {"inner": "value"}}';
    const result = parseJsonObject(input, { label: 'Nested' });
    expect(result).toEqual({ outer: { inner: 'value' } });
  });
});
