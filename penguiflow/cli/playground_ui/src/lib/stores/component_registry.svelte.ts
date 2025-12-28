export type ComponentExample = {
  description?: string;
  props?: Record<string, unknown>;
};

export type ComponentDefinition = {
  name: string;
  description: string;
  propsSchema: Record<string, unknown>;
  interactive: boolean;
  category: string;
  tags?: string[];
  example?: ComponentExample;
};

export type ComponentRegistryPayload = {
  version: string;
  enabled: boolean;
  allowlist: string[];
  components: Record<string, ComponentDefinition>;
};

function createComponentRegistryStore() {
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

export const componentRegistryStore = createComponentRegistryStore();
