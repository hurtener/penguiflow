import { describe, it, expect } from 'vitest';
import { createInteractionsStore } from '$lib/stores';
import type { ArtifactChunkPayload } from '$lib/types';

function uiComponentChunk(
  component: string,
  storeArtifactId?: string,
  overrides: Partial<ArtifactChunkPayload> = {}
): ArtifactChunkPayload {
  return {
    stream_id: storeArtifactId ?? 'stream',
    seq: 0,
    done: true,
    artifact_type: 'ui_component',
    chunk: { id: `${component}-id`, component, props: {}, title: component },
    meta: storeArtifactId ? { artifact_id: storeArtifactId } : {},
    ...overrides
  };
}

describe('interactionsStore.addArtifactChunk dedupe (decision 7)', () => {
  it('renders a store-backed component once when the same artifact_id arrives twice', () => {
    const store = createInteractionsStore();

    store.addArtifactChunk(uiComponentChunk('report', 'artifact-1'), { message_id: 'm1' });
    store.addArtifactChunk(uiComponentChunk('report', 'artifact-1'), { message_id: 'm1' });

    expect(store.artifacts.length).toBe(1);
    expect(store.artifacts[0]?.component).toBe('report');
  });

  it('dedupes regardless of arrival order (inline-first then stored, or stored-first then inline)', () => {
    // Inline-first then stored
    const a = createInteractionsStore();
    a.addArtifactChunk(uiComponentChunk('report', 'artifact-x'), { message_id: 'm1' });
    a.addArtifactChunk(uiComponentChunk('report', 'artifact-x'), { message_id: 'm1' });
    expect(a.artifacts.length).toBe(1);

    // Stored-first then inline (same id, different surrounding meta) still collapses to one
    const b = createInteractionsStore();
    b.addArtifactChunk(
      uiComponentChunk('report', 'artifact-y', { meta: { artifact_id: 'artifact-y', from: 'stored' } }),
      { message_id: 'm1' }
    );
    b.addArtifactChunk(
      uiComponentChunk('report', 'artifact-y', { meta: { artifact_id: 'artifact-y', from: 'inline' } }),
      { message_id: 'm1' }
    );
    expect(b.artifacts.length).toBe(1);
  });

  it('keys strictly on the store artifact_id, not the component id', () => {
    const store = createInteractionsStore();
    // Same store artifact_id but different component chunk ids -> still one render.
    store.addArtifactChunk(
      {
        artifact_type: 'ui_component',
        chunk: { id: 'chunk-a', component: 'report', props: {} },
        meta: { artifact_id: 'artifact-1' }
      },
      { message_id: 'm1' }
    );
    store.addArtifactChunk(
      {
        artifact_type: 'ui_component',
        chunk: { id: 'chunk-b', component: 'report', props: {} },
        meta: { artifact_id: 'artifact-1' }
      },
      { message_id: 'm1' }
    );
    expect(store.artifacts.length).toBe(1);
  });

  it('does NOT dedupe inline-only chunks that carry no store artifact_id', () => {
    const store = createInteractionsStore();
    // `inline` mode: no meta.artifact_id, so each chunk renders (existing behavior preserved).
    store.addArtifactChunk(uiComponentChunk('report'), { message_id: 'm1' });
    store.addArtifactChunk(uiComponentChunk('report'), { message_id: 'm1' });
    expect(store.artifacts.length).toBe(2);
  });

  it('renders distinct store artifact_ids separately', () => {
    const store = createInteractionsStore();
    store.addArtifactChunk(uiComponentChunk('report', 'artifact-1'), { message_id: 'm1' });
    store.addArtifactChunk(uiComponentChunk('metric', 'artifact-2'), { message_id: 'm1' });
    expect(store.artifacts.length).toBe(2);
  });

  it('clear() resets the dedupe set so a previously-rendered id can render again', () => {
    const store = createInteractionsStore();
    store.addArtifactChunk(uiComponentChunk('report', 'artifact-1'), { message_id: 'm1' });
    expect(store.artifacts.length).toBe(1);

    store.clear();
    expect(store.artifacts.length).toBe(0);

    store.addArtifactChunk(uiComponentChunk('report', 'artifact-1'), { message_id: 'm1' });
    expect(store.artifacts.length).toBe(1);
  });
});
