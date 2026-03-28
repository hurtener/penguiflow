import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import StepDetails from '$lib/components/features/trajectory/StepDetails.svelte';

describe('StepDetails', () => {
  it('renders object args/result with JSON inspector', () => {
    render(StepDetails, {
      args: { route: 'bug', confidence: 0.95 },
      result: { status: 'ok', diagnostics: { api: 'degraded' } }
    });

    expect(screen.getAllByText(/Object\(/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('route')).toBeTruthy();
    expect(screen.getByText('diagnostics')).toBeTruthy();
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
