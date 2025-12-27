import { safeParse } from '$lib/utils';
import { eventsStore, timelineStore, artifactsStore } from '$lib/stores';
import type { ArtifactStoredEvent } from '$lib/types';

/**
 * Manages the follow EventSource for live event updates
 */
class EventStreamManager {
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
      if (!eventsStore.shouldProcess(incomingEvent)) return;

      // Handle artifact chunks
      if (incomingEvent === 'artifact_chunk') {
        const streamId = (data.stream_id as string) ?? 'artifact';
        timelineStore.addArtifactChunk(streamId, data.chunk);
      }

      // Handle artifact_stored - add to artifacts store for download
      if (incomingEvent === 'artifact_stored') {
        artifactsStore.addArtifact({
          artifact_id: data.artifact_id as string,
          mime_type: data.mime_type as string,
          size_bytes: data.size_bytes as number,
          filename: data.filename as string,
          source: (data.source as Record<string, unknown>) || {},
          trace_id: data.trace_id as string,
          session_id: data.session_id as string,
          ts: data.ts as number
        } as ArtifactStoredEvent);
      }

      // Skip llm_stream_chunk to avoid flooding
      if (incomingEvent === 'llm_stream_chunk') return;

      // Add to events
      eventsStore.addEvent(data, incomingEvent || 'event');
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

export const eventStreamManager = new EventStreamManager();
