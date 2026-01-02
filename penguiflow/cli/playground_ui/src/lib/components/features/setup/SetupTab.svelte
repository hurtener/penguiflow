<script lang="ts">
  import { readable } from 'svelte/store';
  import { ErrorList } from '$lib/components/composites';
  import { getEventsStore, getInteractionsStore, getSessionStore, getSetupStore, getTrajectoryStore } from '$lib/stores';
  import { MessageList, StateDebugger, setAGUIContext, type AGUIStore } from '$lib/agui';
  import SetupField from './SetupField.svelte';

  const previewStore: AGUIStore = {
    messages: readable([
      {
        id: 'preview-msg',
        role: 'assistant',
        content: 'AG-UI preview message with a tool call.',
        isStreaming: false,
        toolCalls: [
          {
            id: 'preview-call',
            name: 'search',
            arguments: '{"query":"preview"}',
            isStreaming: false,
            result: 'ok'
          }
        ]
      }
    ]),
    state: readable({
      status: 'idle',
      threadId: 'preview-thread',
      runId: 'preview-run',
      messages: [],
      agentState: { mode: 'preview' },
      activeSteps: [],
      error: null
    }),
    status: readable('idle'),
    agentState: readable({ mode: 'preview' }),
    isRunning: readable(false),
    error: readable(null),
    activeSteps: readable([]),
    sendMessage: async () => {},
    cancel: () => {},
    reset: () => {}
  };

  const sessionStore = getSessionStore();
  const setupStore = getSetupStore();
  const trajectoryStore = getTrajectoryStore();
  const eventsStore = getEventsStore();
  const interactionsStore = getInteractionsStore();

  setAGUIContext(previewStore);
</script>

<div class="setup-body">
  <div class="setup-grid">
    <SetupField label="Session ID" hint="Used to scope short-term memory and trajectory lookups.">
      <div class="row gap">
        <input class="setup-input" bind:value={sessionStore.sessionId} />
        <button
          class="ghost-btn small"
          onclick={() => {
            sessionStore.newSession();
            trajectoryStore.clear();
            eventsStore.clear();
            interactionsStore.clear();
          }}
        >
          New
        </button>
      </div>
    </SetupField>

    <SetupField label="Tenant ID">
      <input class="setup-input" bind:value={setupStore.tenantId} />
    </SetupField>

    <SetupField label="User ID">
      <input class="setup-input" bind:value={setupStore.userId} />
    </SetupField>

    <SetupField
      label="Tool Context (JSON)"
      hint="Merged with tenant/user and injected as runtime tool_context."
      full
    >
      <textarea
        class="setup-textarea"
        bind:value={setupStore.toolContextRaw}
        placeholder={'{"any_runtime_key": "value"}'}
      ></textarea>
    </SetupField>

    <SetupField
      label="LLM Context (JSON)"
      hint="Only used when the playground wraps a planner entry point."
      full
    >
      <textarea
        class="setup-textarea"
        bind:value={setupStore.llmContextRaw}
        placeholder={'{}'}
      ></textarea>
    </SetupField>

    <SetupField
      label="Streaming Protocol"
      hint="Enable AG-UI streaming for the playground."
      full
    >
      <label class="toggle-row">
        <input class="toggle-input" type="checkbox" bind:checked={setupStore.useAgui} />
        <span>Use AG-UI</span>
      </label>
    </SetupField>
  </div>

  {#if setupStore.error}
    <ErrorList errors={[{ id: 'setup-err', message: setupStore.error }]} />
  {/if}

  {#if setupStore.useAgui}
    <div class="agui-preview">
      <div class="agui-preview-title">AG-UI Preview</div>
      <MessageList />
      <StateDebugger />
    </div>
  {/if}
</div>

<style>
  .setup-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
  }

  .setup-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }

  .row {
    display: flex;
  }

  .gap {
    gap: 8px;
  }

  .setup-input {
    flex: 1;
    padding: 8px 10px;
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    font-size: 12px;
    background: var(--color-code-bg, #fbf8f3);
    outline: none;
  }

  .setup-input:focus {
    border-color: var(--color-primary, #31a6a0);
  }

  .setup-textarea {
    width: 100%;
    min-height: 60px;
    padding: 8px 10px;
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    font-size: 11px;
    font-family: var(--font-mono);
    background: var(--color-code-bg, #fbf8f3);
    resize: vertical;
    outline: none;
  }

  .setup-textarea:focus {
    border-color: var(--color-primary, #31a6a0);
  }

  .toggle-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .toggle-input {
    width: 16px;
    height: 16px;
    accent-color: var(--color-primary, #31a6a0);
  }

  .agui-preview {
    margin-top: 16px;
    padding: 12px;
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 12px;
    background: var(--color-card-bg, #fcfaf7);
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .agui-preview-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-secondary, #3c3a36);
  }

  .ghost-btn {
    padding: 8px 12px;
    border-radius: 8px;
    background: var(--color-btn-ghost-bg, #f2eee8);
    font-weight: 600;
    font-size: 12px;
    border: 1px solid var(--color-border, #e8e1d7);
    cursor: pointer;
  }

  .ghost-btn:hover {
    background: var(--color-btn-ghost-hover, #e8e4de);
  }

  .ghost-btn.small {
    padding: 6px 10px;
    font-size: 11px;
  }
</style>
