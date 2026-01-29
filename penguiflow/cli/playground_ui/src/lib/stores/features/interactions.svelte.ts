import { getContext, setContext } from 'svelte';
import type { ArtifactChunkPayload, ComponentArtifact, PendingInteraction } from '$lib/types';

const INTERACTIONS_STORE_KEY = Symbol('interactions-store');

export interface InteractionsStore {
  readonly artifacts: ComponentArtifact[];
  readonly pendingInteraction: PendingInteraction | null;
  readonly lastArtifact: ComponentArtifact | null;
  addArtifactChunk(
    payload: ArtifactChunkPayload,
    options?: { message_id?: string }
  ): void;
  setPendingInteraction(value: PendingInteraction | null): void;
  updatePendingInteraction(update: Partial<PendingInteraction>): void;
  clearPendingInteraction(): void;
  clear(): void;
}

export function createInteractionsStore(): InteractionsStore {
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
    const component = typeof chunk.component === 'string' ? chunk.component : undefined;
    const props = (chunk.props as Record<string, unknown>) || {};
    if (!component) return;

    const artifact: ComponentArtifact = {
      id: (typeof chunk.id === 'string' ? chunk.id : undefined) || `ui_${Date.now()}`,
      component,
      props,
      title: typeof chunk.title === 'string' ? chunk.title : undefined,
      message_id: message_id ?? (typeof payload.meta?.message_id === 'string' ? payload.meta.message_id : undefined),
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

export function setInteractionsStore(
  store: InteractionsStore = createInteractionsStore()
): InteractionsStore {
  setContext(INTERACTIONS_STORE_KEY, store);
  return store;
}

export function getInteractionsStore(): InteractionsStore {
  return getContext<InteractionsStore>(INTERACTIONS_STORE_KEY);
}
