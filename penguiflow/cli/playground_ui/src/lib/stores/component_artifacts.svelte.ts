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

function createComponentArtifactsStore() {
  let artifacts = $state<ComponentArtifact[]>([]);
  let pendingInteraction = $state<PendingInteraction | null>(null);
  let lastArtifact = $state<ComponentArtifact | null>(null);

  function addArtifactChunk(
    payload: ArtifactChunkPayload,
    {
      message_id,
    }: {
      message_id?: string;
    } = {}
  ): void {
    if (payload.artifact_type !== 'ui_component') return;
    if (!payload.chunk || typeof payload.chunk !== 'object') return;
    const chunk = payload.chunk as Record<string, unknown>;
    const component = chunk.component as string | undefined;
    const props = (chunk.props as Record<string, unknown>) || {};
    if (!component) return;

    const artifact: ComponentArtifact = {
      id: (chunk.id as string) || `ui_${Date.now()}`,
      component,
      props,
      title: chunk.title as string | undefined,
      message_id: message_id ?? (payload.meta?.message_id as string | undefined),
      seq: payload.seq ?? 0,
      ts: payload.ts ?? Date.now(),
      meta: payload.meta ?? {}
    };

    artifacts = [...artifacts, artifact];
    lastArtifact = artifact;
  }

  function setPendingInteraction(value: PendingInteraction | null): void {
    pendingInteraction = value;
  }

  function updatePendingInteraction(update: Partial<PendingInteraction>): void {
    if (!pendingInteraction) return;
    pendingInteraction = { ...pendingInteraction, ...update };
  }

  function clearPendingInteraction(): void {
    pendingInteraction = null;
  }

  return {
    get artifacts() { return artifacts; },
    get pendingInteraction() { return pendingInteraction; },
    get lastArtifact() { return lastArtifact; },
    addArtifactChunk,
    setPendingInteraction,
    updatePendingInteraction,
    clearPendingInteraction,
    clear() {
      artifacts = [];
      pendingInteraction = null;
      lastArtifact = null;
    }
  };
}

export const componentArtifactsStore = createComponentArtifactsStore();
