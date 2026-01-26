<script lang="ts">
  import { Empty } from '$lib/components/composites';
  import { getTrajectoryStore } from '$lib/stores';

  type BackgroundTaskResultPayload = {
    task_id: string;
    group_id?: string | null;
    status?: string;
    summary?: string | null;
    payload?: unknown;
    facts?: Record<string, unknown>;
    artifacts?: Record<string, unknown>[];
    consumed?: boolean;
    completed_at?: number;
  };

  const trajectoryStore = getTrajectoryStore();

  let memoryExpanded = $state(true);
  let llmContextExpanded = $state(true);
  let backgroundExpanded = $state(true);
  let toolContextExpanded = $state(false);

  // Extract conversation_memory from llmContext for separate display
  const llmContextWithoutMemory = $derived.by(() => {
    if (!trajectoryStore.llmContext) return null;
    const { conversation_memory, background_result, background_results, ...rest } = trajectoryStore.llmContext;
    return Object.keys(rest).length > 0 ? rest : null;
  });

  const backgroundResults = $derived.by((): BackgroundTaskResultPayload[] => {
    const results = (trajectoryStore as unknown as {
      backgroundResults?: Record<string, BackgroundTaskResultPayload> | null;
    }).backgroundResults;
    if (!results) return [];
    return (Object.values(results) as BackgroundTaskResultPayload[]).sort((a, b) => {
      const aTime = typeof a.completed_at === 'number' ? a.completed_at : 0;
      const bTime = typeof b.completed_at === 'number' ? b.completed_at : 0;
      return bTime - aTime;
    });
  });

  const formatJson = (obj: unknown): string => {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  };

  const truncate = (text: string, maxLength: number = 100): string => {
    if (text.length <= maxLength) return text;
    return text.slice(0, maxLength) + '...';
  };

  const formatTimestamp = (epochSeconds?: number): string => {
    if (typeof epochSeconds !== 'number') return '';
    const date = new Date(epochSeconds * 1000);
    return date.toLocaleTimeString();
  };

  const statusBadgeClass = (status?: string): string => {
    const normalized = status?.toLowerCase();
    if (normalized === 'failed') return 'badge error';
    if (normalized === 'completed') return 'badge success';
    return 'badge';
  };
</script>

