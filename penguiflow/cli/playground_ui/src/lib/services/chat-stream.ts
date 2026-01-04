import type {
  ArtifactChunkPayload,
  ArtifactStoredEvent,
  ChatMessage,
  PendingInteraction,
  StateUpdate
} from '$lib/types';
import { safeParse } from '$lib/utils';
import { ANSWER_GATE_SENTINEL } from '$lib/utils/constants';
import type { AppStores } from '$lib/stores';
import { HttpAgent } from '@ag-ui/client';
import {
  EventType,
  type BaseEvent,
  type CustomEvent,
  type Message as AguiMessage,
  type RunAgentInput,
  type RunErrorEvent,
  type RunFinishedEvent,
  type RunStartedEvent,
  type TextMessageContentEvent,
  type TextMessageEndEvent,
  type TextMessageStartEvent,
  type ToolCallArgsEvent,
  type ToolCallEndEvent,
  type ToolCallResultEvent,
  type ToolCallStartEvent
} from '@ag-ui/core';

type ChatStreamStores = Pick<
  AppStores,
  'chatStore'
  | 'eventsStore'
  | 'trajectoryStore'
  | 'artifactsStore'
  | 'interactionsStore'
  | 'tasksStore'
  | 'notificationsStore'
>;

type AguiStreamEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | RunErrorEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | ToolCallResultEvent
  | CustomEvent;

export interface ChatStreamCallbacks {
  onDone: (traceId: string | null) => void;
  onError: (error: string) => void;
}

/**
 * Manages the chat EventSource connection
 */
class ChatStreamManager {
  constructor(private stores: ChatStreamStores) {}
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
    const agentMsg = this.stores.chatStore.addAgentMessage();
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
    const events = [
      'chunk',
      'artifact_chunk',
      'artifact_stored',
      'llm_stream_chunk',
      'step',
      'event',
      'state_update',
      'done',
      'error'
    ];
    events.forEach(eventName => {
      this.eventSource!.addEventListener(eventName, (evt: MessageEvent) => {
        this.handleEvent(eventName, evt, callbacks);
      });
    });

    this.eventSource.onerror = () => {
      const msg = this.findAgentMsg();
      if (msg) {
        this.stores.chatStore.updateMessage(msg.id, { isStreaming: false });
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
    return this.agentMsgId ? this.stores.chatStore.findMessage(this.agentMsgId) : undefined;
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

      case 'state_update':
        this.handleStateUpdate(data);
        break;

      case 'done':
        this.handleDone(msg, data, callbacks);
        break;

      case 'error':
        this.handleError(msg, data, callbacks);
        break;
    }
  }

  private handleStateUpdate(data: Record<string, unknown>): void {
    this.stores.tasksStore.applyUpdate(data as StateUpdate);
    const updateType = data.update_type as string | undefined;
    if (updateType === 'NOTIFICATION') {
      const content = data.content as Record<string, unknown> | undefined;
      const severity = String(content?.severity ?? 'info');
      const body = String(content?.body ?? '');
      const title = String(content?.title ?? '');
      const message = title ? `${title}: ${body}` : body;
      const actionsRaw = content?.actions;
      const actions = Array.isArray(actionsRaw)
        ? actionsRaw
            .filter(item => item && typeof item === 'object')
            .map(item => ({
              id: String((item as Record<string, unknown>).id ?? ''),
              label: String((item as Record<string, unknown>).label ?? 'Action'),
              payload: (item as Record<string, unknown>).payload as Record<string, unknown> | undefined
            }))
            .filter(item => item.id)
        : undefined;
      this.stores.notificationsStore.add(message || 'Notification', severity as any, actions);
    }
  }

