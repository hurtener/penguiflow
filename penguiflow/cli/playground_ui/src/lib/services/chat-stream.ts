import type { ChatMessage, ArtifactStoredEvent } from '$lib/types';
import { safeParse } from '$lib/utils';
import { ANSWER_GATE_SENTINEL } from '$lib/utils/constants';
import { chatStore, eventsStore, timelineStore, artifactsStore, componentArtifactsStore } from '$lib/stores';
import { HttpAgent } from '@ag-ui/client';
import type { BaseEvent, Message as AguiMessage, RunAgentInput } from '@ag-ui/core';

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
  private aguiSubscription: { unsubscribe: () => void } | null = null;
  private aguiMessageMap: Map<string, string> = new Map();
  private aguiMappedPlaceholder = false;
  private aguiRunId: string | null = null;
  private aguiSessionId: string | null = null;
  private aguiCompleted = false;
  private aguiToolCalls: Map<string, { name: string; args: string; messageId?: string }> = new Map();

  /**
   * Start a new chat stream
   */
  start(
    query: string,
    sessionId: string,
    toolContext: Record<string, unknown>,
    llmContext: Record<string, unknown>,
    callbacks: ChatStreamCallbacks,
    protocol: 'sse' | 'agui' = 'sse'
  ): void {
    // Close any existing connection
    this.close();

    if (protocol === 'agui') {
      this.startAgui(query, sessionId, toolContext, llmContext, callbacks);
      return;
    }

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
    if (this.aguiSubscription) {
      this.aguiSubscription.unsubscribe();
      this.aguiSubscription = null;
    }
    this.aguiMessageMap.clear();
    this.aguiMappedPlaceholder = false;
    this.aguiRunId = null;
    this.aguiSessionId = null;
    this.aguiCompleted = false;
    this.aguiToolCalls.clear();
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
    if ((data.artifact_type as string) === 'ui_component') {
      componentArtifactsStore.addArtifactChunk(data as any, { message_id: this.agentMsgId ?? undefined });
      eventsStore.addEvent(data, 'artifact_chunk');
      return;
    }
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

    if (eventType === 'tool_call_start') {
      const toolCallId = data.tool_call_id as string | undefined;
      const toolName = data.tool_name as string | undefined;
      const argsJson = data.args_json as string | undefined;
      if (toolCallId && toolName) {
        this.handleInteractiveToolCall(toolCallId, toolName, argsJson ?? '', this.agentMsgId ?? undefined);
      }
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

    if (pause.resume_token) {
      componentArtifactsStore.updatePendingInteraction({
        resume_token: pause.resume_token as string
      });
    }

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

  private handleInteractiveToolCall(
    toolCallId: string,
    toolName: string,
    argsText: string,
    messageId?: string
  ): void {
    const component = this.mapInteractiveComponent(toolName);
    if (!component) return;

    let props: Record<string, unknown> = {};
    if (argsText) {
      try {
        const parsed = JSON.parse(argsText) as Record<string, unknown>;
        if (parsed && typeof parsed === 'object') {
          props = parsed;
        }
      } catch {
        props = {};
      }
    }

    componentArtifactsStore.setPendingInteraction({
      tool_call_id: toolCallId,
      tool_name: toolName,
      component,
      props,
      message_id: messageId,
      created_at: Date.now()
    });
  }

  private mapInteractiveComponent(toolName: string): string | null {
    if (toolName === 'ui_form') return 'form';
    if (toolName === 'ui_confirm') return 'confirm';
    if (toolName === 'ui_select_option') return 'select_option';
    return null;
  }

  private startAgui(
    query: string,
    sessionId: string,
    toolContext: Record<string, unknown>,
    llmContext: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    const history = this.buildHistory();
    const hasUser = history.some(msg => msg.role === 'user' && this.hasTextContent(msg.content));
    if (!hasUser && query.trim()) {
      history.push({
        id: `msg_${Date.now()}`,
        role: 'user',
        content: query
      });
    }

    const agentMsg = chatStore.addAgentMessage();
    this.agentMsgId = agentMsg.id;
    this.aguiMessageMap.clear();
    this.aguiMappedPlaceholder = false;
    this.aguiCompleted = false;

    const url = new URL('/agui/agent', window.location.origin);
    const input: RunAgentInput = {
      threadId: sessionId,
      runId: `run_${Date.now()}`,
      messages: history,
      tools: [],
      context: [],
      state: {},
      forwardedProps: {
        penguiflow: {
          llm_context: llmContext,
          tool_context: toolContext
        }
      }
    } as RunAgentInput;

    this.aguiRunId = input.runId;
    this.aguiSessionId = sessionId;
    console.log('[AG-UI] Sending request:', JSON.stringify(input, null, 2));
    const agent = new HttpAgent({ url: url.toString() });
    // Use run() not runAgent() - run() returns Observable<BaseEvent>
    const observable = agent.run(input);

    this.aguiSubscription = observable.subscribe({
      next: (event: BaseEvent) => this.handleAguiEvent(event, callbacks),
      error: (err: Error) => {
        const msg = this.findAgentMsg();
        if (msg) {
          chatStore.updateMessage(msg.id, { isStreaming: false });
        }
        callbacks.onError(err.message);
        this.close();
      },
      complete: () => {
        if (!this.aguiCompleted && this.aguiRunId) {
          callbacks.onDone(this.aguiRunId);
        }
      }
    });
  }

  resumeAgui(
    interaction: {
      resume_token?: string;
      tool_call_id: string;
      tool_name: string;
      component: string;
    },
    result: unknown,
    sessionId: string,
    toolContext: Record<string, unknown>,
    callbacks: ChatStreamCallbacks
  ): void {
    if (!interaction.resume_token) {
      callbacks.onError('Resume token missing');
      return;
    }

    this.close();

    const agentMsg = chatStore.addAgentMessage();
    this.agentMsgId = agentMsg.id;
    this.aguiMessageMap.clear();
    this.aguiMappedPlaceholder = false;
    this.aguiCompleted = false;

    const url = new URL('/agui/resume', window.location.origin);
    const input = {
      resume_token: interaction.resume_token,
      thread_id: sessionId,
      run_id: `run_${Date.now()}`,
      tool_name: interaction.tool_name,
      component: interaction.component,
      result,
      tool_context: toolContext
    };

    this.aguiRunId = input.run_id;
    this.aguiSessionId = sessionId;

    const agent = new HttpAgent({ url: url.toString() });
    const observable = agent.run(input as any);

    this.aguiSubscription = observable.subscribe({
      next: (event: BaseEvent) => this.handleAguiEvent(event, callbacks),
      error: (err: Error) => {
        const msg = this.findAgentMsg();
        if (msg) {
          chatStore.updateMessage(msg.id, { isStreaming: false });
        }
        callbacks.onError(err.message);
        this.close();
      },
      complete: () => {
        if (!this.aguiCompleted && this.aguiRunId) {
          callbacks.onDone(this.aguiRunId);
        }
      }
    });
  }

  private handleAguiEvent(event: BaseEvent, callbacks: ChatStreamCallbacks): void {
    console.log('[AG-UI] Received event:', event.type, event);
    switch (event.type) {
      case 'RUN_STARTED':
        this.aguiRunId = (event as any).runId ?? this.aguiRunId;
        return;

      case 'RUN_FINISHED': {
        const msg = this.findAgentMsg();
        if (msg) {
          chatStore.updateMessage(msg.id, { isStreaming: false, isThinking: false });
        }
        if (!this.aguiCompleted) {
          callbacks.onDone((event as any).runId ?? this.aguiRunId);
        }
        this.aguiCompleted = true;
        return;
      }

      case 'RUN_ERROR': {
        const msg = this.findAgentMsg();
        if (msg) {
          chatStore.updateMessage(msg.id, { isStreaming: false, isThinking: false });
        }
        if (!this.aguiCompleted) {
          callbacks.onError((event as any).message || 'Run error');
        }
        this.aguiCompleted = true;
        return;
      }

      case 'TEXT_MESSAGE_START': {
        const e = event as any;
        if (e.role !== 'assistant') return;
        this.ensureAguiMessage(e.messageId);
        return;
      }

      case 'TEXT_MESSAGE_CONTENT': {
        const e = event as any;
        const id = this.ensureAguiMessage(e.messageId);
        const msg = chatStore.findMessage(id);
        if (msg && e.delta) {
          chatStore.updateMessage(msg.id, { text: `${msg.text}${e.delta}`, isStreaming: true });
        }
        return;
      }

      case 'TEXT_MESSAGE_END': {
        const e = event as any;
        const id = this.ensureAguiMessage(e.messageId);
        chatStore.updateMessage(id, { isStreaming: false });
        return;
      }

      case 'TOOL_CALL_START': {
        const e = event as any;
        const toolCallId = e.toolCallId as string;
        const toolCallName = e.toolCallName as string;
        const parentMessageId = e.parentMessageId as string | undefined;
        const messageId = parentMessageId ? this.ensureAguiMessage(parentMessageId) : this.agentMsgId ?? undefined;
        this.aguiToolCalls.set(toolCallId, { name: toolCallName, args: '', messageId });
        eventsStore.addEvent(
          { tool_call_id: toolCallId, tool_call_name: toolCallName } as any,
          'tool_call_start'
        );
        return;
      }

      case 'TOOL_CALL_ARGS': {
        const e = event as any;
        const toolCallId = e.toolCallId as string;
        const entry = this.aguiToolCalls.get(toolCallId);
        if (entry) {
          entry.args += (e.delta as string) ?? '';
        }
        eventsStore.addEvent(
          { tool_call_id: toolCallId, delta: e.delta as string } as any,
          'tool_call_args'
        );
        return;
      }

      case 'TOOL_CALL_END': {
        const e = event as any;
        const toolCallId = e.toolCallId as string;
        const entry = this.aguiToolCalls.get(toolCallId);
        if (entry) {
          this.handleInteractiveToolCall(toolCallId, entry.name, entry.args, entry.messageId);
          this.aguiToolCalls.delete(toolCallId);
        }
        eventsStore.addEvent({ tool_call_id: toolCallId } as any, 'tool_call_end');
        return;
      }

      case 'TOOL_CALL_RESULT': {
        const e = event as any;
        const toolCallId = e.toolCallId as string;
        eventsStore.addEvent(
          { tool_call_id: toolCallId, content: e.content as string } as any,
          'tool_call_result'
        );
        return;
      }

      case 'CUSTOM': {
        const name = (event as any).name as string;
        const value = (event as any).value as Record<string, unknown> | string | null;

        if (name === 'artifact_stored' && value && typeof value === 'object' && (value as any).artifact) {
          const artifact = value.artifact as Record<string, unknown>;
          artifactsStore.addArtifact({
            artifact_id: artifact.id as string,
            mime_type: artifact.mime_type as string,
            size_bytes: artifact.size_bytes as number,
            filename: artifact.filename as string,
            source: (artifact.source as Record<string, unknown>) || {},
            trace_id: this.aguiRunId ?? undefined,
            session_id: this.aguiSessionId ?? '',
            ts: Date.now()
          } as ArtifactStoredEvent);
        }

        if (name === 'artifact_chunk' && value && typeof value === 'object') {
          const payload = value as Record<string, unknown>;
          if ((payload.artifact_type as string) === 'ui_component') {
            componentArtifactsStore.addArtifactChunk(payload as any, { message_id: this.agentMsgId ?? undefined });
          } else {
            const streamId = (payload.stream_id as string) ?? 'artifact';
            timelineStore.addArtifactChunk(streamId, payload.chunk);
          }
        }

        // Handle thinking events - show in observations panel
        if (name === 'thinking' && value && typeof value === 'object') {
          const msg = this.findAgentMsg();
          if (msg) {
            const text = (value as any).text as string || '';
            const phase = (value as any).phase as string;
            if (phase === 'action') {
              // Action phase = agent is thinking
              chatStore.updateMessage(msg.id, { isThinking: true });
            } else if (text) {
              // Observation/other thinking content
              chatStore.updateMessage(msg.id, {
                observations: `${msg.observations ?? ''}${text}`,
                showObservations: true,
                isThinking: false
              });
            }
          }
        }

        // Handle revision events
        if (name === 'revision' && value && typeof value === 'object') {
          const msg = this.findAgentMsg();
          if (msg) {
            const text = (value as any).text as string || '';
            const done = (value as any).done as boolean;
            const updates: Partial<ChatMessage> = {
              isThinking: false,
              isStreaming: !done
            };
            if (!msg.revisionStreamActive) {
              updates.revisionStreamActive = true;
              updates.text = '';
            }
            if (text) {
              updates.text = `${msg.revisionStreamActive ? msg.text : ''}${text}`;
            }
            chatStore.updateMessage(msg.id, updates);
          }
        }

        if (name === 'pause' && value && typeof value === 'object') {
          const msg = this.findAgentMsg();
          if (msg) {
            this.handlePause(msg, {}, value as Record<string, unknown>, this.aguiRunId, callbacks);
          }
          const payload = (value as any).payload as Record<string, unknown> | undefined;
          if (payload?.component && payload?.props && !componentArtifactsStore.pendingInteraction) {
            componentArtifactsStore.setPendingInteraction({
              tool_call_id: `pause_${Date.now()}`,
              tool_name: (payload.tool as string) ?? 'ui_pause',
              component: payload.component as string,
              props: (payload.props as Record<string, unknown>) ?? {},
              message_id: this.agentMsgId ?? undefined,
              resume_token: (value as any).resume_token as string | undefined,
              created_at: Date.now()
            });
          } else if ((value as any).resume_token) {
            componentArtifactsStore.updatePendingInteraction({
              resume_token: (value as any).resume_token as string
            });
          }
          this.aguiCompleted = true;
        }

        const payload = value && typeof value === 'object' ? value : { value };
        eventsStore.addEvent(payload, name);
        return;
      }

      default:
        return;
    }
  }

  private ensureAguiMessage(messageId: string): string {
    const existing = this.aguiMessageMap.get(messageId);
    if (existing) {
      return existing;
    }

    if (!this.aguiMappedPlaceholder && this.agentMsgId) {
      this.aguiMappedPlaceholder = true;
      this.aguiMessageMap.set(messageId, this.agentMsgId);
      return this.agentMsgId;
    }

    const newMsg = chatStore.addAgentMessage();
    this.aguiMessageMap.set(messageId, newMsg.id);
    return newMsg.id;
  }

  private buildHistory(): AguiMessage[] {
    console.log('[AG-UI] Building history from chatStore.messages:', chatStore.messages.length, 'messages');
    const history = chatStore.messages
      .filter(msg => msg.role === 'user' || msg.role === 'agent')
      .map(msg => ({
        id: msg.id,
        role: msg.role === 'agent' ? 'assistant' : 'user',
        content: msg.text
      }));
    console.log('[AG-UI] Built history:', history);
    return history;
  }

  private hasTextContent(content: AguiMessage['content']): boolean {
    if (typeof content === 'string') {
      return content.trim().length > 0;
    }
    if (Array.isArray(content)) {
      return content.some(item => typeof item === 'object' && item !== null && 'text' in item);
    }
    return false;
  }
}

export const chatStreamManager = new ChatStreamManager();
