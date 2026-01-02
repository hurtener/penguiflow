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

export type ComponentArtifact = {
  id: string;
  component: string;
  props: Record<string, unknown>;
  title?: string;
  message_id?: string;
  seq: number;
  ts: number;
  meta?: Record<string, unknown>;
};

export type ArtifactChunkPayload = {
  stream_id?: string;
  seq?: number;
  done?: boolean;
  artifact_type?: string;
  chunk?: unknown;
  meta?: Record<string, unknown>;
  ts?: number;
};

export type PendingInteraction = {
  tool_call_id: string;
  tool_name: string;
  component: string;
  props: Record<string, unknown>;
  message_id?: string;
  resume_token?: string;
  created_at: number;
};
