import type { ChatMessage, ArtifactStoredEvent } from '$lib/types';
import { safeParse } from '$lib/utils';
import { ANSWER_GATE_SENTINEL } from '$lib/utils/constants';
import { chatStore, eventsStore, timelineStore, artifactsStore } from '$lib/stores';

export interface ChatStreamCallbacks {
  onDone: (traceId: string | null) => void;
  onError: (error: string) => void;
}

/**
 * Manages the chat EventSource connection
 */
class ChatStreamManager {
  private eventSource: EventSource | null = null;
  private agentMsgId: string | null = null;

  /**
   * Start a new chat stream
   */
  start(
    query: string,
    sessionId: string,
    toolContext: Record<string, unknown>,
    llmContext: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    // Close any existing connection
    this.close();

    // Create agent message placeholder
    const agentMsg = chatStore.addAgentMessage();
    this.agentMsgId = agentMsg.id;

    // Build URL
    const url = new URL('/chat/stream', window.location.origin);
    url.searchParams.set('query', query);
    url.searchParams.set('session_id', sessionId);
    if (Object.keys(toolContext).length) {
      url.searchParams.set('tool_context', JSON.stringify(toolContext));
    }
    if (Object.keys(llmContext).length) {
      url.searchParams.set('llm_context', JSON.stringify(llmContext));
    }

    this.eventSource = new EventSource(url.toString());

    // Register event handlers
    const events = ['chunk', 'artifact_chunk', 'artifact_stored', 'llm_stream_chunk', 'step', 'event', 'done', 'error'];
    events.forEach(eventName => {
      this.eventSource!.addEventListener(eventName, (evt: MessageEvent) => {
        this.handleEvent(eventName, evt, callbacks);
      });
    });

    this.eventSource.onerror = () => {
      const msg = this.findAgentMsg();
      if (msg) {
        chatStore.updateMessage(msg.id, { isStreaming: false });
      }
      callbacks.onError('Connection lost');
      this.close();
    };
  }

  /**
   * Close the EventSource connection
   */
  close(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.agentMsgId = null;
  }

  private findAgentMsg(): ChatMessage | undefined {
    return this.agentMsgId ? chatStore.findMessage(this.agentMsgId) : undefined;
  }

  private handleEvent(
    eventName: string,
    evt: MessageEvent,
    callbacks: ChatStreamCallbacks
  ): void {
    const data = safeParse(evt.data);
    if (!data) return;

    const msg = this.findAgentMsg();
    if (!msg) return;

    switch (eventName) {
      case 'chunk':
      case 'llm_stream_chunk':
        this.handleChunk(msg, data, eventName);
        break;

      case 'artifact_chunk':
        this.handleArtifactChunk(data);
        break;

      case 'artifact_stored':
        this.handleArtifactStored(data);
        break;

      case 'step':
      case 'event':
        this.handleStepEvent(msg, data, eventName);
        break;

      case 'done':
        this.handleDone(msg, data, callbacks);
        break;

      case 'error':
        this.handleError(msg, data, callbacks);
        break;
    }
  }

  private handleChunk(msg: ChatMessage, data: Record<string, unknown>, eventName: string): void {
    const channel = (data.channel as string) ?? 'thinking';
    const phase = (data.phase as string) ?? (eventName === 'chunk' ? 'observation' : undefined);
    const text = (data.text as string) ?? '';
    const done = Boolean(data.done);

    if (channel === 'thinking' && phase === 'action') {
      chatStore.updateMessage(msg.id, { isThinking: !done });
      return;
    }

    if (channel === 'thinking') {
      if (text) {
        chatStore.updateMessage(msg.id, {
          observations: `${msg.observations ?? ''}${text}`,
          showObservations: true,
          isThinking: false
        });
      } else {
        chatStore.updateMessage(msg.id, { isThinking: false });
      }
      return;
    }

    if (channel === 'revision') {
      const updates: Partial<ChatMessage> = {
        isThinking: false,
        isStreaming: true
      };
      if (!msg.revisionStreamActive) {
        updates.revisionStreamActive = true;
        updates.text = '';
      }
      if (text) {
        updates.text = `${msg.revisionStreamActive ? msg.text : ''}${text}`;
      }
      chatStore.updateMessage(msg.id, updates);
      return;
    }

    if (channel === 'answer') {
      const gate = (msg.answerActionSeq ?? ANSWER_GATE_SENTINEL) as number;
      const seq = data.action_seq as number | undefined;

      if (gate === ANSWER_GATE_SENTINEL) {
        chatStore.updateMessage(msg.id, {
          answerStreamDone: done,
          isStreaming: !done,
          isThinking: false
        });
        return;
      }

      if (seq !== undefined && seq !== gate) {
        chatStore.updateMessage(msg.id, { isThinking: false });
        return;
      }

      const updates: Partial<ChatMessage> = {
        isThinking: false,
        isStreaming: !done
      };
      if (text) {
        updates.text = `${msg.text}${text}`;
      }
      if (done) {
        updates.answerStreamDone = true;
      }
      chatStore.updateMessage(msg.id, updates);
      return;
    }

    // Default: append to observations
    if (text) {
      chatStore.updateMessage(msg.id, {
        observations: `${msg.observations ?? ''}${text}`,
        showObservations: true,
        isThinking: false
      });
    } else {
      chatStore.updateMessage(msg.id, { isThinking: false });
    }
  }

