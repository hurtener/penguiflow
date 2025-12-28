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
