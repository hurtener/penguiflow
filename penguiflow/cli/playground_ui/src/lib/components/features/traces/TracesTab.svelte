<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchTrajectory, listTraces, setTraceTags } from '$lib/services/api';
  import { getSessionStore, getTrajectoryStore } from '$lib/stores';
  import type { TraceSummary } from '$lib/types';

  type TraceRow = TraceSummary & {
    query_preview?: string | null;
    turn_index?: number | null;
  };

  export interface TraceOpenRequest {
    traceId: string;
    sessionId: string;
    requestId?: number;
  }

  interface Props {
    openRequest?: TraceOpenRequest | null;
  }

  let { openRequest = null }: Props = $props();

  const trajectoryStore = (() => {
    try {
      const store = getTrajectoryStore();
      if (store) {
        return store;
      }
    } catch {
    }
    return {
      clearArtifacts: () => {},
      setFromPayload: (_payload: unknown) => {}
    };
  })();

  const sessionStore = (() => {
    try {
      const store = getSessionStore();
      if (store) {
        return store;
      }
    } catch {
    }
    return {
      activeTraceId: null as string | null
    };
  })();

  let traces = $state<TraceRow[]>([]);
  let tagDrafts = $state<Record<string, string>>({});
  let traceLoadError = $state(false);
  let openTraceErrorById = $state<Record<string, string>>({});
  let openTraceLoadingById = $state<Record<string, boolean>>({});
  let selectedTraceId = $state<string | null>(null);
  let lastOpenRequestKey = $state<string | null>(null);

  const groupedSessions = $derived.by(() => {
    const bySession = new Map<string, TraceRow[]>();
    for (const trace of traces) {
      const existing = bySession.get(trace.session_id);
      if (existing) {
        existing.push(trace);
      } else {
        bySession.set(trace.session_id, [trace]);
      }
    }

    return Array.from(bySession.entries())
      .map(([sessionId, rows]) => ({
        sessionId,
        rows: [...rows].sort((a, b) => {
          const turnA = a.turn_index ?? Number.MAX_SAFE_INTEGER;
          const turnB = b.turn_index ?? Number.MAX_SAFE_INTEGER;
          if (turnA !== turnB) return turnA - turnB;
          return a.trace_id.localeCompare(b.trace_id);
        })
      }))
      .sort((a, b) => a.sessionId.localeCompare(b.sessionId));
  });

  function mergeTraceUpdate(updated: TraceRow): void {
    traces = traces.map((item) => {
      if (item.trace_id !== updated.trace_id) {
        return item;
      }
      return {
        ...item,
        ...updated,
        query_preview: updated.query_preview ?? item.query_preview ?? null,
        turn_index: updated.turn_index ?? item.turn_index ?? null
      };
    });
  }

  async function loadTraces(): Promise<void> {
    const rows = await listTraces(50);
    if (rows === null) {
      traceLoadError = true;
      traces = [];
      return;
    }
    traceLoadError = false;
    traces = rows;
  }

  async function addTag(trace: TraceSummary): Promise<void> {
    const rawTag = tagDrafts[trace.trace_id] ?? '';
    const tag = rawTag.trim();
    if (!tag) return;
    const updated = await setTraceTags(trace.trace_id, trace.session_id, [tag], []);
    if (!updated) return;
    mergeTraceUpdate(updated as TraceRow);
    tagDrafts = { ...tagDrafts, [trace.trace_id]: '' };
  }

  async function removeTag(trace: TraceSummary, tag: string): Promise<void> {
    const updated = await setTraceTags(trace.trace_id, trace.session_id, [], [tag]);
    if (!updated) return;
    mergeTraceUpdate(updated as TraceRow);
  }

  async function assignSplit(trace: TraceSummary, splitTag: 'split:val' | 'split:test'): Promise<void> {
    const remove: string[] = [];
    if (splitTag === 'split:val' && trace.tags.includes('split:test')) {
      remove.push('split:test');
    }
    if (splitTag === 'split:test' && trace.tags.includes('split:val')) {
      remove.push('split:val');
    }
    if (trace.tags.includes(splitTag)) {
      return;
    }
    const updated = await setTraceTags(trace.trace_id, trace.session_id, [splitTag], remove);
    if (!updated) return;
    mergeTraceUpdate(updated as TraceRow);
  }

  async function openTrace(trace: Pick<TraceSummary, 'trace_id' | 'session_id'>): Promise<void> {
    openTraceErrorById = { ...openTraceErrorById, [trace.trace_id]: '' };
    openTraceLoadingById = { ...openTraceLoadingById, [trace.trace_id]: true };
    const payload = await fetchTrajectory(trace.trace_id, trace.session_id);
    if (!payload) {
      openTraceErrorById = {
        ...openTraceErrorById,
        [trace.trace_id]: `Failed to open trace ${trace.trace_id}.`
      };
      openTraceLoadingById = { ...openTraceLoadingById, [trace.trace_id]: false };
      return;
    }
    sessionStore.activeTraceId = trace.trace_id;
    trajectoryStore.clearArtifacts();
    trajectoryStore.setFromPayload(payload);
    selectedTraceId = trace.trace_id;
    openTraceLoadingById = { ...openTraceLoadingById, [trace.trace_id]: false };
  }

  onMount(async () => {
    await loadTraces();
  });

  $effect(() => {
    if (!openRequest?.traceId || !openRequest.sessionId) {
      return;
    }
    const key = `${openRequest.traceId}:${openRequest.sessionId}:${openRequest.requestId ?? 0}`;
    if (lastOpenRequestKey === key) {
      return;
    }
    lastOpenRequestKey = key;
    void openTrace({ trace_id: openRequest.traceId, session_id: openRequest.sessionId });
  });
