import { getContext, setContext } from 'svelte';
import type { ComponentDefinition, ComponentRegistryPayload } from '$lib/types';

const COMPONENT_REGISTRY_STORE_KEY = Symbol('component-registry-store');

export interface ComponentRegistryStore {
  readonly registry: ComponentRegistryPayload | null;
  readonly error: string | null;
  readonly enabled: boolean;
  readonly components: Record<string, ComponentDefinition>;
  readonly version: string;
  readonly allowlist: string[];
  setFromPayload(payload: ComponentRegistryPayload): void;
  setError(message: string): void;
  getComponent(name: string): ComponentDefinition | undefined;
  reset(): void;
}

export function createComponentRegistryStore(): ComponentRegistryStore {
  let registry = $state<ComponentRegistryPayload | null>(null);
  let error = $state<string | null>(null);

  return {
    get registry() { return registry; },
    get error() { return error; },
    get enabled() { return registry?.enabled ?? false; },
    get components() { return registry?.components ?? {}; },
    get version() { return registry?.version ?? ''; },
    get allowlist() { return registry?.allowlist ?? []; },

    setFromPayload(payload: ComponentRegistryPayload) {
      registry = payload;
      error = null;
    },

    setError(message: string) {
      error = message;
    },

    getComponent(name: string): ComponentDefinition | undefined {
      return registry?.components?.[name];
    },

    reset() {
      registry = null;
      error = null;
    }
  };
}

export function setComponentRegistryStore(
  store: ComponentRegistryStore = createComponentRegistryStore()
): ComponentRegistryStore {
  setContext(COMPONENT_REGISTRY_STORE_KEY, store);
  return store;
}

export function getComponentRegistryStore(): ComponentRegistryStore {
  return getContext<ComponentRegistryStore>(COMPONENT_REGISTRY_STORE_KEY);
}
