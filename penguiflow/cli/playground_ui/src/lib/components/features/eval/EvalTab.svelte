<script module lang="ts">
  import type { EvalDatasetLoadResponse, EvalRunResponse } from '$lib/types';

  type PersistedEvalState = {
    datasetPath: string;
    runMetricSpec: string;
    runMinTestScore: string;
    runMaxCases: string;
    loadResult: EvalDatasetLoadResponse | null;
    runResult: EvalRunResponse | null;
    lastReviewedExample: string | null;
    activeFilter: 'all' | 'failed' | 'passed';
  };

  const persistedEvalState: PersistedEvalState = {
    datasetPath: '',
    runMetricSpec: '',
    runMinTestScore: '0.8',
    runMaxCases: '50',
    loadResult: null,
    runResult: null,
    lastReviewedExample: null,
    activeFilter: 'all'
  };

  export function resetPersistedEvalState(): void {
    persistedEvalState.datasetPath = '';
    persistedEvalState.runMetricSpec = '';
    persistedEvalState.runMinTestScore = '0.8';
    persistedEvalState.runMaxCases = '50';
    persistedEvalState.loadResult = null;
    persistedEvalState.runResult = null;
    persistedEvalState.lastReviewedExample = null;
    persistedEvalState.activeFilter = 'all';
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import {
    fetchEvalCaseComparison,
    fetchTrajectory,
    listEvalDatasets,
    listEvalMetrics,
    loadEvalDataset,
    runEval
  } from '$lib/services/api';
  import { getSessionStore, getTrajectoryStore } from '$lib/stores';
  import type {
    EvalDatasetBrowseEntry,
    EvalMetricBrowseEntry
  } from '$lib/types';

  interface Props {}

  let {}: Props = $props();

  const trajectoryStore = getTrajectoryStore();
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

  let datasetPath = $state(persistedEvalState.datasetPath);
  let loadResult = $state<EvalDatasetLoadResponse | null>(persistedEvalState.loadResult);
  let loadError = $state<string | null>(null);
  let datasetBrowse = $state<EvalDatasetBrowseEntry[]>([]);
  let datasetBrowseError = $state<string | null>(null);
  let metricBrowse = $state<EvalMetricBrowseEntry[]>([]);
  let metricBrowseError = $state<string | null>(null);
  let runMetricSpec = $state(persistedEvalState.runMetricSpec);
  let runMinTestScore = $state(persistedEvalState.runMinTestScore);
  let runMaxCases = $state(persistedEvalState.runMaxCases);
  let runError = $state<string | null>(null);
  let runResult = $state<EvalRunResponse | null>(persistedEvalState.runResult);
  let isRunning = $state(false);
  let openTraceErrorByExample = $state<Record<string, string>>({});
  let openTraceLoadingByExample = $state<Record<string, boolean>>({});
  let lastReviewedExample = $state<string | null>(persistedEvalState.lastReviewedExample);
  let activeFilter = $state<'all' | 'failed' | 'passed'>(persistedEvalState.activeFilter);

  const sortedCases = $derived.by(() => {
    if (!runResult) return [];
    return [...runResult.cases].sort((a, b) => a.score - b.score);
  });

  const evalThreshold = $derived.by(() => runResult?.min_test_score ?? 0.8);

  function getSeverity(score: number): 'critical' | 'warning' | 'ok' {
    if (score < 0.5) return 'critical';
    if (score < evalThreshold) return 'warning';
    return 'ok';
  }

  const filteredCases = $derived.by(() => {
    if (activeFilter === 'all') return sortedCases;
    if (activeFilter === 'failed') {
      return sortedCases.filter((caseRow) => caseRow.score < evalThreshold);
    }
    return sortedCases.filter((caseRow) => caseRow.score >= evalThreshold);
  });

  const filterCounts = $derived.by(() => {
    const failed = sortedCases.filter((caseRow) => caseRow.score < evalThreshold).length;
    return {
      all: sortedCases.length,
      failed,
      passed: sortedCases.length - failed
    };
  });

  const canRun = $derived(Boolean(datasetPath.trim()) && Boolean(runMetricSpec.trim()) && !isRunning);

  function formatCount(value: number, singular: string, plural: string): string {
    return `${value} ${value === 1 ? singular : plural}`;
  }

  const statusLine = $derived.by(() => {
    if (isRunning) return 'Running evaluation...';
    if (runError) return runError;
    if (loadError) return loadError;
    if (runResult) {
      return `${formatCount(runResult.counts.total, 'case', 'cases')} evaluated.`;
    }
    if (loadResult) {
      return `${formatCount(loadResult.counts.total, 'example', 'examples')} loaded.`;
    }
    return '';
  });

  async function loadDatasetBrowse(): Promise<void> {
    datasetBrowseError = null;
    const entries = await listEvalDatasets();
    if (!entries) {
      datasetBrowse = [];
      datasetBrowseError = 'Failed to load datasets.';
      return;
    }
    datasetBrowse = entries;
    if (!datasetPath) {
      const preferred = entries.find((entry) => entry.is_default) ?? entries[0];
      if (preferred) {
        await selectDatasetPath(preferred.path);
      }
    }
  }

  async function loadMetricBrowse(): Promise<void> {
    metricBrowseError = null;
    const entries = await listEvalMetrics();
    if (!entries) {
      metricBrowse = [];
      metricBrowseError = 'Failed to load metrics.';
      return;
    }
    metricBrowse = entries;
    if (!runMetricSpec && entries[0]) {
      runMetricSpec = entries[0].metric_spec;
    }
  }

  async function onLoadDataset(): Promise<void> {
    loadError = null;
    const path = datasetPath.trim();
    if (!path) {
      loadResult = null;
      loadError = 'Dataset selection is required.';
      return;
    }
    const response = await loadEvalDataset(path);
    if (!response) {
      loadResult = null;
      loadError = 'Dataset preview failed.';
      return;
    }
    loadResult = response;
  }

  async function selectDatasetPath(path: string): Promise<void> {
    datasetPath = path;
    await onLoadDataset();
  }

  async function onRunEval(): Promise<void> {
    if (isRunning || !canRun) return;
    runError = null;
    trajectoryStore.setEvalCaseSelection(null);
    isRunning = true;

    const payload: { dataset_path: string; metric_spec: string; min_test_score?: number; max_cases?: number } = {
      dataset_path: datasetPath.trim(),
      metric_spec: runMetricSpec.trim()
    };
    const minScore = Number(runMinTestScore);
    if (Number.isFinite(minScore)) {
      payload.min_test_score = minScore;
    }
    const maxCases = Number(runMaxCases);
    if (Number.isFinite(maxCases) && maxCases > 0) {
      payload.max_cases = maxCases;
    }

    const response = await runEval(payload);
    if (!response) {
      runError = 'Eval run failed.';
      isRunning = false;
      return;
    }

    runResult = response;
    openTraceErrorByExample = {};
    openTraceLoadingByExample = {};
    lastReviewedExample = null;
    activeFilter = 'all';
    isRunning = false;
  }

  async function onOpenTrace(caseRow: EvalRunResponse['cases'][number]): Promise<void> {
    openTraceErrorByExample = { ...openTraceErrorByExample, [caseRow.example_id]: '' };
    openTraceLoadingByExample = { ...openTraceLoadingByExample, [caseRow.example_id]: true };
    const threshold = evalThreshold;
    const isFailing = caseRow.score < threshold;
    if (isFailing) {
      trajectoryStore.setEvalCaseSelection({
        exampleId: caseRow.example_id,
        datasetPath: datasetPath.trim(),
        predTraceId: caseRow.pred_trace_id,
        predSessionId: caseRow.pred_session_id,
        score: caseRow.score,
        threshold
      });
      trajectoryStore.setEvalComparisonLoading(true);
      trajectoryStore.setEvalComparisonError(null);
      const comparison = await fetchEvalCaseComparison({
        dataset_path: datasetPath.trim(),
        example_id: caseRow.example_id,
        pred_trace_id: caseRow.pred_trace_id,
        pred_session_id: caseRow.pred_session_id
      });
      if (comparison) {
        trajectoryStore.setEvalComparison(comparison);
      } else {
        trajectoryStore.setEvalComparison(null);
        trajectoryStore.setEvalComparisonError('Trajectory divergence data unavailable.');
      }
      trajectoryStore.setEvalComparisonLoading(false);
      trajectoryStore.setTrajectoryViewMode('divergence');
    } else {
      trajectoryStore.setEvalCaseSelection(null);
    }

    const payload = await fetchTrajectory(caseRow.pred_trace_id, caseRow.pred_session_id);
    if (!payload) {
      openTraceErrorByExample = {
        ...openTraceErrorByExample,
        [caseRow.example_id]: `Failed to open trace for ${caseRow.example_id}.`
      };
      if (isFailing) {
        trajectoryStore.setEvalComparisonError('Failed to load predicted trajectory.');
      }
      openTraceLoadingByExample = { ...openTraceLoadingByExample, [caseRow.example_id]: false };
      return;
    }
    sessionStore.activeTraceId = caseRow.pred_trace_id;
    trajectoryStore.clearArtifacts();
    trajectoryStore.setFromPayload(payload);
    lastReviewedExample = caseRow.example_id;
    openTraceLoadingByExample = { ...openTraceLoadingByExample, [caseRow.example_id]: false };
  }

  onMount(async () => {
    await Promise.all([loadDatasetBrowse(), loadMetricBrowse()]);
  });

  $effect(() => {
    persistedEvalState.datasetPath = datasetPath;
    persistedEvalState.runMetricSpec = runMetricSpec;
    persistedEvalState.runMinTestScore = runMinTestScore;
    persistedEvalState.runMaxCases = runMaxCases;
    persistedEvalState.loadResult = loadResult;
    persistedEvalState.runResult = runResult;
    persistedEvalState.lastReviewedExample = lastReviewedExample;
    persistedEvalState.activeFilter = activeFilter;
  });
</script>

<div class="eval-tab eval-light">
  <h3 class="title">Eval</h3>
  <div class="eval-body" data-testid="eval-body">
    <section class="toolbar" data-testid="eval-toolbar">
      <div class="toolbar-row">
        <div class="field compact-grow">
          <label for="dataset-select">Dataset selection</label>
          <select
            id="dataset-select"
            class="tag-input"
            bind:value={datasetPath}
            onchange={(event) => selectDatasetPath((event.currentTarget as HTMLSelectElement).value)}
          >
            {#if datasetBrowse.length === 0}
              <option value="">No datasets</option>
            {:else}
              {#each datasetBrowse as entry (entry.path)}
                <option value={entry.path}>{entry.label}{entry.is_default ? ' (default)' : ''}</option>
              {/each}
            {/if}
          </select>
        </div>

        <div class="field compact-grow">
          <label for="run-metric-spec">Metric selection</label>
          <select id="run-metric-spec" class="tag-input" bind:value={runMetricSpec}>
            <option value="">Select metric</option>
            {#each metricBrowse as metric (metric.metric_spec)}
              <option value={metric.metric_spec}>{metric.label}</option>
            {/each}
          </select>
        </div>

        <div class="field compact-narrow">
          <label for="run-min-test-score">Min score</label>
          <input id="run-min-test-score" class="tag-input" bind:value={runMinTestScore} placeholder="0.8" />
        </div>

        <div class="field compact-narrow">
          <label for="run-max-cases">Max cases</label>
          <input id="run-max-cases" class="tag-input" bind:value={runMaxCases} placeholder="50" />
        </div>

        <button class="tag-action" onclick={onRunEval} aria-label="Run evaluation" disabled={!canRun}>
          {isRunning ? 'Running...' : 'Run'}
        </button>
      </div>

      {#if datasetBrowseError}
        <p class="muted error-text">{datasetBrowseError}</p>
      {/if}
      {#if metricBrowseError}
        <p class="muted error-text">{metricBrowseError}</p>
      {/if}
      {#if statusLine}
        <p class="status-line" data-testid="eval-status-line">{statusLine}</p>
      {/if}
    </section>

    <section class="results">
      {#if runResult}
        <div class="summary-line" data-testid="eval-summary-line">
          <button
            class="chip filter-chip"
            data-active={activeFilter === 'all'}
            aria-pressed={activeFilter === 'all'}
            onclick={() => (activeFilter = 'all')}
          >
            All {filterCounts.all}
          </button>
          <button
            class="chip filter-chip"
            data-active={activeFilter === 'failed'}
            aria-pressed={activeFilter === 'failed'}
            onclick={() => (activeFilter = 'failed')}
          >
            Failed {filterCounts.failed}
          </button>
          <button
            class="chip filter-chip"
            data-active={activeFilter === 'passed'}
            aria-pressed={activeFilter === 'passed'}
            onclick={() => (activeFilter = 'passed')}
          >
            Passed {filterCounts.passed}
          </button>
        </div>

        <table class="result-table" data-testid="eval-results-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Question</th>
              <th>Split</th>
              <th>Trace</th>
              <th>Score</th>
              <th>Feedback</th>
            </tr>
          </thead>
          <tbody>
            {#each filteredCases as caseRow (caseRow.example_id)}
              <tr
                class="result-row"
                tabindex="0"
                data-selected={lastReviewedExample === caseRow.example_id}
                data-severity={getSeverity(caseRow.score)}
                aria-busy={openTraceLoadingByExample[caseRow.example_id] === true}
                onkeydown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    void onOpenTrace(caseRow);
                  }
                }}
                onclick={() => onOpenTrace(caseRow)}
              >
                <td data-testid="result-example-id">{caseRow.example_id}</td>
                <td>{caseRow.question}</td>
                <td>{caseRow.split.toUpperCase()}</td>
                <td class="mono">{caseRow.pred_trace_id}</td>
                <td class="score-cell" data-pass={caseRow.score >= evalThreshold ? 'true' : 'false'}>
                  <span
                    class="score-icon"
                    aria-label={caseRow.score >= evalThreshold
                      ? `Passed case ${caseRow.example_id}`
                      : `Failed case ${caseRow.example_id}`}
                  >
                    {caseRow.score >= evalThreshold ? '✓' : '✕'}
                  </span>
                  <span>{caseRow.score}</span>
                </td>
                <td>{caseRow.feedback ?? '—'}</td>
              </tr>
              {#if openTraceErrorByExample[caseRow.example_id]}
                <tr class="error-row"><td class="error-text" colspan="6">{openTraceErrorByExample[caseRow.example_id]}</td></tr>
              {/if}
            {/each}
          </tbody>
        </table>
        {#if filteredCases.length === 0}
          <p class="muted">No cases in this filter.</p>
        {/if}
      {:else}
        <p class="muted">Run evaluation to see results.</p>
      {/if}
    </section>
  </div>
</div>

<style>
  .eval-tab {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 2px;
    flex: 1;
    min-height: 0;
  }

  .eval-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding-right: 4px;
  }

  .title {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    color: var(--color-ink, #1e242c);
  }

  .toolbar {
    position: sticky;
    top: 0;
    z-index: 2;
    border: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-card-bg, #fcfaf7);
    border-radius: 10px;
    padding: 8px;
    display: grid;
    gap: 6px;
  }

  .toolbar-row {
    display: flex;
    align-items: flex-end;
    gap: 8px;
    flex-wrap: wrap;
  }

  .field {
    display: grid;
    gap: 3px;
  }

  .compact-grow {
    flex: 1;
    min-width: 200px;
  }

  .compact-narrow {
    width: 92px;
  }

  .field label {
    font-size: 10px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .tag-input {
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    background: var(--color-code-bg, #fbf8f3);
    color: var(--color-text-secondary, #3c3a36);
    padding: 7px 8px;
    font-size: 12px;
    min-width: 0;
  }

  .tag-action {
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    background: var(--color-btn-ghost-bg, #f2eee8);
    color: var(--color-text-secondary, #3c3a36);
    padding: 7px 10px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .tag-action:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .status-line,
  .muted {
    margin: 0;
    font-size: 12px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .results {
    border: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-card-bg, #fcfaf7);
    border-radius: 10px;
    padding: 8px;
    display: grid;
    gap: 8px;
  }

  .summary-line {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }

  .chip {
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
    color: var(--color-text-secondary, #3c3a36);
    background: #f8f4ed;
  }

  .filter-chip {
    cursor: pointer;
  }

  .filter-chip[data-active='true'] {
    border-color: var(--color-primary, #31a6a0);
    background: #edf8f7;
  }

  .result-table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }

  .result-table th,
  .result-table td {
    padding: 7px 8px;
    border-bottom: 1px solid var(--color-border-muted, #eee6dc);
    font-size: 11px;
    text-align: left;
    vertical-align: top;
    overflow-wrap: anywhere;
  }

  .result-table th {
    background: #fffdf9;
    color: var(--color-text-secondary, #5f5a51);
    font-weight: 700;
  }

  .result-row {
    cursor: pointer;
    transition: background-color 120ms ease;
  }

  .result-row:hover {
    background: #faf6ef;
  }

  .result-row[data-severity='critical'] {
    border-left: 2px solid #c56f6f;
    background: #fff7f7;
  }

  .result-row[data-severity='warning'] {
    border-left: 2px solid #d3ad6c;
    background: #fffcf6;
  }

  .result-row[data-severity='ok'] {
    opacity: 0.92;
  }

  .result-row[data-selected='true'] {
    background: #eef4ff;
  }

  .result-row[aria-busy='true'] {
    opacity: 0.72;
  }

  .score-cell {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
  }

  .score-icon {
    font-size: 11px;
    line-height: 1;
    font-weight: 700;
  }

  .score-cell[data-pass='true'] .score-icon {
    color: #2f6d3f;
  }

  .score-cell[data-pass='false'] .score-icon {
    color: #8a2d2d;
  }

  .error-row td {
    background: #fff9f8;
  }

  .error-text {
    color: var(--color-error-text, #9b2d2d);
  }
</style>
