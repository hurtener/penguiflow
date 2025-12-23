import { safeParse } from '$lib/utils';
import { eventsStore, timelineStore } from '$lib/stores';

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

      // Skip llm_stream_chunk to avoid flooding
      if (incomingEvent === 'llm_stream_chunk') return;

      // Add to events
      eventsStore.addEvent(data, incomingEvent || 'event');
    };

    // Register for multiple event types
    ['event', 'step', 'chunk', 'llm_stream_chunk', 'artifact_chunk'].forEach(type => {
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
