import { describe, expect, it, beforeEach } from 'vitest';
import { createLayoutStore } from '$lib/stores';
import type { ComponentArtifact } from '$lib/types';

describe('layoutStore MCP app workspace', () => {
  const store = createLayoutStore();

  const firstArtifact: ComponentArtifact = {
    id: 'app-1',
    component: 'mcp_app',
    props: { namespace: 'pengui_slides' },
    seq: 1,
    ts: 1,
  };

  const secondArtifact: ComponentArtifact = {
    id: 'app-2',
    component: 'mcp_app',
    props: { namespace: 'tableau' },
    seq: 2,
    ts: 2,
  };

  beforeEach(() => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      writable: true,
      value: 1400,
    });
    store.reset();
  });

  it('opens an MCP app workspace', () => {
    store.openMcpApp(firstArtifact);

    expect(store.isMcpAppOpen).toBe(true);
    expect(store.activeMcpApp?.id).toBe('app-1');
  });

  it('replaces the current app when a new one opens', () => {
    store.openMcpApp(firstArtifact);
    store.openMcpApp(secondArtifact);

    expect(store.activeMcpApp?.id).toBe('app-2');
    expect(store.isMcpAppOpen).toBe(true);
  });

  it('clamps workspace width within desktop bounds', () => {
    store.setMcpAppWidth(200);
    expect(store.mcpAppWidthPx).toBe(420);

    store.setMcpAppWidth(2000);
    expect(store.mcpAppWidthPx).toBe(Math.max(420, Math.floor(window.innerWidth * 0.7)));
  });

  it('closes the workspace and clears the active app', () => {
    store.openMcpApp(firstArtifact);
    store.closeMcpApp();

    expect(store.isMcpAppOpen).toBe(false);
    expect(store.activeMcpApp).toBeNull();
  });

  it('does not auto-open the same app again after dismissal', () => {
    store.openMcpApp(firstArtifact);
    store.closeMcpApp();

    expect(store.dismissedMcpAppId).toBe('app-1');
    expect(store.canAutoOpenMcpApp(firstArtifact)).toBe(false);
    expect(store.canAutoOpenMcpApp(secondArtifact)).toBe(true);
  });
});
