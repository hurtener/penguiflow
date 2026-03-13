import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import ChatCardEvalTabHost from './ChatCardEvalTabHost.svelte';
import * as api from '$lib/services/api';

vi.mock('$lib/services/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('$lib/services/api')>();
  return {
    ...original,
    listTraces: vi.fn()
  };
});

describe('ChatCard Eval tab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listTraces).mockResolvedValue([]);
  });

  it('renders an Eval tab and shows eval surface when selected', async () => {
    render(ChatCardEvalTabHost);

    const evalTab = screen.getByRole('button', { name: 'Eval' });
    expect(evalTab).toBeTruthy();

    await fireEvent.click(evalTab);
    expect(screen.getByText('Trace Selection')).toBeTruthy();
  });
});
