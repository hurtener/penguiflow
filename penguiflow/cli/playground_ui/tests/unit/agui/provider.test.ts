import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/svelte';

vi.mock('@ag-ui/client', () => {
  class HttpAgentMock {
    runAgent = vi.fn();
  }
  return { HttpAgent: HttpAgentMock };
});

import AguiProviderHost from './AguiProviderHost.svelte';

describe('AGUIProvider', () => {
  it('provides context to children', () => {
    const { getByText } = render(AguiProviderHost);
    expect(getByText('No messages yet')).toBeInTheDocument();
  });
});
