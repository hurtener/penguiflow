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

export type McpAppArtifact = ComponentArtifact & {
  component: 'mcp_app';
};

export type McpAppMessageRequest = {
  text: string;
  namespace?: string | null;
  modelContext?: Record<string, unknown>;
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

export function isMcpAppArtifact(artifact: ComponentArtifact | null | undefined): artifact is McpAppArtifact {
  return artifact?.component === 'mcp_app';
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

export function getMcpAppNamespace(artifact: ComponentArtifact | null | undefined): string | null {
  if (!artifact) return null;
  return firstString(artifact.props?.namespace, artifact.meta?.namespace);
}

export function getMcpAppTitle(artifact: ComponentArtifact | null | undefined): string {
  if (!artifact) return 'MCP App';
  return (
    firstString(
      artifact.title,
      artifact.props?.title,
      artifact.props?.name,
      artifact.props?.label,
      artifact.meta?.title,
      getMcpAppNamespace(artifact),
    ) ?? 'MCP App'
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function mergeRecords(
  base: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (isRecord(value) && isRecord(merged[key])) {
      merged[key] = mergeRecords(merged[key] as Record<string, unknown>, value);
      continue;
    }
    merged[key] = value;
  }
  return merged;
}

export function mergeMcpAppLlmContext(
  base: Record<string, unknown>,
  request: McpAppMessageRequest,
): Record<string, unknown> {
  const next = { ...base };
  const modelContext = isRecord(request.modelContext) ? request.modelContext : {};
  const existingAppContext = isRecord(next.mcp_app) ? next.mcp_app : {};

  next.mcp_app = mergeRecords(existingAppContext, {
    ...(request.namespace ? { namespace: request.namespace } : {}),
    ...(Object.keys(modelContext).length ? { model_context: modelContext } : {}),
  });

  return next;
}