<div class="context-body">
  {#if !trajectoryStore.hasContext}
    <Empty
      icon="*"
      title="No context yet"
      subtitle="Send a prompt to see injected context."
    />
  {:else}
    <!-- Memory Section -->
    <section class="context-section">
      <button
        class="section-header"
        onclick={() => memoryExpanded = !memoryExpanded}
        aria-expanded={memoryExpanded}
      >
        <span class="section-title">
          Conversation Memory
          {#if trajectoryStore.hasMemory}
            <span class="badge active">Active</span>
          {:else}
            <span class="badge">Disabled</span>
          {/if}
        </span>
        <span class="chevron" class:expanded={memoryExpanded}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M4 5L6 7L8 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/>
          </svg>
        </span>
      </button>

      {#if memoryExpanded}
        <div class="section-content">
          {#if trajectoryStore.conversationMemory}
            <!-- Summary -->
            {#if trajectoryStore.conversationMemory.summary}
              <div class="memory-block">
                <h4>Summary</h4>
                <div class="summary-text">
                  {trajectoryStore.conversationMemory.summary}
                </div>
              </div>
            {/if}

            <!-- Recent Turns -->
            {#if trajectoryStore.conversationMemory.recent_turns?.length}
              <div class="memory-block">
                <h4>Recent Turns ({trajectoryStore.conversationMemory.recent_turns.length})</h4>
                <div class="turns-list">
                  {#each trajectoryStore.conversationMemory.recent_turns as turn, idx (idx)}
                    <div class="turn-item">
                      <div class="turn-header">Turn {idx + 1}</div>
                      <div class="turn-row">
                        <span class="turn-label user">User</span>
                        <span class="turn-content">{truncate(turn.user, 150)}</span>
                      </div>
                      <div class="turn-row">
                        <span class="turn-label assistant">Assistant</span>
                        <span class="turn-content">{truncate(turn.assistant, 150)}</span>
                      </div>
                      {#if turn.trajectory_digest}
                        <div class="turn-digest">
                          {#if turn.trajectory_digest.tools_invoked?.length}
                            <span class="digest-item">
                              Tools: {turn.trajectory_digest.tools_invoked.join(', ')}
                            </span>
                          {/if}
                          {#if turn.trajectory_digest.observations_summary}
                            <span class="digest-item">
                              Obs: {truncate(turn.trajectory_digest.observations_summary, 80)}
                            </span>
                          {/if}
                        </div>
                      {/if}
                    </div>
                  {/each}
                </div>
              </div>
            {/if}

            <!-- Pending Turns -->
            {#if trajectoryStore.conversationMemory.pending_turns?.length}
              <div class="memory-block">
                <h4>Pending Turns ({trajectoryStore.conversationMemory.pending_turns.length})</h4>
                <div class="turns-list pending">
                  {#each trajectoryStore.conversationMemory.pending_turns as turn, idx (idx)}
                    <div class="turn-item">
                      <div class="turn-header">Pending {idx + 1}</div>
                      <div class="turn-row">
                        <span class="turn-label user">User</span>
                        <span class="turn-content">{truncate(turn.user, 100)}</span>
                      </div>
                      <div class="turn-row">
                        <span class="turn-label assistant">Assistant</span>
                        <span class="turn-content">{truncate(turn.assistant, 100)}</span>
                      </div>
                    </div>
                  {/each}
                </div>
              </div>
            {/if}
          {:else}
            <div class="empty-section">
              Short-term memory is not enabled or no conversation history exists.
            </div>
          {/if}
        </div>
      {/if}
    </section>

    <!-- LLM Context Section (other fields) -->
    <section class="context-section">
      <button
        class="section-header"
        onclick={() => llmContextExpanded = !llmContextExpanded}
        aria-expanded={llmContextExpanded}
      >
        <span class="section-title">LLM Context</span>
        <span class="chevron" class:expanded={llmContextExpanded}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M4 5L6 7L8 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/>
          </svg>
        </span>
      </button>

      {#if llmContextExpanded}
        <div class="section-content">
          {#if llmContextWithoutMemory}
            <pre class="json-block">{formatJson(llmContextWithoutMemory)}</pre>
          {:else}
            <div class="empty-section">
              No additional LLM context was injected (besides memory).
            </div>
          {/if}
        </div>
      {/if}
    </section>

    <!-- Background Results Section -->
    <section class="context-section">
      <button
        class="section-header"
        onclick={() => backgroundExpanded = !backgroundExpanded}
        aria-expanded={backgroundExpanded}
      >
        <span class="section-title">
          Background Results
          {#if backgroundResults.length > 0}
            <span class="badge active">{backgroundResults.length}</span>
          {/if}
        </span>
        <span class="chevron" class:expanded={backgroundExpanded}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M4 5L6 7L8 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/>
          </svg>
        </span>
      </button>

      {#if backgroundExpanded}
        <div class="section-content">
          {#if backgroundResults.length > 0}
            <div class="background-results">
              {#each backgroundResults as result (result.task_id)}
                <div class="background-result">
                  <div class="result-header">
                    <div class="result-title">Task {result.task_id.slice(0, 8)}</div>
                    <div class="result-badges">
                      <span class={statusBadgeClass(result.status)}>
                        {(result.status ?? 'completed').toUpperCase()}
                      </span>
                      {#if result.consumed}
                        <span class="badge muted">Consumed</span>
                      {/if}
                    </div>
                  </div>
                  {#if result.summary}
                    <div class="result-summary">{result.summary}</div>
                  {/if}
                  <div class="result-meta">
                    {#if result.group_id}
                      <span>Group {result.group_id.slice(0, 8)}</span>
                    {/if}
                    {#if result.completed_at}
                      <span>{formatTimestamp(result.completed_at)}</span>
                    {/if}
                    {#if result.artifacts?.length}
                      <span>{result.artifacts.length} artifacts</span>
                    {/if}
                    {#if result.facts && Object.keys(result.facts).length > 0}
                      <span>{Object.keys(result.facts).length} facts</span>
                    {/if}
                  </div>
                  {#if result.facts && Object.keys(result.facts).length > 0}
                    <pre class="json-block">{formatJson(result.facts)}</pre>
                  {/if}
                  {#if result.payload != null}
                    <details class="result-details">
                      <summary>Payload</summary>
                      <pre class="json-block">{formatJson(result.payload)}</pre>
                    </details>
                  {/if}
                </div>
              {/each}
            </div>
          {:else}
            <div class="empty-section">
              No background task results yet.
            </div>
          {/if}
        </div>
      {/if}
    </section>

    <!-- Tool Context Section -->
    <section class="context-section">
      <button
        class="section-header"
        onclick={() => toolContextExpanded = !toolContextExpanded}
        aria-expanded={toolContextExpanded}
      >
        <span class="section-title">Tool Context</span>
        <span class="chevron" class:expanded={toolContextExpanded}>
          <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor">
            <path d="M4 5L6 7L8 5" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/>
          </svg>
        </span>
      </button>

      {#if toolContextExpanded}
        <div class="section-content">
          {#if trajectoryStore.toolContext && Object.keys(trajectoryStore.toolContext).length > 0}
            <pre class="json-block">{formatJson(trajectoryStore.toolContext)}</pre>
          {:else}
            <div class="empty-section">
              No tool context was passed.
            </div>
          {/if}
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .context-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px;
  }

  .context-section {
    margin-bottom: 12px;
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    background: var(--color-code-bg, #fbf8f3);
    overflow: hidden;
  }

  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    padding: 10px 12px;
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
  }

  .section-header:hover {
    background: var(--color-btn-ghost-bg, #f2eee8);
  }

  .section-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }

  .badge {
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    background: var(--color-tab-bg, #f2eee8);
    color: var(--color-text-secondary, #3c3a36);
  }

  .badge.active {
    background: var(--color-tab-active-bg, #e8f6f2);
    color: var(--color-tab-active-text, #106c67);
  }

  .chevron {
    color: var(--color-text-secondary, #3c3a36);
    transition: transform 0.15s ease;
  }

  .chevron.expanded {
    transform: rotate(180deg);
  }

  .section-content {
    padding: 0 12px 12px;
  }

  .memory-block {
    margin-top: 10px;
  }

  .memory-block:first-child {
    margin-top: 0;
  }

  .memory-block h4 {
    margin: 0 0 6px 0;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-secondary, #3c3a36);
  }

  .summary-text {
    padding: 8px 10px;
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 6px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-text, #1f1f1f);
    white-space: pre-wrap;
  }

  .turns-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .turns-list.pending {
    opacity: 0.7;
  }

  .turn-item {
    padding: 8px 10px;
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 6px;
  }

  .turn-header {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-secondary, #3c3a36);
    margin-bottom: 6px;
  }

  .turn-row {
    display: flex;
    gap: 8px;
    margin-bottom: 4px;
  }

  .turn-row:last-child {
    margin-bottom: 0;
  }

  .turn-label {
    flex-shrink: 0;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
  }

  .turn-label.user {
    background: var(--color-tab-bg, #f2eee8);
    color: var(--color-text-secondary, #3c3a36);
  }

  .turn-label.assistant {
    background: var(--color-tab-active-bg, #e8f6f2);
    color: var(--color-tab-active-text, #106c67);
  }

  .turn-content {
    font-size: 11px;
    line-height: 1.4;
    color: var(--color-text, #1f1f1f);
    word-break: break-word;
  }

  .turn-digest {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px dashed var(--color-border, #e8e1d7);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .digest-item {
    font-size: 10px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .json-block {
    margin: 0;
    padding: 10px;
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 6px;
    font-size: 11px;
    font-family: var(--font-mono, ui-monospace);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 200px;
    overflow-y: auto;
    color: var(--color-text, #1f1f1f);
  }

  .background-results {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .background-result {
    padding: 10px;
    background: var(--color-card-bg, #fcfaf7);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
  }

  .result-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .result-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }

  .result-badges {
    display: inline-flex;
    gap: 6px;
  }

  .badge.success {
    background: #e9f7ef;
    color: #1b5e20;
  }

  .badge.error {
    background: #fef2f2;
    color: #c62828;
  }

  .badge.muted {
    background: var(--color-tab-bg, #f2eee8);
    color: var(--color-text-secondary, #3c3a36);
  }

  .result-summary {
    margin-top: 8px;
    padding: 8px 10px;
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 6px;
    font-size: 12px;
    line-height: 1.4;
    color: var(--color-text, #1f1f1f);
    white-space: pre-wrap;
  }

  .result-meta {
    margin-top: 6px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 10px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .result-details {
    margin-top: 8px;
  }

  .result-details summary {
    cursor: pointer;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-secondary, #3c3a36);
    margin-bottom: 6px;
  }

  .empty-section {
    padding: 12px;
    text-align: center;
    font-size: 12px;
    color: var(--color-text-secondary, #3c3a36);
  }
</style>