</script>

<div class="traces-tab">
  <h3 class="title">Traces</h3>
  <p class="muted">Select a trace to load its trajectory below. Keep tagging here for dataset export workflows.</p>

  <div class="traces-body">
    <div class="section">
      <h4>Trace Library</h4>
      {#if traceLoadError}
        <div class="trace-error">
          <p class="muted">Failed to load traces.</p>
          <button class="tag-action" onclick={loadTraces} aria-label="Retry trace load">Retry</button>
        </div>
      {:else if traces.length === 0}
        <p class="muted">No traces available yet.</p>
      {:else}
        <div class="trace-groups" data-testid="traces-table">
          {#each groupedSessions as group (group.sessionId)}
            <section class="session-group">
              <h5 class="session-header">Session {group.sessionId}</h5>
              <table class="trace-table">
                <thead>
                  <tr>
                    <th>Turn</th>
                    <th>Query</th>
                    <th>Tags</th>
                    <th>Trace</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {#each group.rows as trace (trace.trace_id)}
                    <tr
                      class="trace-row"
                      tabindex="0"
                      data-selected={selectedTraceId === trace.trace_id}
                      aria-busy={openTraceLoadingById[trace.trace_id] === true}
                      onkeydown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          void openTrace(trace);
                        }
                      }}
                      onclick={() => openTrace(trace)}
                    >
                      <td class="turn-cell">Turn {trace.turn_index ?? '-'}</td>
                      <td class="query-cell">{trace.query_preview ?? '—'}</td>
                      <td>
                        <div class="tags">
                          {#each trace.tags as tag (tag)}
                            <span class="tag">
                              <span>{tag}</span>
                              <button
                                class="tag-remove"
                                onclick={(event) => {
                                  event.stopPropagation();
                                  void removeTag(trace, tag);
                                }}
                                aria-label={`Remove tag ${tag} ${trace.trace_id}`}
                              >
                                x
                              </button>
                            </span>
                          {/each}
                        </div>
                      </td>
                      <td class="trace-id mono">{trace.trace_id}</td>
                      <td>
                        <div class="inline-actions">
                          <label class="sr-only" for={`add-tag-${trace.trace_id}`}>Add tag for {trace.trace_id}</label>
                          <input
                            id={`add-tag-${trace.trace_id}`}
                            class="tag-input"
                            placeholder="tag:value"
                            bind:value={tagDrafts[trace.trace_id]}
                            onclick={(event) => event.stopPropagation()}
                          />
                          <button
                            class="tag-action"
                            onclick={(event) => {
                              event.stopPropagation();
                              void addTag(trace);
                            }}
                            aria-label={`Add tag ${trace.trace_id}`}
                          >
                            Add
                          </button>
                          <button
                            class="tag-action"
                            onclick={(event) => {
                              event.stopPropagation();
                              void assignSplit(trace, 'split:val');
                            }}
                            aria-label={`Mark val ${trace.trace_id}`}
                          >
                            Val
                          </button>
                          <button
                            class="tag-action"
                            onclick={(event) => {
                              event.stopPropagation();
                              void assignSplit(trace, 'split:test');
                            }}
                            aria-label={`Mark test ${trace.trace_id}`}
                          >
                            Test
                          </button>
                        </div>
                      </td>
                    </tr>
                    {#if openTraceErrorById[trace.trace_id]}
                      <tr class="error-row">
                        <td colspan="5"><p class="muted error-text">{openTraceErrorById[trace.trace_id]}</p></td>
                      </tr>
                    {/if}
                  {/each}
                </tbody>
              </table>
            </section>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .traces-tab {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 8px 2px;
    flex: 1;
    min-height: 0;
  }

  .title {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    color: var(--color-ink, #1e242c);
  }

  .muted {
    margin: 0;
    color: var(--color-text-secondary, #5f5a51);
    font-size: 12px;
  }

  .section {
    border: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-card-bg, #fcfaf7);
    border-radius: 12px;
    padding: 12px;
  }

  .section h4 {
    margin: 0;
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text-secondary, #3c3a36);
  }

  .traces-body {
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding-right: 4px;
  }

  .trace-groups {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .session-group {
    border: 1px solid var(--color-border-muted, #e4ddd2);
    border-radius: 10px;
    background: #fff;
    overflow: hidden;
  }

  .session-header {
    margin: 0;
    padding: 7px 10px;
    font-size: 11px;
    letter-spacing: 0.02em;
    font-weight: 700;
    color: var(--color-text-secondary, #5f5a51);
    background: #f7f2ea;
    border-bottom: 1px solid var(--color-border-muted, #e4ddd2);
  }

  .trace-table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }

  .trace-table th,
  .trace-table td {
    padding: 7px 8px;
    border-bottom: 1px solid var(--color-border-muted, #eee6dc);
    vertical-align: top;
    font-size: 11px;
    text-align: left;
  }

  .trace-table th {
    font-weight: 700;
    color: var(--color-text-secondary, #5f5a51);
    background: #fffdf9;
  }

  .trace-table tbody tr:last-child td {
    border-bottom: none;
  }

  .trace-row {
    cursor: pointer;
    transition: background-color 120ms ease;
  }

  .trace-row:hover {
    background: #faf6ef;
  }

  .trace-row[data-selected='true'] {
    background: #eef4ff;
  }

  .trace-row[aria-busy='true'] {
    opacity: 0.72;
  }

  .turn-cell {
    width: 70px;
    white-space: nowrap;
    color: var(--color-text-secondary, #5f5a51);
  }

  .query-cell {
    color: var(--color-text, #1f1f1f);
    overflow-wrap: anywhere;
  }

  .trace-id {
    width: 180px;
    color: var(--color-text, #1f1f1f);
    overflow-wrap: anywhere;
  }

  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border: 1px solid var(--color-border-muted, #dfd6ca);
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
    background: #fffdf9;
  }

  .tag-remove {
    border: none;
    background: transparent;
    color: var(--color-text-secondary, #6b6255);
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }

  .inline-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
  }

  .tag-input {
    width: 120px;
    border: 1px solid var(--color-border, #d9d0c4);
    border-radius: 8px;
    padding: 6px 8px;
    font-size: 12px;
    color: var(--color-text, #1f1f1f);
    background: #fff;
  }

  .tag-action {
    border: 1px solid var(--color-border, #d2cabf);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
    background: #f6f1e8;
    cursor: pointer;
  }

  .tag-action:disabled {
    cursor: default;
    opacity: 0.7;
  }

  .trace-error {
    margin-top: 8px;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .error-text {
    color: #a33434;
  }

  .error-row td {
    background: #fff9f8;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    border: 0;
  }
</style>