  private handleChunk(msg: ChatMessage, data: Record<string, unknown>, eventName: string): void {
    const channel = (data.channel as string) ?? 'thinking';
    const phase = (data.phase as string) ?? (eventName === 'chunk' ? 'observation' : undefined);
    const text = (data.text as string) ?? '';
    const done = Boolean(data.done);

    if (channel === 'thinking' && phase === 'action') {
      this.stores.chatStore.updateMessage(msg.id, { isThinking: !done });
      return;
    }

    if (channel === 'thinking') {
      if (text) {
        this.stores.chatStore.updateMessage(msg.id, {
          observations: `${msg.observations ?? ''}${text}`,
          showObservations: true,
          isThinking: false
        });
      } else {
        this.stores.chatStore.updateMessage(msg.id, { isThinking: false });
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
      this.stores.chatStore.updateMessage(msg.id, updates);
      return;
    }

    if (channel === 'answer') {
      const gate = (msg.answerActionSeq ?? ANSWER_GATE_SENTINEL) as number;
      const seq = data.action_seq as number | undefined;

      if (gate === ANSWER_GATE_SENTINEL) {
        this.stores.chatStore.updateMessage(msg.id, {
          answerStreamDone: done,
          isStreaming: !done,
          isThinking: false
        });
        return;
      }

      if (seq !== undefined && seq !== gate) {
        this.stores.chatStore.updateMessage(msg.id, { isThinking: false });
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
      this.stores.chatStore.updateMessage(msg.id, updates);
      return;
    }

    // Default: append to observations
    if (text) {
      this.stores.chatStore.updateMessage(msg.id, {
        observations: `${msg.observations ?? ''}${text}`,
        showObservations: true,
        isThinking: false
      });
    } else {
      this.stores.chatStore.updateMessage(msg.id, { isThinking: false });
    }
  }

  private handleArtifactChunk(data: Record<string, unknown>): void {
    const payload = toArtifactChunkPayload(data);
    if (payload.artifact_type === 'ui_component') {
      this.stores.interactionsStore.addArtifactChunk(payload, { message_id: this.agentMsgId ?? undefined });
      this.stores.eventsStore.addEvent(data, 'artifact_chunk');
      return;
    }
    const streamId = payload.stream_id ?? 'artifact';
    this.stores.trajectoryStore.addArtifactChunk(streamId, payload.chunk);
    this.stores.eventsStore.addEvent(data, 'artifact_chunk');
  }

  private handleArtifactStored(data: Record<string, unknown>): void {
    const stored = toArtifactStoredEvent(data);
    if (stored) {
      this.stores.artifactsStore.addArtifact(stored);
    }
    // Also add to events for visibility
    this.stores.eventsStore.addEvent(data, 'artifact_stored');
  }

  private handleStepEvent(
    msg: ChatMessage,
    data: Record<string, unknown>,
    eventName: string
  ): void {
    const eventType = data.event as string;

    if (eventType === 'step_start') {
      const seq = data.action_seq as number | undefined;
      this.stores.chatStore.updateMessage(msg.id, {
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

    this.stores.eventsStore.addEvent(data, eventName);
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
        this.stores.chatStore.updateMessage(msg.id, { text: data.answer });
      }
    }

    this.stores.chatStore.updateMessage(msg.id, {
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
    const payload = asRecord(pause.payload) ?? {};
    const authUrl = getString(payload.auth_url) || getString(payload.url) || '';
    const provider = getString(payload.provider) || '';
    const reason = getString(pause.reason) || 'pause';
    const resumeToken = getString(pause.resume_token);

    if (resumeToken) {
      this.stores.interactionsStore.updatePendingInteraction({ resume_token: resumeToken });
    }

    let body = `Planner paused (${reason})`;
    if (provider) body += ` for ${provider}`;
    if (authUrl) body += `\n[Open auth link](${authUrl})`;
    if (resumeToken) body += `\nResume token: \`${resumeToken}\``;

    this.stores.chatStore.updateMessage(msg.id, {
      pause: { reason, payload, resume_token: resumeToken },
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
    this.stores.chatStore.updateMessage(msg.id, {
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

    this.stores.interactionsStore.setPendingInteraction({
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

    const agentMsg = this.stores.chatStore.addAgentMessage();
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
    };

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
          this.stores.chatStore.updateMessage(msg.id, { isStreaming: false });
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
    interaction: PendingInteraction,
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

    const agentMsg = this.stores.chatStore.addAgentMessage();
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
    const observable = agent.run(input as unknown as RunAgentInput);

    this.aguiSubscription = observable.subscribe({
      next: (event: BaseEvent) => this.handleAguiEvent(event, callbacks),
      error: (err: Error) => {
        const msg = this.findAgentMsg();
        if (msg) {
          this.stores.chatStore.updateMessage(msg.id, { isStreaming: false });
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
    const typedEvent = event as AguiStreamEvent;
    console.log('[AG-UI] Received event:', typedEvent.type, typedEvent);
    switch (typedEvent.type) {
      case EventType.RUN_STARTED: {
        const e = typedEvent as RunStartedEvent;
        this.aguiRunId = e.runId ?? this.aguiRunId;
        return;
      }

      case EventType.RUN_FINISHED: {
        const e = typedEvent as RunFinishedEvent;
        const msg = this.findAgentMsg();
        if (msg) {
          this.stores.chatStore.updateMessage(msg.id, { isStreaming: false, isThinking: false });
        }
        if (!this.aguiCompleted) {
          callbacks.onDone(e.runId ?? this.aguiRunId);
        }
        this.aguiCompleted = true;
        return;
      }

      case EventType.RUN_ERROR: {
        const e = typedEvent as RunErrorEvent;
        const msg = this.findAgentMsg();
        if (msg) {
          this.stores.chatStore.updateMessage(msg.id, { isStreaming: false, isThinking: false });
        }
        if (!this.aguiCompleted) {
          callbacks.onError(e.message || 'Run error');
        }
        this.aguiCompleted = true;
        return;
      }

      case EventType.TEXT_MESSAGE_START: {
        const e = typedEvent as TextMessageStartEvent;
        if (e.role !== 'assistant') return;
        this.ensureAguiMessage(e.messageId);
        return;
      }

      case EventType.TEXT_MESSAGE_CONTENT: {
        const e = typedEvent as TextMessageContentEvent;
        const id = this.ensureAguiMessage(e.messageId);
        const msg = this.stores.chatStore.findMessage(id);
        if (msg && e.delta) {
          this.stores.chatStore.updateMessage(msg.id, { text: `${msg.text}${e.delta}`, isStreaming: true });
        }
        return;
      }

      case EventType.TEXT_MESSAGE_END: {
        const e = typedEvent as TextMessageEndEvent;
        const id = this.ensureAguiMessage(e.messageId);
        this.stores.chatStore.updateMessage(id, { isStreaming: false });
        return;
      }

      case EventType.TOOL_CALL_START: {
        const e = typedEvent as ToolCallStartEvent;
        const toolCallId = e.toolCallId;
        const toolCallName = e.toolCallName;
        const parentMessageId = e.parentMessageId;
        const messageId = parentMessageId ? this.ensureAguiMessage(parentMessageId) : this.agentMsgId ?? undefined;
        this.aguiToolCalls.set(toolCallId, { name: toolCallName, args: '', messageId });
        this.stores.eventsStore.addEvent(
          { tool_call_id: toolCallId, tool_call_name: toolCallName },
          'tool_call_start'
        );
        return;
      }

      case EventType.TOOL_CALL_ARGS: {
        const e = typedEvent as ToolCallArgsEvent;
        const toolCallId = e.toolCallId;
        const entry = this.aguiToolCalls.get(toolCallId);
        if (entry) {
          entry.args += e.delta ?? '';
        }
        this.stores.eventsStore.addEvent(
          { tool_call_id: toolCallId, delta: e.delta },
          'tool_call_args'
        );
        return;
      }

      case EventType.TOOL_CALL_END: {
        const e = typedEvent as ToolCallEndEvent;
        const toolCallId = e.toolCallId;
        const entry = this.aguiToolCalls.get(toolCallId);
        if (entry) {
          this.handleInteractiveToolCall(toolCallId, entry.name, entry.args, entry.messageId);
          this.aguiToolCalls.delete(toolCallId);
        }
        this.stores.eventsStore.addEvent({ tool_call_id: toolCallId }, 'tool_call_end');
        return;
      }

      case EventType.TOOL_CALL_RESULT: {
        const e = typedEvent as ToolCallResultEvent;
        const toolCallId = e.toolCallId;
        this.stores.eventsStore.addEvent(
          { tool_call_id: toolCallId, content: e.content },
          'tool_call_result'
        );
        return;
      }

      case EventType.CUSTOM: {
        const e = typedEvent as CustomEvent;
        const name = e.name;
        const value = e.value;
        const record = asRecord(value);

        if (name === 'artifact_stored' && record) {
          const artifactRecord = asRecord(record.artifact);
          const stored = artifactRecord
            ? toArtifactStoredEventFromCustom(artifactRecord, this.aguiRunId, this.aguiSessionId)
            : null;
          if (stored) {
            this.stores.artifactsStore.addArtifact(stored);
          }
        }

        if (name === 'artifact_chunk' && record) {
          const payload = toArtifactChunkPayload(record);
          if (payload.artifact_type === 'ui_component') {
            this.stores.interactionsStore.addArtifactChunk(payload, { message_id: this.agentMsgId ?? undefined });
          } else {
            const streamId = payload.stream_id ?? 'artifact';
            this.stores.trajectoryStore.addArtifactChunk(streamId, payload.chunk);
          }
        }

        // Handle thinking events - show in observations panel
        if (name === 'thinking' && record) {
          const msg = this.findAgentMsg();
          if (msg) {
            const text = getString(record.text) ?? '';
            const phase = getString(record.phase);
            if (phase === 'action') {
              this.stores.chatStore.updateMessage(msg.id, { isThinking: true });
            } else if (text) {
              this.stores.chatStore.updateMessage(msg.id, {
                observations: `${msg.observations ?? ''}${text}`,
                showObservations: true,
                isThinking: false
              });
            }
          }
        }

        // Handle revision events
        if (name === 'revision' && record) {
          const msg = this.findAgentMsg();
          if (msg) {
            const text = getString(record.text) ?? '';
            const done = getBoolean(record.done) ?? false;
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
            this.stores.chatStore.updateMessage(msg.id, updates);
          }
        }

        if (name === 'pause' && record) {
          const msg = this.findAgentMsg();
          if (msg) {
            this.handlePause(msg, {}, record, this.aguiRunId, callbacks);
          }
          const payload = asRecord(record.payload);
          const component = payload ? getString(payload.component) : undefined;
          const props = payload ? asRecord(payload.props) : undefined;
          if (payload && component && props && !this.stores.interactionsStore.pendingInteraction) {
            this.stores.interactionsStore.setPendingInteraction({
              tool_call_id: `pause_${Date.now()}`,
              tool_name: getString(payload.tool) ?? 'ui_pause',
              component,
              props,
              message_id: this.agentMsgId ?? undefined,
              resume_token: getString(record.resume_token),
              created_at: Date.now()
            });
          } else if (getString(record.resume_token)) {
            this.stores.interactionsStore.updatePendingInteraction({
              resume_token: getString(record.resume_token)
            });
          }
          this.aguiCompleted = true;
        }

        const payload = record ?? { value };
        this.stores.eventsStore.addEvent(payload, name);
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

    const newMsg = this.stores.chatStore.addAgentMessage();
    this.aguiMessageMap.set(messageId, newMsg.id);
    return newMsg.id;
  }

  private buildHistory(): AguiMessage[] {
    console.log('[AG-UI] Building history from this.stores.chatStore.messages:', this.stores.chatStore.messages.length, 'messages');
    const history: AguiMessage[] = this.stores.chatStore.messages
      .filter((msg: ChatMessage) => msg.role === 'user' || msg.role === 'agent')
      .map((msg: ChatMessage): AguiMessage => ({
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

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function getString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function getNumber(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

function getBoolean(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

function toArtifactChunkPayload(data: Record<string, unknown>): ArtifactChunkPayload {
  return {
    stream_id: getString(data.stream_id),
    seq: getNumber(data.seq),
    done: getBoolean(data.done),
    artifact_type: getString(data.artifact_type),
    chunk: data.chunk,
    meta: asRecord(data.meta) ?? undefined,
    ts: getNumber(data.ts)
  };
}

function toArtifactStoredEvent(data: Record<string, unknown>): ArtifactStoredEvent | null {
  const artifact_id = getString(data.artifact_id);
  const mime_type = getString(data.mime_type);
  const size_bytes = getNumber(data.size_bytes);
  const filename = getString(data.filename);
  const trace_id = getString(data.trace_id);
  const session_id = getString(data.session_id);
  const ts = getNumber(data.ts);
  if (!artifact_id || !mime_type || size_bytes === undefined || !filename || !trace_id || !session_id || ts === undefined) {
    return null;
  }
  return {
    artifact_id,
    mime_type,
    size_bytes,
    filename,
    source: asRecord(data.source) ?? {},
    trace_id,
    session_id,
    ts
  };
}

function toArtifactStoredEventFromCustom(
  artifact: Record<string, unknown>,
  traceId: string | null,
  sessionId: string | null
): ArtifactStoredEvent | null {
  if (!traceId || !sessionId) return null;
  const artifact_id = getString(artifact.id);
  const mime_type = getString(artifact.mime_type);
  const size_bytes = getNumber(artifact.size_bytes);
  const filename = getString(artifact.filename);
  if (!artifact_id || !mime_type || size_bytes === undefined || !filename) {
    return null;
  }
  return {
    artifact_id,
    mime_type,
    size_bytes,
    filename,
    source: asRecord(artifact.source) ?? {},
    trace_id: traceId,
    session_id: sessionId,
    ts: Date.now()
  };
}

export function createChatStreamManager(stores: ChatStreamStores): ChatStreamManager {
  return new ChatStreamManager(stores);
}
