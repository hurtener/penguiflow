import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import StepDetails from '$lib/components/features/trajectory/StepDetails.svelte';

describe('StepDetails', () => {
  it('renders object args/result as plain code blocks', () => {
    const { container } = render(StepDetails, {
      args: { route: 'bug', confidence: 0.95 },
      result: { status: 'ok', diagnostics: { api: 'degraded' } }
    });

    expect(screen.queryByText(/Object\(/)).toBeNull();
    const blocks = Array.from(container.querySelectorAll('pre')).map((node) => node.textContent ?? '');
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toContain('"route": "bug"');
    expect(blocks[1]).toContain('"diagnostics": {');
    expect(screen.getByText('args')).toBeTruthy();
    expect(screen.getByText('result')).toBeTruthy();
  });

  it('keeps string payload rendering compactly', () => {
    render(StepDetails, {
      args: 'plain args payload',
      result: 'plain result payload'
    });

    expect(screen.getByText('args')).toBeTruthy();
    expect(screen.getByText('result')).toBeTruthy();
    expect(screen.getByText('plain args payload')).toBeTruthy();
    expect(screen.getByText('plain result payload')).toBeTruthy();
  });
});
