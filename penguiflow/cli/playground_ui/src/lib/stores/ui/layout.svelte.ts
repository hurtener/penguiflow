import { getContext, setContext } from 'svelte';

const LAYOUT_STORE_KEY = Symbol('layout-store');

export interface LayoutStore {
  isMobile: boolean;
  sidebarOpen: boolean;
  setMobile(value: boolean): void;
  toggleSidebar(): void;
  reset(): void;
}

export function createLayoutStore(): LayoutStore {
  let isMobile = $state(false);
  let sidebarOpen = $state(false);

  return {
    get isMobile() { return isMobile; },
    set isMobile(value: boolean) { isMobile = value; },
    get sidebarOpen() { return sidebarOpen; },
    set sidebarOpen(value: boolean) { sidebarOpen = value; },
    setMobile(value: boolean) {
      isMobile = value;
      if (!value) {
        sidebarOpen = false;
      }
    },
    toggleSidebar() {
      sidebarOpen = !sidebarOpen;
    },
    reset() {
      isMobile = false;
      sidebarOpen = false;
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
