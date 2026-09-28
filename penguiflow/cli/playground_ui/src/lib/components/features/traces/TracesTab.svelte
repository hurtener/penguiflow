<script lang="ts">
  import { onMount } from 'svelte';
  import { exportEvalDataset, fetchTrajectory, listTraces } from '$lib/services/api';
  import { getSessionStore, getTrajectoryStore } from '$lib/stores';
  import type { EvalDatasetExportResponse, TraceSummary } from '$lib/types';

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
  let traceLoadError = $state(false);
  let openTraceErrorById = $state<Record<string, string>>({});
  let openTraceLoadingById = $state<Record<string, boolean>>({});
  let selectedTraceId = $state<string | null>(null);
  let lastOpenRequestKey = $state<string | null>(null);
  let exportTagsQuery = $state('+split:val');
  let traceFilter = $state('');
  let exportResult = $state<EvalDatasetExportResponse | null>(null);
  let exportError = $state<string | null>(null);

  const filteredTraces = $derived.by(() => {
    const needle = traceFilter.trim().toLowerCase();
    if (!needle) {
      return traces;
    }
    return traces.filter((trace) => {
      const haystack = [
        trace.session_id,
        trace.trace_id,
        trace.query_preview ?? '',
        trace.tags.join(' ')
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(needle);
    });
  });

  const groupedSessions = $derived.by(() => {
    const bySession = new Map<string, TraceRow[]>();
    for (const trace of filteredTraces) {
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

  const exportTagSuggestions = $derived.by(() => {
    const baseTags = Array.from(new Set(traces.flatMap((trace) => trace.tags))).sort();
    return baseTags.flatMap((tag) => [`+${tag}`, `-${tag}`]);
  });

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

  function parseTagQuery(input: string): { include: string[]; exclude: string[] } {
    const include = new Set<string>();
    const exclude = new Set<string>();
    for (const rawToken of input.split(/[\s,]+/)) {
      const token = rawToken.trim();
      if (!token) {
        continue;
      }
      if (token.startsWith('-')) {
        const value = token.slice(1).trim();
        if (value) {
          exclude.add(value);
        }
        continue;
      }
      if (token.startsWith('+')) {
        const value = token.slice(1).trim();
        if (value) {
          include.add(value);
        }
        continue;
      }
      include.add(token);
    }
    return {
      include: Array.from(include),
      exclude: Array.from(exclude)
    };
  }

  async function onExportDataset(): Promise<void> {
    exportError = null;
    exportResult = null;
    const parsed = parseTagQuery(exportTagsQuery);
    const includeTags = parsed.include;
    if (includeTags.length === 0) {
      exportError = 'Include at least one tag.';
      return;
    }
    const response = await exportEvalDataset({
      include_tags: includeTags,
      exclude_tags: parsed.exclude,
      limit: 0
    });
    if (!response) {
      exportError = 'Dataset export failed.';
      return;
    }
    exportResult = response;
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
    const activeTraceId = sessionStore.activeTraceId;
    if (activeTraceId) {
      selectedTraceId = activeTraceId;
    }
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

  $effect(() => {
    const traceId = selectedTraceId;
    const traceCount = traces.length;
    void traceCount;
    if (!traceId) {
      return;
    }
    queueMicrotask(() => {
      const row = document.querySelector<HTMLTableRowElement>(`tr[data-trace-id="${traceId}"]`);
      if (row && typeof row.scrollIntoView === 'function') {
        row.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    });
  });
</script>

<div class="traces-tab">
  <h3 class="title">Traces</h3>

  <div class="traces-body">
    <div class="section">
      <h4>Dataset Export</h4>
      <div class="export-controls">
        <label class="sr-only" for="traces-export-query">Export tags query</label>
        <input
          id="traces-export-query"
          class="tag-input"
          bind:value={exportTagsQuery}
          placeholder="+split:val -tag:skip dataset:policy"
          list="traces-export-tag-suggestions"
        />
        <span class="export-hint">+tag include -tag ignore</span>
        <datalist id="traces-export-tag-suggestions">
          {#each exportTagSuggestions as suggestion (suggestion)}
            <option value={suggestion}></option>
          {/each}
        </datalist>
        <button class="tag-action" onclick={onExportDataset} aria-label="Export dataset traces tab">Export</button>
      </div>
      {#if exportError}
        <p class="muted error-text">{exportError}</p>
      {/if}
      {#if exportResult}
        <p class="muted">Exported {exportResult.trace_count} traces.</p>
        <p class="muted mono-path">{exportResult.dataset_path}</p>
        <p class="muted mono-path">{exportResult.manifest_path}</p>
      {/if}
    </div>

    <div class="section">
      <h4>Trace history</h4>
      <div class="filter-controls">
        <label for="traces-filter">Filter traces</label>
        <input
          id="traces-filter"
          class="tag-input"
          bind:value={traceFilter}
          placeholder="session, query, tag, or trace id"
        />
      </div>
      {#if traceLoadError}
        <div class="trace-error">
          <p class="muted">Failed to load traces.</p>
          <button class="tag-action" onclick={loadTraces} aria-label="Retry trace load">Retry</button>
        </div>
      {:else if traces.length === 0}
        <p class="muted">No traces available yet.</p>
      {:else if groupedSessions.length === 0}
        <p class="muted">No traces match this filter.</p>
      {:else}
        <div class="trace-groups" data-testid="traces-table">
          {#each groupedSessions as group (group.sessionId)}
            <section class="session-group">
              <h5 class="session-header">{group.sessionId}</h5>
              <table class="trace-table">
                <thead>
                  <tr>
                    <th>Turn</th>
                    <th>Query</th>
                    <th>Tags</th>
                    <th>Trace</th>
                  </tr>
                </thead>
                <tbody>
                  {#each group.rows as trace (trace.trace_id)}
                    <tr
                      class="trace-row"
                      data-trace-id={trace.trace_id}
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
                      <td class="tags-cell">
                        <div class="tags">
                          {#each trace.tags as tag (tag)}
                            <span class="tag"><span>{tag}</span></span>
                          {/each}
                        </div>
                      </td>
                      <td class="trace-id mono">{trace.trace_id}</td>
                    </tr>
                    {#if openTraceErrorById[trace.trace_id]}
                      <tr class="error-row">
                        <td colspan="4"><p class="muted error-text">{openTraceErrorById[trace.trace_id]}</p></td>
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
    font-size: 10px;
    letter-spacing: 0.02em;
    font-weight: 600;
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

  .export-controls {
    margin: 8px 0;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .filter-controls {
    margin-top: 8px;
    display: grid;
    gap: 4px;
  }

  .export-hint {
    font-size: 10px;
    color: var(--color-text-secondary, #5f5a51);
    opacity: 0.78;
    white-space: nowrap;
  }

  .filter-controls label {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-secondary, #5f5a51);
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
    font-size: 10px;
    color: var(--color-text-secondary, #736f67);
    overflow-wrap: anywhere;
  }

  .tags-cell {
    width: 220px;
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

  .tag-input {
    width: 220px;
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

  .mono-path {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
    overflow-wrap: anywhere;
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
