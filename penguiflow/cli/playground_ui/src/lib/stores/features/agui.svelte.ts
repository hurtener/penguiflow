/**
 * AG-UI Svelte stores wrapping @ag-ui/client HttpAgent.
 */
import { derived, get, writable, type Readable } from 'svelte/store';
import { getContext, setContext } from 'svelte';
import { HttpAgent } from '@ag-ui/client';
import {
  EventType,
  type BaseEvent,
  type CustomEvent,
  type Message,
  type MessagesSnapshotEvent,
  type RunAgentInput,
  type RunErrorEvent,
  type RunFinishedEvent,
  type RunStartedEvent,
  type StateDeltaEvent,
  type StateSnapshotEvent,
  type StepFinishedEvent,
  type StepStartedEvent,
  type TextMessageContentEvent,
  type TextMessageEndEvent,
  type TextMessageStartEvent,
  type Tool,
  type ToolCallArgsEvent,
  type ToolCallEndEvent,
  type ToolCallResultEvent,
  type ToolCallStartEvent
} from '@ag-ui/core';
import { applyJsonPatch } from '$lib/utils/json-patch';

export type RunStatus = 'idle' | 'running' | 'finished' | 'error';

export interface StreamingMessage {
  id: string;
  role: Message['role'];
  content: string;
  isStreaming: boolean;
  toolCalls: StreamingToolCall[];
}

export interface StreamingToolCall {
  id: string;
  name: string;
  arguments: string;
  isStreaming: boolean;
  result?: string;
}

export interface ActiveStep {
  name: string;
  startedAt: Date;
  metadata?: Record<string, unknown>;
}

export interface AGUIStoreState {
  status: RunStatus;
  threadId: string | null;
  runId: string | null;
  messages: StreamingMessage[];
  agentState: Record<string, unknown>;
  activeSteps: ActiveStep[];
  error: { message: string; code?: string } | null;
}

export interface AGUIStoreOptions {
  url: string;
  tools?: Tool[];
  initialState?: Record<string, unknown>;
  getForwardedProps?: () => Record<string, unknown>;
  onComplete?: () => void;
  onError?: (error: { message: string; code?: string }) => void;
  onCustomEvent?: (name: string, value: unknown) => void;
}

export interface AGUIStore {
  state: Readable<AGUIStoreState>;
  status: Readable<RunStatus>;
  messages: Readable<StreamingMessage[]>;
  agentState: Readable<Record<string, unknown>>;
  isRunning: Readable<boolean>;
  error: Readable<{ message: string; code?: string } | null>;
  activeSteps: Readable<ActiveStep[]>;

  sendMessage: (content: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

function createInitialState(): AGUIStoreState {
  return {
    status: 'idle',
    threadId: null,
    runId: null,
    messages: [],
    agentState: {},
    activeSteps: [],
    error: null
  };
}

type AGUIEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | RunErrorEvent
  | StepStartedEvent
  | StepFinishedEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | ToolCallResultEvent
  | StateSnapshotEvent
  | StateDeltaEvent
  | CustomEvent
  | MessagesSnapshotEvent;

type EventSubscription = { unsubscribe: () => void };
type EventObservable = {
  subscribe: (observer: {
    next: (event: BaseEvent) => void;
    error?: (err: Error) => void;
    complete?: () => void;
  }) => EventSubscription;
};

type TextRole = 'assistant' | 'user' | 'system' | 'developer';

function isTextRole(role: Message['role']): role is TextRole {
  return role === 'assistant' || role === 'user' || role === 'system' || role === 'developer';
}

function toTextContent(content: Message['content'] | undefined): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map(item =>
        item && typeof item === 'object' && 'text' in item ? String(item.text ?? '') : ''
      )
      .filter(Boolean)
      .join('');
  }
  return '';
}

