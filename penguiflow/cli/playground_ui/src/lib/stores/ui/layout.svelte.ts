import { getContext, setContext } from 'svelte';
import type { ComponentArtifact } from '$lib/types';

const LAYOUT_STORE_KEY = Symbol('layout-store');

const DEFAULT_MCP_APP_WIDTH_RATIO = 0.48;
const MIN_MCP_APP_WIDTH_PX = 420;
const MAX_MCP_APP_WIDTH_RATIO = 0.7;

function viewportWidth(): number {
  if (typeof window !== 'undefined' && Number.isFinite(window.innerWidth) && window.innerWidth > 0) {
    return window.innerWidth;
  }
  return 1440;
}

function maxMcpAppWidthPx(): number {
  return Math.max(MIN_MCP_APP_WIDTH_PX, Math.floor(viewportWidth() * MAX_MCP_APP_WIDTH_RATIO));
}

function defaultMcpAppWidthPx(): number {
  return Math.round(viewportWidth() * DEFAULT_MCP_APP_WIDTH_RATIO);
}

function clampMcpAppWidth(width: number): number {
  return Math.min(Math.max(Math.round(width), MIN_MCP_APP_WIDTH_PX), maxMcpAppWidthPx());
}

export interface LayoutStore {
  isMobile: boolean;
  sidebarOpen: boolean;
  activeMcpApp: ComponentArtifact | null;
  isMcpAppOpen: boolean;
  mcpAppWidthPx: number;
  dismissedMcpAppId: string | null;
  setMobile(value: boolean): void;
  toggleSidebar(): void;
  canAutoOpenMcpApp(artifact: ComponentArtifact | null | undefined): boolean;
  openMcpApp(artifact: ComponentArtifact): void;
  closeMcpApp(): void;
  setMcpAppWidth(px: number): void;
  resetMcpAppWorkspace(): void;
  reset(): void;
}

export function createLayoutStore(): LayoutStore {
  let isMobile = $state(false);
  let sidebarOpen = $state(false);
  let activeMcpApp = $state<ComponentArtifact | null>(null);
  let isMcpAppOpen = $state(false);
  let mcpAppWidthPx = $state(clampMcpAppWidth(defaultMcpAppWidthPx()));
  let dismissedMcpAppId = $state<string | null>(null);

  return {
    get isMobile() { return isMobile; },
    set isMobile(value: boolean) { isMobile = value; },
    get sidebarOpen() { return sidebarOpen; },
    set sidebarOpen(value: boolean) { sidebarOpen = value; },
    get activeMcpApp() { return activeMcpApp; },
    get isMcpAppOpen() { return isMcpAppOpen; },
    get mcpAppWidthPx() { return mcpAppWidthPx; },
    get dismissedMcpAppId() { return dismissedMcpAppId; },
    setMobile(value: boolean) {
      isMobile = value;
      if (!value) {
        sidebarOpen = false;
      }
      if (!value && isMcpAppOpen) {
        mcpAppWidthPx = clampMcpAppWidth(mcpAppWidthPx);
      }
    },
    toggleSidebar() {
      sidebarOpen = !sidebarOpen;
    },
    canAutoOpenMcpApp(artifact: ComponentArtifact | null | undefined) {
      if (!artifact) return false;
      if (dismissedMcpAppId === artifact.id) return false;
      if (isMcpAppOpen && activeMcpApp?.id === artifact.id) return false;
      return true;
    },
    openMcpApp(artifact: ComponentArtifact) {
      activeMcpApp = artifact;
      isMcpAppOpen = true;
      dismissedMcpAppId = null;
      mcpAppWidthPx = clampMcpAppWidth(mcpAppWidthPx || defaultMcpAppWidthPx());
    },
    closeMcpApp() {
      dismissedMcpAppId = activeMcpApp?.id ?? dismissedMcpAppId;
      activeMcpApp = null;
      isMcpAppOpen = false;
    },
    setMcpAppWidth(px: number) {
      if (!Number.isFinite(px)) return;
      mcpAppWidthPx = clampMcpAppWidth(px);
    },
    resetMcpAppWorkspace() {
      activeMcpApp = null;
      isMcpAppOpen = false;
      dismissedMcpAppId = null;
      mcpAppWidthPx = clampMcpAppWidth(defaultMcpAppWidthPx());
    },
    reset() {
      isMobile = false;
      sidebarOpen = false;
      activeMcpApp = null;
      isMcpAppOpen = false;
      dismissedMcpAppId = null;
      mcpAppWidthPx = clampMcpAppWidth(defaultMcpAppWidthPx());
    }
  };
}

export function setLayoutStore(store: LayoutStore = createLayoutStore()): LayoutStore {
  setContext(LAYOUT_STORE_KEY, store);
  return store;
}

export function getLayoutStore(): LayoutStore {
  return getContext<LayoutStore>(LAYOUT_STORE_KEY);
}
