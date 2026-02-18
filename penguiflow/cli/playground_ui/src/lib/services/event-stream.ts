import { safeParse } from '$lib/utils';
import type { ArtifactChunkPayload, ArtifactStoredEvent } from '$lib/types';
import type { AppStores } from '$lib/stores';

type EventStreamStores = Pick<
  AppStores,
  'eventsStore' | 'trajectoryStore' | 'artifactsStore' | 'interactionsStore'
>;

/**
 * Manages the follow EventSource for live event updates
 */
class EventStreamManager {
  constructor(private stores: EventStreamStores) {}
  private eventSource: EventSource | null = null;

  /**
   * Start following events for a trace
   */
  start(traceId: string, sessionId: string): void {
    this.close();

    const url = new URL('/events', window.location.origin);
    url.searchParams.set('trace_id', traceId);
    url.searchParams.set('session_id', sessionId);
    url.searchParams.set('follow', 'true');

    this.eventSource = new EventSource(url.toString());

    const listener = (evt: MessageEvent) => {
      const data = safeParse(evt.data);
      if (!data) return;

      const incomingEvent = (evt.type as string) || (data.event as string) || '';

      // Check if we should process this event
      if (!this.stores.eventsStore.shouldProcess(incomingEvent)) return;

      // Handle artifact chunks
      if (incomingEvent === 'artifact_chunk') {
        const payload = toArtifactChunkPayload(data);
        if (payload.artifact_type === 'ui_component') {
          this.stores.interactionsStore.addArtifactChunk(payload, {});
        } else {
          const streamId = payload.stream_id ?? 'artifact';
          this.stores.trajectoryStore.addArtifactChunk(streamId, payload.chunk);
        }
      }

      // Handle artifact_stored - add to artifacts store for download
      if (incomingEvent === 'artifact_stored') {
        const stored = toArtifactStoredEvent(data);
        if (stored) {
          this.stores.artifactsStore.addArtifact(stored);
        }
      }

      // Skip llm_stream_chunk to avoid flooding
      if (incomingEvent === 'llm_stream_chunk') return;

      // Add to events
      this.stores.eventsStore.addEvent(data, incomingEvent || 'event');
    };

    // Register for multiple event types
    ['event', 'step', 'chunk', 'llm_stream_chunk', 'artifact_chunk', 'artifact_stored'].forEach(type => {
      this.eventSource!.addEventListener(type, listener);
    });

    this.eventSource.onmessage = listener;
    this.eventSource.onerror = () => this.close();
  }

  /**
   * Close the EventSource connection
   */
  close(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }
}

function toArtifactChunkPayload(data: Record<string, unknown>): ArtifactChunkPayload {
  const meta = data.meta;
  return {
    stream_id: typeof data.stream_id === 'string' ? data.stream_id : undefined,
    seq: typeof data.seq === 'number' ? data.seq : undefined,
    done: typeof data.done === 'boolean' ? data.done : undefined,
    artifact_type: typeof data.artifact_type === 'string' ? data.artifact_type : undefined,
    chunk: data.chunk,
    meta: meta && typeof meta === 'object' && !Array.isArray(meta) ? (meta as Record<string, unknown>) : undefined,
    ts: typeof data.ts === 'number' ? data.ts : undefined
  };
}

function toNumberLike(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === 'string' && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function toArtifactStoredEvent(data: Record<string, unknown>): ArtifactStoredEvent | null {
  const artifact_id = typeof data.artifact_id === 'string' ? data.artifact_id : null;
  if (!artifact_id) {
    return null;
  }
  const mime_type = typeof data.mime_type === 'string' ? data.mime_type : 'application/octet-stream';
  const size_bytes = toNumberLike(data.size_bytes) ?? 0;
  const filename = typeof data.filename === 'string' && data.filename.trim().length > 0
    ? data.filename
    : artifact_id;
  const trace_id = typeof data.trace_id === 'string' ? data.trace_id : 'unknown';
  const session_id = typeof data.session_id === 'string' ? data.session_id : 'unknown';
  const ts = toNumberLike(data.ts) ?? Date.now();
  const source = data.source;
  return {
    artifact_id,
    mime_type,
    size_bytes,
    filename,
    source: source && typeof source === 'object' && !Array.isArray(source) ? (source as Record<string, unknown>) : {},
    trace_id,
    session_id,
    ts
  };
}

export function createEventStreamManager(stores: EventStreamStores): EventStreamManager {
  return new EventStreamManager(stores);
}
