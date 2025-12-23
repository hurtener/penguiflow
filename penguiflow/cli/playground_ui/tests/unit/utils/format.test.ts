import { describe, it, expect } from 'vitest';
import { formatTime, randomId } from '$lib/utils/format';

describe('formatTime', () => {
  it('formats timestamp to HH:MM format', () => {
    const ts = new Date('2024-01-15T14:30:00').getTime();
    const result = formatTime(ts);
    // Result should be in HH:MM format (locale-dependent)
    expect(result).toMatch(/^\d{1,2}:\d{2}/);
  });

  it('handles midnight correctly', () => {
    const ts = new Date('2024-01-15T00:00:00').getTime();
    const result = formatTime(ts);
    expect(result).toMatch(/^(12:00|00:00)/);
  });

  it('handles noon correctly', () => {
    const ts = new Date('2024-01-15T12:00:00').getTime();
    const result = formatTime(ts);
    expect(result).toMatch(/12:00/);
  });
});

describe('randomId', () => {
  it('returns a string', () => {
    const id = randomId();
    expect(typeof id).toBe('string');
  });

  it('returns a UUID-like string (mocked)', () => {
    const id = randomId();
    expect(id).toMatch(/^test-uuid-/);
  });

  it('returns unique values on each call', () => {
    const id1 = randomId();
    const id2 = randomId();
    expect(id1).not.toBe(id2);
  });
});
