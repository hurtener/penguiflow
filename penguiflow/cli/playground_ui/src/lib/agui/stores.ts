/**
 * AG-UI Svelte stores wrapping @ag-ui/client HttpAgent.
 */
import { derived, get, writable, type Readable } from 'svelte/store';
import { getContext, setContext } from 'svelte';
import { HttpAgent } from '@ag-ui/client';
import type { BaseEvent, Message, RunAgentInput, Tool } from '@ag-ui/core';
import { applyJsonPatch } from './patch';

export type RunStatus = 'idle' | 'running' | 'finished' | 'error';

export interface StreamingMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
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

export function createAGUIStore(options: AGUIStoreOptions): AGUIStore {
  const agent = new HttpAgent({ url: options.url });
  const state = writable<AGUIStoreState>({
    ...createInitialState(),
    agentState: options.initialState ?? {}
  });

  let messageHistory: Message[] = [];
  let abortController: AbortController | null = null;

  function processEvent(event: BaseEvent): void {
    state.update(s => {
      switch (event.type) {
        case 'RUN_STARTED':
          return {
            ...s,
            status: 'running',
            threadId: (event as any).threadId,
            runId: (event as any).runId,
            error: null
          };

        case 'RUN_FINISHED':
          options.onComplete?.();
          return { ...s, status: 'finished' };

        case 'RUN_ERROR': {
          const err = { message: (event as any).message, code: (event as any).code };
          options.onError?.(err);
          return { ...s, status: 'error', error: err };
        }

        case 'STEP_STARTED':
          return {
            ...s,
            activeSteps: [
              ...s.activeSteps,
              {
                name: (event as any).stepName,
                startedAt: new Date(),
                metadata: (event as any).metadata
              }
            ]
          };

        case 'STEP_FINISHED':
          return {
            ...s,
            activeSteps: s.activeSteps.filter(step => step.name !== (event as any).stepName)
          };

        case 'TEXT_MESSAGE_START': {
          const e = event as any;
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

        case 'TEXT_MESSAGE_CONTENT': {
          const e = event as any;
          return {
            ...s,
            messages: s.messages.map(msg =>
              msg.id === e.messageId ? { ...msg, content: msg.content + e.delta } : msg
            )
          };
        }

        case 'TEXT_MESSAGE_END': {
          const e = event as any;
          const updated = s.messages.map(msg =>
            msg.id === e.messageId ? { ...msg, isStreaming: false } : msg
          );
          const msg = updated.find(m => m.id === e.messageId);
          if (msg && msg.role !== 'tool') {
            messageHistory.push({
              id: msg.id,
              role: msg.role as any,
              content: msg.content
            });
          }
          return { ...s, messages: updated };
        }

        case 'TOOL_CALL_START': {
          const e = event as any;
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

        case 'TOOL_CALL_ARGS': {
          const e = event as any;
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

        case 'TOOL_CALL_END': {
          const e = event as any;
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

        case 'TOOL_CALL_RESULT': {
          const e = event as any;
          messageHistory.push({
            id: e.messageId,
            role: 'tool',
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

        case 'STATE_SNAPSHOT':
          return { ...s, agentState: (event as any).snapshot };

        case 'STATE_DELTA': {
          return { ...s, agentState: applyJsonPatch(s.agentState, (event as any).delta) };
        }

        case 'CUSTOM':
          options.onCustomEvent?.((event as any).name, (event as any).value);
          return s;

        case 'MESSAGES_SNAPSHOT': {
          const msgs = (event as any).messages as Message[];
          messageHistory = [...msgs];
          return {
            ...s,
            messages: msgs.map(m => ({
              id: m.id,
              role: m.role,
              content: m.content ?? '',
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
    } as RunAgentInput;

    abortController = new AbortController();

    try {
      const observable = agent.runAgent(input);

      await new Promise<void>((resolve, reject) => {
        const subscription = observable.subscribe({
          next: (event: BaseEvent) => processEvent(event),
          error: (err: Error) => {
            processEvent({
              type: 'RUN_ERROR',
              message: err.message,
              code: 'CLIENT_ERROR'
            } as any);
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