  private handleArtifactChunk(data: Record<string, unknown>): void {
    const streamId = (data.stream_id as string) ?? 'artifact';
    timelineStore.addArtifactChunk(streamId, data.chunk);
    eventsStore.addEvent(data, 'artifact_chunk');
  }

  private handleArtifactStored(data: Record<string, unknown>): void {
    // Add to artifacts store for download
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
    // Also add to events for visibility
    eventsStore.addEvent(data, 'artifact_stored');
  }

  private handleStepEvent(
    msg: ChatMessage,
    data: Record<string, unknown>,
    eventName: string
  ): void {
    const eventType = data.event as string;

    if (eventType === 'step_start') {
      const seq = data.action_seq as number | undefined;
      chatStore.updateMessage(msg.id, {
        answerActionSeq: typeof seq === 'number' ? seq : ANSWER_GATE_SENTINEL
      });
    }

    eventsStore.addEvent(data, eventName);
  }

  private handleDone(
    msg: ChatMessage,
    data: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    const pause = data.pause as Record<string, unknown> | undefined;
    const traceId = (data.trace_id as string) ?? null;

    if (pause) {
      this.handlePause(msg, data, pause, traceId, callbacks);
      return;
    }

    // Handle final answer
    const doneActionSeq = data.answer_action_seq as number | undefined;
    const gate = (msg.answerActionSeq ?? ANSWER_GATE_SENTINEL) as number;
    const gateReady = gate !== ANSWER_GATE_SENTINEL;

    if (gateReady && (doneActionSeq === undefined || doneActionSeq === gate)) {
      if (data.answer && typeof data.answer === 'string') {
        chatStore.updateMessage(msg.id, { text: data.answer });
      }
    }

    chatStore.updateMessage(msg.id, {
      isStreaming: false,
      isThinking: false
    });

    callbacks.onDone(traceId);
    this.close();
  }

  private handlePause(
    msg: ChatMessage,
    _data: Record<string, unknown>,
    pause: Record<string, unknown>,
    traceId: string | null,
    callbacks: ChatStreamCallbacks
  ): void {
    const payload = (pause.payload as Record<string, unknown>) ?? {};
    const authUrl = (payload.auth_url as string) || (payload.url as string) || '';
    const provider = (payload.provider as string) || '';
    const reason = (pause.reason as string) || 'pause';

    let body = `Planner paused (${reason})`;
    if (provider) body += ` for ${provider}`;
    if (authUrl) body += `\n[Open auth link](${authUrl})`;
    if (pause.resume_token) body += `\nResume token: \`${pause.resume_token}\``;

    chatStore.updateMessage(msg.id, {
      pause: pause as ChatMessage['pause'],
      traceId: traceId ?? undefined,
      text: body,
      isStreaming: false,
      isThinking: false
    });

    callbacks.onDone(traceId);
    this.close();
  }

  private handleError(
    msg: ChatMessage,
    data: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    const error = (data.error as string) ?? 'Unexpected error';
    chatStore.updateMessage(msg.id, {
      text: error,
      isStreaming: false,
      isThinking: false
    });
    callbacks.onError(error);
    this.close();
  }
}

export const chatStreamManager = new ChatStreamManager();
