import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import McpAppWorkspace from '$lib/components/features/mcp-app-workspace/McpAppWorkspace.svelte';
import type { ComponentArtifact } from '$lib/types';

describe('McpAppWorkspace', () => {
  const artifact: ComponentArtifact = {
    id: 'mcp-app-1',
    component: 'mcp_app',
    title: 'Deck Editor',
    props: {
      html: '<!doctype html><html><body><main>Editor</main></body></html>',
      namespace: 'pengui_slides',
      tool_input: { deck_id: 'deck-123' },
    },
    seq: 1,
    ts: Date.now(),
    meta: {}
  };

  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      writable: true,
      value: 1400,
    });
  });

  it('renders a compact header and iframe surface', async () => {
    render(McpAppWorkspace, {
      props: {
        artifact,
        widthPx: 680,
        onClose: vi.fn(),
        onWidthChange: vi.fn(),
      }
    });

    expect(screen.getByText('Interactive App')).toBeInTheDocument();
    expect(screen.queryByText('Deck Editor')).not.toBeInTheDocument();
    expect(screen.queryByText('pengui_slides')).not.toBeInTheDocument();
    expect(screen.queryByText('deck-123')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(document.querySelector('.mcp-app-frame')).toBeInTheDocument();
    });
  });

  it('calls onClose when close button is clicked', async () => {
    const onClose = vi.fn();
    render(McpAppWorkspace, {
      props: {
        artifact,
        widthPx: 680,
        onClose,
        onWidthChange: vi.fn(),
      }
    });

    await fireEvent.click(screen.getByLabelText('Close MCP app viewer'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('emits width changes from the resize handle', async () => {
    const onWidthChange = vi.fn();
    render(McpAppWorkspace, {
      props: {
        artifact,
        widthPx: 680,
        onClose: vi.fn(),
        onWidthChange,
      }
    });

    const handle = screen.getByLabelText('Resize MCP app viewer');
    await fireEvent.mouseDown(handle, { clientX: 900 });
    await fireEvent.mouseMove(window, { clientX: 880 });
    await fireEvent.mouseUp(window);

    expect(onWidthChange).toHaveBeenCalledWith(520);
  });
});
