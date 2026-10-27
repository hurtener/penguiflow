import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import RightSidebarHost from './RightSidebarHost.svelte';

describe('RightSidebar layout', () => {
  it('does not include the discarded Eval card', () => {
    render(RightSidebarHost);
    expect(screen.queryByText('Eval')).toBeNull();
  });
});
