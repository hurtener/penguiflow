<script lang="ts">
  import { onMount } from 'svelte';
  import {
    exportEvalDataset,
    fetchTrajectory,
    listEvalDatasets,
    listEvalMetrics,
    listTraces,
    loadEvalDataset,
    runEval,
    setTraceTags
  } from '$lib/services/api';
  import { getTrajectoryStore } from '$lib/stores';
  import type {
    EvalDatasetBrowseEntry,
    EvalDatasetExportResponse,
    EvalDatasetLoadResponse,
    EvalMetricBrowseEntry,
    EvalRunResponse,
    TraceSummary
  } from '$lib/types';

  const trajectoryStore = getTrajectoryStore();

  let traces = $state<TraceSummary[]>([]);
  let tagDrafts = $state<Record<string, string>>({});
  let traceLoadError = $state(false);
  let exportIncludeTags = $state('split:val');
  let exportExcludeTags = $state('');
  let exportOutputDir = $state('examples/evals/policy_compliance_v1/dataset');
  let exportLimit = $state('0');
  let exportResult = $state<EvalDatasetExportResponse | null>(null);
  let exportError = $state<string | null>(null);
  let datasetPath = $state('');
  let loadResult = $state<EvalDatasetLoadResponse | null>(null);
  let loadError = $state<string | null>(null);
  let datasetBrowse = $state<EvalDatasetBrowseEntry[]>([]);
  let datasetBrowseError = $state<string | null>(null);
  let metricBrowse = $state<EvalMetricBrowseEntry[]>([]);
  let metricBrowseError = $state<string | null>(null);
  let runMetricSpec = $state('');
  let runMinTestScore = $state('0.8');
  let runMaxCases = $state('50');
  let runError = $state<string | null>(null);
  let runResult = $state<EvalRunResponse | null>(null);
  let isRunning = $state(false);
  let openTraceErrorByExample = $state<Record<string, string>>({});
  let openTraceLoadingByExample = $state<Record<string, boolean>>({});
  let lastReviewedExample = $state<string | null>(null);

  const sortedCases = $derived.by(() => {
    if (!runResult) return [];
    return [...runResult.cases].sort((a, b) => a.score - b.score);
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

  async function loadDatasetBrowse(): Promise<void> {
    datasetBrowseError = null;
    const entries = await listEvalDatasets();
    if (!entries) {
      datasetBrowse = [];
      datasetBrowseError = 'Failed to load datasets.';
      return;
    }
    datasetBrowse = entries;
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
    if (!runMetricSpec && entries.length > 0) {
      runMetricSpec = entries[0].metric_spec;
    }
  }

  onMount(async () => {
    await Promise.all([loadTraces(), loadDatasetBrowse(), loadMetricBrowse()]);
  });

  async function addTag(trace: TraceSummary): Promise<void> {
    const rawTag = tagDrafts[trace.trace_id] ?? '';
    const tag = rawTag.trim();
    if (!tag) return;
    const updated = await setTraceTags(trace.trace_id, trace.session_id, [tag], []);
    if (!updated) return;
    traces = traces.map((item) => (item.trace_id === updated.trace_id ? updated : item));
    tagDrafts = { ...tagDrafts, [trace.trace_id]: '' };
  }

  async function removeTag(trace: TraceSummary, tag: string): Promise<void> {
    const updated = await setTraceTags(trace.trace_id, trace.session_id, [], [tag]);
    if (!updated) return;
    traces = traces.map((item) => (item.trace_id === updated.trace_id ? updated : item));
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
    traces = traces.map((item) => (item.trace_id === updated.trace_id ? updated : item));
  }

  function splitCsv(input: string): string[] {
    return input
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  async function onExportDataset(): Promise<void> {
    exportError = null;
    exportResult = null;
    const includeTags = splitCsv(exportIncludeTags);
    if (includeTags.length === 0) {
      exportError = 'Include at least one tag.';
      return;
    }
    const maybeLimit = Number(exportLimit || '0');
    const limit = Number.isFinite(maybeLimit) && maybeLimit > 0 ? maybeLimit : 0;
    const response = await exportEvalDataset({
      include_tags: includeTags,
      exclude_tags: splitCsv(exportExcludeTags),
      output_dir: exportOutputDir.trim(),
      limit
    });
    if (!response) {
      exportError = 'Dataset export failed.';
      return;
    }
    exportResult = response;
    datasetPath = response.dataset_path;
  }

  async function onLoadDataset(): Promise<void> {
    loadError = null;
    loadResult = null;
    const path = datasetPath.trim();
    if (!path) {
      loadError = 'Dataset path is required.';
      return;
    }
    const response = await loadEvalDataset(path);
    if (!response) {
      loadError = 'Dataset load failed.';
      return;
    }
    loadResult = response;
  }

  async function selectDataset(entry: EvalDatasetBrowseEntry): Promise<void> {
    datasetPath = entry.path;
    await onLoadDataset();
  }

  async function onRunEval(): Promise<void> {
    if (isRunning) return;
    runError = null;
    runResult = null;
    isRunning = true;
    const path = datasetPath.trim();
    if (!path) {
      runError = 'Dataset path is required before running eval.';
      isRunning = false;
      return;
    }
    if (!runMetricSpec.trim()) {
      runError = 'Metric selection is required before running eval.';
      isRunning = false;
      return;
    }
    const payload: { dataset_path: string; metric_spec: string; min_test_score?: number; max_cases?: number } = {
      dataset_path: path,
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
    isRunning = false;
  }

  async function onOpenTrace(caseRow: EvalRunResponse['cases'][number]): Promise<void> {
    openTraceErrorByExample = { ...openTraceErrorByExample, [caseRow.example_id]: '' };
    openTraceLoadingByExample = { ...openTraceLoadingByExample, [caseRow.example_id]: true };
    const payload = await fetchTrajectory(caseRow.pred_trace_id, caseRow.pred_session_id);
    if (!payload) {
      openTraceErrorByExample = {
        ...openTraceErrorByExample,
        [caseRow.example_id]: `Failed to open trace for ${caseRow.example_id}.`
      };
      openTraceLoadingByExample = { ...openTraceLoadingByExample, [caseRow.example_id]: false };
      return;
    }
    trajectoryStore.clearArtifacts();
    trajectoryStore.setFromPayload(payload);
    lastReviewedExample = caseRow.example_id;
    openTraceLoadingByExample = { ...openTraceLoadingByExample, [caseRow.example_id]: false };
  }
</script>

<div class="eval-tab eval-light">
  <h3 class="title">Eval</h3>

  <div class="eval-body" data-testid="eval-body">
    <div class="section">
      <h4>Trace Selection</h4>
      <p class="muted">Tag traces here, then use those tags when exporting datasets.</p>
      <div class="trace-list">
        {#if traceLoadError}
          <div class="trace-error">
            <p class="muted">Failed to load traces.</p>
            <button class="tag-action" onclick={loadTraces} aria-label="Retry trace load">Retry</button>
          </div>
        {:else if traces.length === 0}
          <p class="muted">No traces available yet.</p>
        {:else}
          {#each traces as trace (trace.trace_id)}
            <article class="trace-row">
              <div class="trace-meta">
                <span class="trace-id">{trace.trace_id}</span>
                <span class="session-id">{trace.session_id}</span>
              </div>
              <div class="tags">
                {#each trace.tags as tag (tag)}
                  <span class="tag">
                    <span>{tag}</span>
                    <button
                      class="tag-remove"
                      onclick={() => removeTag(trace, tag)}
                      aria-label={`Remove tag ${tag} ${trace.trace_id}`}
                    >
                      x
                    </button>
                  </span>
                {/each}
              </div>
              <div class="tag-editor">
                <label class="sr-only" for={`add-tag-${trace.trace_id}`}>Add tag for {trace.trace_id}</label>
                <input
                  id={`add-tag-${trace.trace_id}`}
                  class="tag-input"
                  placeholder="tag:value"
                  bind:value={tagDrafts[trace.trace_id]}
                />
                <button class="tag-action" onclick={() => addTag(trace)} aria-label={`Add tag ${trace.trace_id}`}>Add</button>
              </div>
              <div class="split-actions">
                <button class="tag-action" onclick={() => assignSplit(trace, 'split:val')} aria-label={`Mark val ${trace.trace_id}`}>
                  Mark Val
                </button>
                <button class="tag-action" onclick={() => assignSplit(trace, 'split:test')} aria-label={`Mark test ${trace.trace_id}`}>
                  Mark Test
                </button>
              </div>
            </article>
          {/each}
        {/if}
      </div>
    </div>

    <div class="section">
      <h4>Dataset</h4>
      <p class="muted">Preview reads the dataset from disk and does not import traces into memory.</p>

      <div class="dataset-grid">
        <div class="dataset-pane">
          <h5>Export from tagged traces</h5>
          <div class="form-grid">
            <label for="export-include-tags">Export include tags</label>
            <input id="export-include-tags" class="tag-input" bind:value={exportIncludeTags} placeholder="split:val, dataset:policy" />

            <label for="export-exclude-tags">Export exclude tags</label>
            <input id="export-exclude-tags" class="tag-input" bind:value={exportExcludeTags} placeholder="tag:skip" />

            <label for="export-output-dir">Export output directory</label>
            <input id="export-output-dir" class="tag-input" bind:value={exportOutputDir} placeholder="examples/evals/policy_compliance_v1/dataset" />

            <label for="export-limit">Export limit</label>
            <input id="export-limit" class="tag-input" bind:value={exportLimit} placeholder="0" />
          </div>
          <button class="tag-action" onclick={onExportDataset} aria-label="Export dataset">Export dataset</button>

          {#if exportError}
            <p class="muted error-text">{exportError}</p>
          {/if}
          {#if exportResult}
            <p class="muted">Exported {exportResult.trace_count} traces.</p>
            <p class="muted mono">{exportResult.dataset_path}</p>
            <p class="muted mono">{exportResult.manifest_path}</p>
          {/if}
        </div>
        <div class="dataset-pane">
          <h5>Browse datasets</h5>
          {#if datasetBrowseError}
            <div class="trace-error">
              <p class="muted error-text">{datasetBrowseError}</p>
              <button class="tag-action" onclick={loadDatasetBrowse} aria-label="Retry dataset browse">Retry</button>
            </div>
          {:else if datasetBrowse.length === 0}
            <p class="muted">No datasets found in this app's evals folder.</p>
          {:else}
            <div class="dataset-list">
              {#each datasetBrowse as entry (entry.path)}
                <button
                  class="dataset-item"
                  onclick={() => selectDataset(entry)}
                  aria-label={`Select dataset ${entry.label}`}
                  data-selected={datasetPath === entry.path}
                >
                  <span>{entry.label}</span>
                  {#if entry.is_default}
                    <span class="dataset-badge">default</span>
                  {/if}
                </button>
              {/each}
            </div>
          {/if}

          <div class="form-grid">
            <label for="dataset-path">Selected dataset path</label>
            <input id="dataset-path" class="tag-input" bind:value={datasetPath} placeholder="example_app/evals/.../dataset.jsonl" />
          </div>
          <button class="tag-action" onclick={onLoadDataset} aria-label="Preview dataset">Preview dataset</button>
          {#if loadError}
            <p class="muted error-text">{loadError}</p>
          {/if}
          {#if loadResult}
            <p class="muted">Loaded {loadResult.counts.total} examples.</p>
            <p class="muted mono">{loadResult.dataset_path}</p>
            <div class="dataset-summary">
              {#each Object.entries(loadResult.counts.by_split) as [split, count] (split)}
                <p class="muted">{split}: {count}</p>
              {/each}
              {#if loadResult.examples.length > 0}
                <p class="muted">{loadResult.examples[0]?.question}</p>
              {/if}
            </div>
          {/if}
        </div>
      </div>
    </div>

    <div class="section">
      <h4>Run</h4>
      <p class="muted">Run evaluates the currently staged dataset path with your metric spec.</p>
      <p class="muted mono">Staged dataset: {datasetPath || 'none'}</p>

      {#if isRunning}
        <div class="run-banner">
          <p class="muted">Running evaluation…</p>
        </div>
      {:else if runResult}
        <div class="run-banner">
          <p class="muted"><strong>Run completed</strong></p>
          <p class="muted">{runResult.counts.total} cases evaluated</p>
        </div>
      {:else if runError}
        <div class="run-banner">
          <p class="muted error-text">{runError}</p>
        </div>
      {/if}

      <div class="form-grid">
        <label for="run-metric-spec">Metric selection</label>
        <select id="run-metric-spec" class="tag-input" bind:value={runMetricSpec}>
          <option value="">Select a metric</option>
          {#each metricBrowse as metric (metric.metric_spec)}
            <option value={metric.metric_spec}>{metric.label}</option>
          {/each}
        </select>
        {#if metricBrowseError}
          <p class="muted error-text">{metricBrowseError}</p>
        {/if}
        {#if metricBrowse.length === 0 && !metricBrowseError}
          <p class="muted">No metrics discovered yet. Add an evaluate.spec.json with metric_spec.</p>
        {/if}

        <label for="run-min-test-score">Run min test score</label>
        <input id="run-min-test-score" class="tag-input" bind:value={runMinTestScore} placeholder="0.8" />

        <label for="run-max-cases">Run max cases</label>
        <input id="run-max-cases" class="tag-input" bind:value={runMaxCases} placeholder="50" />
      </div>

      <button class="tag-action" onclick={onRunEval} aria-label="Run evaluation" disabled={isRunning}>
        {isRunning ? 'Running evaluation…' : 'Run evaluation'}
      </button>
    </div>

    <div class="section">
      <h4>Results</h4>
      {#if runResult}
        <p class="muted">Run {runResult.run_id}</p>
        <p class="muted">Total: {runResult.counts.total} | val: {runResult.counts.val} | test: {runResult.counts.test}</p>
        {#if runResult.min_test_score !== null && runResult.min_test_score !== undefined}
          <p class="muted">Min test score: {runResult.min_test_score}</p>
        {/if}
        <p class="muted">Passed threshold: {runResult.passed_threshold ? 'yes' : 'no'}</p>

        {#if lastReviewedExample}
          <p class="muted">Trajectory loaded below for case {lastReviewedExample}.</p>
        {/if}

        <div class="result-list">
          {#each sortedCases as caseRow (caseRow.example_id)}
            <article class="trace-row">
              <p class="muted" data-testid="result-example-id">Case {caseRow.example_id}</p>
              <p class="muted">{caseRow.split.toUpperCase()} • {caseRow.score}</p>
              <p class="muted">{caseRow.question}</p>
              {#if caseRow.feedback}
                <p class="muted">{caseRow.feedback}</p>
              {/if}
              <button
                class="tag-action"
                aria-label={`Review trace ${caseRow.example_id}`}
                onclick={() => onOpenTrace(caseRow)}
                disabled={openTraceLoadingByExample[caseRow.example_id] === true}
              >
                {openTraceLoadingByExample[caseRow.example_id] === true ? 'Opening...' : 'Review trace'}
              </button>
              {#if openTraceErrorByExample[caseRow.example_id]}
                <p class="muted error-text">{openTraceErrorByExample[caseRow.example_id]}</p>
              {/if}
            </article>
          {/each}
        </div>
      {:else}
        <p class="muted">Completed eval runs appear here after you execute Run evaluation.</p>
      {/if}
    </div>
  </div>
</div>

<style>
  .eval-tab {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 8px 2px;
    flex: 1;
    min-height: 0;
  }

  .eval-body {
    display: flex;
    flex-direction: column;
    gap: 10px;
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

  .trace-list {
    margin-top: 8px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .trace-row {
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    padding: 10px;
    background: var(--color-card-bg, #fcfaf7);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .trace-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 12px;
  }

  .trace-id {
    font-weight: 700;
    color: var(--color-ink, #1e242c);
  }

  .session-id {
    color: var(--color-text-secondary, #3c3a36);
  }

  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .tag {
    display: inline-flex;
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
    border: 1px solid var(--color-border, #e8e1d7);
    color: var(--color-text-secondary, #3c3a36);
    background: var(--color-btn-ghost-bg, #f2eee8);
    gap: 6px;
    align-items: center;
  }

  .tag-remove {
    border: 0;
    background: transparent;
    color: var(--color-text-secondary, #3c3a36);
    cursor: pointer;
    padding: 0;
    font-size: 11px;
    line-height: 1;
  }

  .muted {
    margin: 0;
    font-size: 12px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .tag-editor {
    display: flex;
    gap: 6px;
  }

  .tag-input {
    flex: 1;
    min-width: 0;
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    background: var(--color-code-bg, #fbf8f3);
    color: var(--color-text-secondary, #3c3a36);
    padding: 8px 10px;
    font-size: 12px;
  }

  .tag-action {
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    background: var(--color-btn-ghost-bg, #f2eee8);
    color: var(--color-text-secondary, #3c3a36);
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
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

  .split-actions {
    display: flex;
    gap: 6px;
  }

  .trace-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .dataset-grid {
    margin-top: 8px;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }

  .dataset-pane {
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    background: var(--color-card-bg, #fcfaf7);
    padding: 10px;
  }

  .dataset-pane h5 {
    margin: 0;
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text-secondary, #3c3a36);
  }

  .dataset-list {
    margin-top: 8px;
    display: grid;
    gap: 6px;
  }

  .dataset-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    background: var(--color-card-bg, #fcfaf7);
    color: var(--color-text-secondary, #3c3a36);
    padding: 8px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .dataset-item[data-selected="true"] {
    border-color: var(--color-primary, #31a6a0);
  }

  .dataset-badge {
    border-radius: 999px;
    padding: 2px 6px;
    font-size: 10px;
    border: 1px solid var(--color-border, #e8e1d7);
    color: var(--color-text-secondary, #3c3a36);
    background: var(--color-btn-ghost-bg, #f2eee8);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .form-grid {
    margin: 8px 0;
    display: grid;
    gap: 4px;
  }

  .form-grid label {
    font-size: 11px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
    word-break: break-all;
  }

  .error-text {
    color: var(--color-error-text, #9b2d2d);
  }

  .dataset-summary {
    margin-top: 6px;
    display: grid;
    gap: 2px;
  }

  .run-banner {
    margin-top: 10px;
    padding: 8px 10px;
    border-radius: 10px;
    border: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-card-bg, #fcfaf7);
  }

  .result-list {
    margin-top: 8px;
    display: grid;
    gap: 8px;
  }

  @media (max-width: 900px) {
    .dataset-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
