import { getContext, setContext } from 'svelte';
import type { ArtifactChunkPayload, ComponentArtifact, PendingInteraction } from '$lib/types';
import { isMcpAppArtifact } from '$lib/types';

const INTERACTIONS_STORE_KEY = Symbol('interactions-store');

export interface InteractionsStore {
  readonly artifacts: ComponentArtifact[];
  readonly pendingInteraction: PendingInteraction | null;
  readonly lastArtifact: ComponentArtifact | null;
  readonly latestMcpAppArtifact: ComponentArtifact | null;
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
  // Dedupe state (decision 7, Phase 008): track rendered components by the opaque store
  // `artifact_id` so a `both`-mode component delivered via BOTH the inline `artifact_chunk` and the
  // `artifact_stored` frame renders ONCE, regardless of which arrives first. Keyed STRICTLY on the
  // store id (carried in `meta.artifact_id`), never on the component's own `id`/`component_id`.
  // Not reactive: it gates inserts but is never read by the UI.
  const renderedArtifactIds = new Set<string>();

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

    // Strict dedupe on the opaque store artifact_id (decision 7). Survives both arrival orders
    // because the inline (`both`) chunk and the store-backed frame both carry the same id in
    // `meta.artifact_id`. Inline-only (`inline` mode) chunks have no store id, so they are never
    // deduped here and keep their existing behavior.
    const storeArtifactId =
      typeof payload.meta?.artifact_id === 'string' ? payload.meta.artifact_id : undefined;
    if (storeArtifactId) {
      if (renderedArtifactIds.has(storeArtifactId)) return;
      renderedArtifactIds.add(storeArtifactId);
    }

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
    get latestMcpAppArtifact() {
      for (let index = artifacts.length - 1; index >= 0; index -= 1) {
        const artifact = artifacts[index];
        if (isMcpAppArtifact(artifact)) {
          return artifact;
        }
      }
      return null;
    },
    addArtifactChunk,
    setPendingInteraction,
    updatePendingInteraction,
    clearPendingInteraction,
    clear() {
      artifacts = [];
      pendingInteraction = null;
      lastArtifact = null;
      renderedArtifactIds.clear();
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