export function createAGUIStore(options: AGUIStoreOptions): AGUIStore {
  const agent = new HttpAgent({ url: options.url });
  const state = writable<AGUIStoreState>({
    ...createInitialState(),
    agentState: options.initialState ?? {}
  });

  let messageHistory: Message[] = [];
  let abortController: AbortController | null = null;

  function processEvent(event: AGUIEvent): void {
    state.update(s => {
      switch (event.type) {
        case EventType.RUN_STARTED: {
          const e = event as RunStartedEvent;
          return {
            ...s,
            status: 'running',
            threadId: e.threadId,
            runId: e.runId,
            error: null
          };
        }

        case EventType.RUN_FINISHED:
          options.onComplete?.();
          return { ...s, status: 'finished' };

        case EventType.RUN_ERROR: {
          const e = event as RunErrorEvent;
          const err = { message: e.message, code: e.code };
          options.onError?.(err);
          return { ...s, status: 'error', error: err };
        }

        case EventType.STEP_STARTED: {
          const e = event as StepStartedEvent;
          const metadata = 'metadata' in event
            ? (event as { metadata?: Record<string, unknown> }).metadata
            : undefined;
          return {
            ...s,
            activeSteps: [
              ...s.activeSteps,
              {
                name: e.stepName,
                startedAt: new Date(),
                metadata
              }
            ]
          };
        }

        case EventType.STEP_FINISHED: {
          const e = event as StepFinishedEvent;
          return {
            ...s,
            activeSteps: s.activeSteps.filter(step => step.name !== e.stepName)
          };
        }

        case EventType.TEXT_MESSAGE_START: {
          const e = event as TextMessageStartEvent;
          return {
            ...s,
            messages: [
              ...s.messages,
              {
                id: e.messageId,
                role: e.role,
                content: '',
                isStreaming: true,
                toolCalls: []
              }
            ]
          };
        }

        case EventType.TEXT_MESSAGE_CONTENT: {
          const e = event as TextMessageContentEvent;
          return {
            ...s,
            messages: s.messages.map(msg =>
              msg.id === e.messageId ? { ...msg, content: msg.content + e.delta } : msg
            )
          };
        }

        case EventType.TEXT_MESSAGE_END: {
          const e = event as TextMessageEndEvent;
          const updated = s.messages.map(msg =>
            msg.id === e.messageId ? { ...msg, isStreaming: false } : msg
          );
          const msg = updated.find(m => m.id === e.messageId);
          if (msg && isTextRole(msg.role)) {
            messageHistory.push({
              id: msg.id,
              role: msg.role,
              content: msg.content
            });
          }
          return { ...s, messages: updated };
        }

        case EventType.TOOL_CALL_START: {
          const e = event as ToolCallStartEvent;
          return {
            ...s,
            messages: s.messages.map(msg =>
              msg.id === e.parentMessageId
                ? {
                    ...msg,
                    toolCalls: [
                      ...msg.toolCalls,
                      { id: e.toolCallId, name: e.toolCallName, arguments: '', isStreaming: true }
                    ]
                  }
                : msg
            )
          };
        }

        case EventType.TOOL_CALL_ARGS: {
          const e = event as ToolCallArgsEvent;
          return {
            ...s,
            messages: s.messages.map(msg => ({
              ...msg,
              toolCalls: msg.toolCalls.map(tc =>
                tc.id === e.toolCallId ? { ...tc, arguments: tc.arguments + e.delta } : tc
              )
            }))
          };
        }

        case EventType.TOOL_CALL_END: {
          const e = event as ToolCallEndEvent;
          return {
            ...s,
            messages: s.messages.map(msg => ({
              ...msg,
              toolCalls: msg.toolCalls.map(tc =>
                tc.id === e.toolCallId ? { ...tc, isStreaming: false } : tc
              )
            }))
          };
        }

        case EventType.TOOL_CALL_RESULT: {
          const e = event as ToolCallResultEvent;
          messageHistory.push({
            id: e.messageId,
            role: 'tool',
            toolCallId: e.toolCallId,
            content: e.content
          });
          return {
            ...s,
            messages: s.messages.map(msg => ({
              ...msg,
              toolCalls: msg.toolCalls.map(tc =>
                tc.id === e.toolCallId ? { ...tc, result: e.content } : tc
              )
            }))
          };
        }

        case EventType.STATE_SNAPSHOT: {
          const e = event as StateSnapshotEvent;
          return { ...s, agentState: e.snapshot ?? {} };
        }

        case EventType.STATE_DELTA: {
          const e = event as StateDeltaEvent;
          return { ...s, agentState: applyJsonPatch(s.agentState, e.delta) };
        }

        case EventType.CUSTOM: {
          const e = event as CustomEvent;
          options.onCustomEvent?.(e.name, e.value);
          return s;
        }

        case EventType.MESSAGES_SNAPSHOT: {
          const e = event as MessagesSnapshotEvent;
          const msgs = e.messages ?? [];
          messageHistory = [...msgs];
          return {
            ...s,
            messages: msgs.map(m => ({
              id: m.id,
              role: m.role,
              content: toTextContent(m.content),
              isStreaming: false,
              toolCalls: []
            }))
          };
        }

        default:
          return s;
      }
    });
  }

  async function sendMessage(content: string): Promise<void> {
    const currentState = get(state);
    if (currentState.status === 'running') return;

    const userMsg: Message = {
      id: `msg_${Date.now()}`,
      role: 'user',
      content
    };
    messageHistory.push(userMsg);

    state.update(s => ({
      ...s,
      messages: [
        ...s.messages,
        {
          id: userMsg.id,
          role: 'user',
          content,
          isStreaming: false,
          toolCalls: []
        }
      ]
    }));

    const input: RunAgentInput = {
      threadId: currentState.threadId ?? `thread_${Date.now()}`,
      runId: `run_${Date.now()}`,
      messages: messageHistory,
      tools: options.tools ?? [],
      context: [],
      state: currentState.agentState,
      forwardedProps: options.getForwardedProps?.() ?? {}
    };

    abortController = new AbortController();

    try {
      const observable = agent.runAgent(input) as unknown as EventObservable;

      await new Promise<void>((resolve, reject) => {
        const subscription = observable.subscribe({
          next: (event: BaseEvent) => processEvent(event as AGUIEvent),
          error: (err: Error) => {
            const errorEvent: RunErrorEvent = {
              type: EventType.RUN_ERROR,
              message: err.message,
              code: 'CLIENT_ERROR'
            };
            processEvent(errorEvent);
            reject(err);
          },
          complete: () => resolve()
        });

        abortController!.signal.addEventListener('abort', () => {
          subscription.unsubscribe();
          state.update(s => ({ ...s, status: 'idle' }));
          resolve();
        });
      });
    } finally {
      abortController = null;
    }
  }

  function cancel(): void {
    abortController?.abort();
  }

  function reset(): void {
    cancel();
    messageHistory = [];
    state.set({
      ...createInitialState(),
      agentState: options.initialState ?? {}
    });
  }

  return {
    state,
    status: derived(state, $s => $s.status),
    messages: derived(state, $s => $s.messages),
    agentState: derived(state, $s => $s.agentState),
    isRunning: derived(state, $s => $s.status === 'running'),
    error: derived(state, $s => $s.error),
    activeSteps: derived(state, $s => $s.activeSteps),
    sendMessage,
    cancel,
    reset
  };
}

const AGUI_KEY = Symbol('agui');

export function setAGUIContext(store: AGUIStore): void {
  setContext(AGUI_KEY, store);
}

export function getAGUIContext(): AGUIStore {
  const store = getContext<AGUIStore>(AGUI_KEY);
  if (!store) throw new Error('AGUIProvider not found');
  return store;
}
