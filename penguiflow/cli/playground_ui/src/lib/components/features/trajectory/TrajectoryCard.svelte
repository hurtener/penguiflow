<script lang="ts">
  import { Card, Empty } from '$lib/components/composites';
  import { Pill } from '$lib/components/primitives';
  import { listTraces, setTraceTags } from '$lib/services/api';
  import { getNotificationsStore, getSessionStore, getTrajectoryStore } from '$lib/stores';
  import type { TimelineStep } from '$lib/types';
  import DiffTreeNode from './DiffTreeNode.svelte';
  import Timeline from './Timeline.svelte';

  const sessionStore = getSessionStore();
  const trajectoryStore = getTrajectoryStore();
  const notificationsStore = getNotificationsStore();

  let tags = $state<string[]>([]);
  let knownTags = $state<string[]>([]);
  let tagDraft = $state('');
  let tagError = $state<string | null>(null);
  const activeTraceId = $derived(trajectoryStore.traceId ?? sessionStore.activeTraceId);
  const activeSessionId = $derived(trajectoryStore.sessionId ?? null);

  type NormalizedStep = {
    node: string;
    input: unknown;
    output: unknown;
    state: 'ok' | 'error';
  };
  type DiffTree = {
    key: string;
    kind: 'same' | 'diff' | 'mixed';
    reference?: string;
    actual?: string;
    children?: DiffTree[];
  };
  type DivergenceSection = {
    label: 'args' | 'result';
    tree: DiffTree;
    hasDiff: boolean;
  };
  type DivergenceRow = {
    index: number;
    stepName: string;
    thought?: string;
    latencyMs?: number;
    sections: DivergenceSection[];
    hasDiff: boolean;
    branchStop: boolean;
  };

  type DiffChange = {
    path: string;
    reference: unknown;
    actual: unknown;
  };

  function safeStringify(value: unknown): string {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return JSON.stringify({ error: 'Could not serialize value' }, null, 2);
    }
  }

  function timelineStepsToTrajectory(steps: TimelineStep[]): Record<string, unknown> {
    return {
      steps: steps.map((step) => {
        const action: Record<string, unknown> = { next_node: step.name };
        if (step.thought !== undefined) action.thought = step.thought;
        if (step.args !== undefined) action.args = step.args;
        const row: Record<string, unknown> = {
          action,
          observation: step.result ?? null
        };
        if (step.latencyMs !== undefined) row.latency_ms = step.latencyMs;
        if (step.status === 'error') row.error = true;
        return row;
      })
    };
  }

  function actualTrajectoryForCopy(): Record<string, unknown> {
    const comparison = trajectoryStore.evalComparison;
    if (hasComparisonForActive && comparison?.pred_trajectory && typeof comparison.pred_trajectory === 'object') {
      return comparison.pred_trajectory as Record<string, unknown>;
    }
    return timelineStepsToTrajectory(trajectoryStore.steps);
  }

  function referenceTrajectoryForCopy(): Record<string, unknown> {
    const comparison = trajectoryStore.evalComparison;
    if (comparison?.gold_trajectory && typeof comparison.gold_trajectory === 'object') {
      return comparison.gold_trajectory as Record<string, unknown>;
    }
    return timelineStepsToTrajectory(referenceTimelineSteps);
  }

  function isEqualValue(reference: unknown, actual: unknown): boolean {
    try {
      return JSON.stringify(reference) === JSON.stringify(actual);
    } catch {
      return reference === actual;
    }
  }

  function pushDiffs(reference: unknown, actual: unknown, path: string, changes: DiffChange[]): void {
    if (isEqualValue(reference, actual)) {
      return;
    }

    const referenceList = arrayValue(reference);
    const actualList = arrayValue(actual);
    if (referenceList && actualList) {
      const maxLength = Math.max(referenceList.length, actualList.length);
      for (let idx = 0; idx < maxLength; idx += 1) {
        const nextPath = `${path}[${idx}]`;
        pushDiffs(referenceList[idx], actualList[idx], nextPath, changes);
      }
      return;
    }

    const referenceObj = objectValue(reference);
    const actualObj = objectValue(actual);
    if (referenceObj && actualObj) {
      const keys = Array.from(new Set([...Object.keys(referenceObj), ...Object.keys(actualObj)])).sort((a, b) => a.localeCompare(b));
      for (const key of keys) {
        const nextPath = path ? `${path}.${key}` : key;
        pushDiffs(referenceObj[key], actualObj[key], nextPath, changes);
      }
      return;
    }

    changes.push({ path, reference, actual });
  }

  function serializeActualPayload(): string {
    return safeStringify({
      mode: 'actual',
      trace_id: activeTraceId,
      session_id: activeSessionId,
      trajectory: actualTrajectoryForCopy()
    });
  }

  function serializeReferencePayload(): string {
    const comparison = trajectoryStore.evalComparison;
    return safeStringify({
      mode: 'reference',
      gold_trace_id: comparison?.gold_trace_id ?? null,
      trajectory: referenceTrajectoryForCopy()
    });
  }

  function serializeDivergencePayload(): string {
    const referenceTrajectory = referenceTrajectoryForCopy();
    const actualTrajectory = actualTrajectoryForCopy();
    const referenceSteps = mapRawSteps((referenceTrajectory as { steps?: unknown }).steps);
    const actualSteps = mapRawSteps((actualTrajectory as { steps?: unknown }).steps);
    const total = Math.max(referenceSteps.length, actualSteps.length);
    const steps: Array<{
      index: number;
      status: 'same' | 'changed' | 'added' | 'removed';
      reference_node: string | null;
      actual_node: string | null;
      changes: DiffChange[];
    }> = [];
    let addedStepCount = 0;
    let removedStepCount = 0;
    let changedStepCount = 0;

    for (let idx = 0; idx < total; idx += 1) {
      const reference = referenceSteps[idx] ?? null;
      const actual = actualSteps[idx] ?? null;
      if (reference == null && actual == null) {
        continue;
      }
      const referenceNode = reference ? nodeName(reference) : null;
      const actualNode = actual ? nodeName(actual) : null;
      if (reference == null && actual) {
        addedStepCount += 1;
        steps.push({
          index: idx + 1,
          status: 'added',
          reference_node: null,
          actual_node: actualNode,
          changes: [{ path: 'step', reference: null, actual }]
        });
        continue;
      }
      if (reference && actual == null) {
        removedStepCount += 1;
        steps.push({
          index: idx + 1,
          status: 'removed',
          reference_node: referenceNode,
          actual_node: null,
          changes: [{ path: 'step', reference, actual: null }]
        });
        continue;
      }

      const changes: DiffChange[] = [];
      pushDiffs(reference, actual, '', changes);
      const normalized = changes.map((entry) => ({
        path: entry.path || 'step',
        reference: entry.reference,
        actual: entry.actual
      }));
      if (normalized.length > 0) {
        changedStepCount += 1;
      }
      steps.push({
        index: idx + 1,
        status: normalized.length > 0 ? 'changed' : 'same',
        reference_node: referenceNode,
        actual_node: actualNode,
        changes: normalized
      });
    }

    const comparison = trajectoryStore.evalComparison;
    return safeStringify({
      mode: 'divergence',
      reference_trace_id: comparison?.gold_trace_id ?? null,
      actual_trace_id: activeTraceId,
      summary: {
        reference_step_count: referenceSteps.length,
        actual_step_count: actualSteps.length,
        changed_step_count: changedStepCount,
        added_step_count: addedStepCount,
        removed_step_count: removedStepCount
      },
      steps
    });
  }

  async function copyText(value: string, successMessage: string): Promise<void> {
    const clipboard = globalThis.navigator?.clipboard;
    if (!clipboard || typeof clipboard.writeText !== 'function') {
      notificationsStore.add('Clipboard is unavailable in this environment.', 'warning');
      return;
    }
    try {
      await clipboard.writeText(value);
      notificationsStore.add(successMessage, 'success');
    } catch {
      notificationsStore.add('Failed to copy trajectory text.', 'error');
    }
  }

  async function copyActualTrajectory(): Promise<void> {
    await copyText(serializeActualPayload(), 'Copied actual trajectory.');
  }

  async function copyReferenceTrajectory(): Promise<void> {
    await copyText(serializeReferencePayload(), 'Copied reference trajectory.');
  }

  async function copyDivergence(): Promise<void> {
    await copyText(serializeDivergencePayload(), 'Copied divergence diff.');
  }

  async function copyCurrentTrajectory(): Promise<void> {
    if (!hasComparisonForActive) {
      await copyActualTrajectory();
      return;
    }
    if (trajectoryStore.trajectoryViewMode === 'reference') {
      await copyReferenceTrajectory();
      return;
    }
    if (trajectoryStore.trajectoryViewMode === 'divergence') {
      await copyDivergence();
      return;
    }
    await copyActualTrajectory();
  }

  function nodeName(step: Record<string, unknown> | null): string {
    if (!step) return '—';
    const action = (step.action ?? {}) as Record<string, unknown>;
    const nextNode = action.next_node;
    if (typeof nextNode === 'string' && nextNode.trim()) {
      return nextNode;
    }
    const plan = action.plan;
    if (Array.isArray(plan) && plan[0] && typeof plan[0] === 'object') {
      const first = plan[0] as Record<string, unknown>;
      if (typeof first.node === 'string' && first.node.trim()) {
        return first.node;
      }
    }
    return 'step';
  }

  function renderValue(value: unknown): string {
    if (value === undefined) return 'none';
    if (value === null) return 'null';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
      return String(value);
    }
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }

  function valueDigest(value: unknown): string {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value ?? '');
    }
  }

  function isObjectLike(value: unknown): value is Record<string, unknown> {
    return value != null && typeof value === 'object' && !Array.isArray(value);
  }

  function objectValue(value: unknown): Record<string, unknown> | null {
    return isObjectLike(value) ? value : null;
  }

  function arrayValue(value: unknown): unknown[] | null {
    return Array.isArray(value) ? value : null;
  }

  function normalizeStep(step: Record<string, unknown>): NormalizedStep {
    const action = (step.action ?? {}) as Record<string, unknown>;
    return {
      node: nodeName(step),
      input: action.args ?? null,
      output: step.observation ?? null,
      state: step.error ? 'error' : 'ok'
    };
  }

  function mapRawSteps(raw: unknown): Record<string, unknown>[] {
    return Array.isArray(raw) ? (raw as Record<string, unknown>[]) : [];
  }

  function buildDiffTreeNode(key: string, reference: unknown, actual: unknown): DiffTree {
    if (valueDigest(reference) === valueDigest(actual)) {
      return { key, kind: 'same' };
    }

    const referenceList = arrayValue(reference);
    const actualList = arrayValue(actual);
    if (referenceList && actualList) {
      const maxLength = Math.max(referenceList.length, actualList.length);
      const children = Array.from({ length: maxLength }, (_, idx) => buildDiffTreeNode(`item ${idx}`, referenceList[idx], actualList[idx]));
      if (!children.some((child) => child.kind !== 'same')) {
        return { key, kind: 'same' };
      }
      return { key, kind: 'mixed', children };
    }

    const referenceObj = objectValue(reference);
    const actualObj = objectValue(actual);
    if (referenceObj && actualObj) {
      const keys = Array.from(new Set([...Object.keys(referenceObj), ...Object.keys(actualObj)])).sort((a, b) => a.localeCompare(b));
      const children = keys.map((childKey) => buildDiffTreeNode(childKey, referenceObj[childKey], actualObj[childKey]));
      if (!children.some((child) => child.kind !== 'same')) {
        return { key, kind: 'same' };
      }
      return { key, kind: 'mixed', children };
    }

    return {
      key,
      kind: 'diff',
      reference: renderValue(reference),
      actual: renderValue(actual)
    };
  }

  function sectionDiff(label: DivergenceSection['label'], reference: unknown, actual: unknown): DivergenceSection {
    const tree = buildDiffTreeNode(label, reference, actual);
    return {
      label,
      tree,
      hasDiff: tree.kind !== 'same'
    };
  }

  function routeValue(step: NormalizedStep): string | null {
    const args = step.input;
    if (isObjectLike(args) && typeof args.route === 'string') {
      return args.route;
    }
    const output = step.output;
    if (isObjectLike(output) && typeof output.route === 'string') {
      return output.route;
    }
    return null;
  }

  function mergedStepRow(
    index: number,
    reference: NormalizedStep,
    actual: NormalizedStep,
    referenceRaw: Record<string, unknown>,
    actualRaw: Record<string, unknown>
  ): DivergenceRow {
    const sections: DivergenceSection[] = [
      sectionDiff('args', reference.input, actual.input),
      sectionDiff('result', reference.output, actual.output)
    ];
    const refRoute = routeValue(reference);
    const actRoute = routeValue(actual);
    const structuralDiff = reference.node !== actual.node || reference.state !== actual.state;
    const routeDiff = refRoute !== null && actRoute !== null && refRoute !== actRoute;
    const branchStop = reference.node !== actual.node || routeDiff;
    const hasDiff = structuralDiff || routeDiff || sections.some((section) => section.hasDiff);
    const stepName = reference.node === actual.node ? actual.node : `${reference.node} -> ${actual.node}`;
    const actualAction = (actualRaw.action ?? {}) as Record<string, unknown>;
    const referenceAction = (referenceRaw.action ?? {}) as Record<string, unknown>;
    const thought = typeof actualAction.thought === 'string'
      ? actualAction.thought
      : (typeof referenceAction.thought === 'string' ? referenceAction.thought : undefined);
    const latencyMs = typeof actualRaw.latency_ms === 'number'
      ? actualRaw.latency_ms
      : (typeof referenceRaw.latency_ms === 'number' ? referenceRaw.latency_ms : undefined);
    return {
      index,
      stepName,
      thought,
      latencyMs,
      sections,
      hasDiff,
      branchStop
    };
  }

  function buildDivergenceRows(referenceRaw: unknown, actualRaw: unknown): DivergenceRow[] {
    const referenceStepRaw = mapRawSteps(referenceRaw);
    const actualStepRaw = mapRawSteps(actualRaw);
    const referenceSteps = referenceStepRaw.map(normalizeStep);
    const actualSteps = actualStepRaw.map(normalizeStep);
    const rows: DivergenceRow[] = [];
    const total = Math.max(referenceSteps.length, actualSteps.length);
    for (let idx = 0; idx < total; idx += 1) {
      const reference = referenceSteps[idx] ?? null;
      const actual = actualSteps[idx] ?? null;
      if (!reference || !actual) {
        rows.push({
          index: idx + 1,
          stepName: 'step',
          thought: undefined,
          latencyMs: undefined,
          sections: [{
            label: 'args',
            tree: {
              key: 'step',
              kind: 'diff',
              reference: reference ? 'present' : 'none',
              actual: actual ? 'present' : 'none'
            },
            hasDiff: true
          }],
          hasDiff: true,
          branchStop: true
        });
        break;
      }
      const row = mergedStepRow(
        idx + 1,
        reference,
        actual,
        referenceStepRaw[idx] ?? {},
        actualStepRaw[idx] ?? {}
      );
      rows.push(row);
      if (row.branchStop) {
        break;
      }
    }
    return rows;
  }

  function toTimelineSteps(raw: unknown, prefix: string): TimelineStep[] {
    return mapRawSteps(raw).map((step, idx) => {
      const action = (step.action ?? {}) as Record<string, unknown>;
      return {
        id: `${prefix}-${idx}`,
        name: nodeName(step),
        thought: typeof action.thought === 'string' ? action.thought : undefined,
        args: isObjectLike(action.args) ? action.args : undefined,
        result: isObjectLike(step.observation) ? step.observation : undefined,
        latencyMs: typeof step.latency_ms === 'number' ? step.latency_ms : undefined,
        reflectionScore: undefined,
        status: step.error ? 'error' : 'ok'
      } satisfies TimelineStep;
    });
  }

  const hasComparisonForActive = $derived.by(() => {
    const selection = trajectoryStore.evalCaseSelection;
    const comparison = trajectoryStore.evalComparison;
    if (!selection || !comparison || !activeTraceId) {
      return false;
    }
    return selection.predTraceId === activeTraceId && selection.score < selection.threshold && comparison.gold_trajectory != null;
  });

  const referenceTimelineSteps = $derived.by(() => {
    const comparison = trajectoryStore.evalComparison;
    const goldTrajectory = comparison?.gold_trajectory as Record<string, unknown> | undefined;
    return toTimelineSteps(goldTrajectory?.steps, 'reference');
  });

  const divergenceRows = $derived.by(() => {
    const comparison = trajectoryStore.evalComparison;
    if (!comparison) {
      return [] as DivergenceRow[];
    }
    const goldTrajectory = comparison.gold_trajectory as Record<string, unknown> | undefined;
    const predTrajectory = comparison.pred_trajectory as Record<string, unknown> | undefined;
    return buildDivergenceRows(goldTrajectory?.steps, predTrajectory?.steps);
  });

  async function refreshTags(): Promise<void> {
    if (!activeTraceId || !activeSessionId) {
      tags = [];
      tagError = null;
      return;
    }
    const rows = await listTraces(200);
    if (!rows) {
      tagError = 'Failed to load trace tags.';
      return;
    }
    knownTags = Array.from(new Set(rows.flatMap((row) => row.tags))).sort();
    const match = rows.find((row) => row.trace_id === activeTraceId && row.session_id === activeSessionId);
    tags = match?.tags ?? [];
    tagError = null;
  }

  async function applyTags(add: string[] = [], remove: string[] = []): Promise<void> {
    if (!activeTraceId || !activeSessionId) {
      tagError = 'Load a trajectory before tagging.';
      return;
    }
    const updated = await setTraceTags(activeTraceId, activeSessionId, add, remove);
    if (!updated) {
      tagError = 'Failed to update trace tags.';
      return;
    }
    tags = updated.tags;
    tagError = null;
  }

  async function addTag(): Promise<void> {
    const tag = tagDraft.trim();
    if (!tag) return;
    if (tags.includes(tag)) {
      tagDraft = '';
      return;
    }
    await applyTags([tag], []);
    tagDraft = '';
  }

  async function onTagEditorKeydown(event: KeyboardEvent): Promise<void> {
    if (event.key !== 'Enter' && event.key !== ',') {
      return;
    }
    event.preventDefault();
    await addTag();
  }

  async function removeTag(tag: string): Promise<void> {
    await applyTags([], [tag]);
  }

  $effect(() => {
    if (!activeTraceId || !activeSessionId) {
      tags = [];
      tagError = null;
      return;
    }
    void refreshTags();
  });

</script>

<Card class="trajectory-card">
  <div class="trajectory-header">
    <div class="title-small">Execution Trajectory</div>
    <div class="header-actions">
      {#if activeTraceId}
        <Pill variant="subtle" size="small">trace {activeTraceId.slice(0, 8)}</Pill>
      {/if}
      <button class="compact-action-btn" aria-label="Copy trajectory text" onclick={copyCurrentTrajectory}>Copy</button>
    </div>
  </div>
  {#if hasComparisonForActive}
    <div class="view-toggle" data-testid="trajectory-view-toggle">
      <button
        class="tag-action"
        data-active={trajectoryStore.trajectoryViewMode === 'actual'}
        aria-label="Actual trajectory"
        onclick={() => trajectoryStore.setTrajectoryViewMode('actual')}
      >
        Actual
      </button>
      <button
        class="tag-action"
        data-active={trajectoryStore.trajectoryViewMode === 'reference'}
        aria-label="Reference trajectory"
        onclick={() => trajectoryStore.setTrajectoryViewMode('reference')}
      >
        Reference
      </button>
      <button
        class="tag-action"
        data-active={trajectoryStore.trajectoryViewMode === 'divergence'}
        aria-label="Trajectory divergence"
        onclick={() => trajectoryStore.setTrajectoryViewMode('divergence')}
      >
        Divergence
      </button>
    </div>
  {/if}

  <div class="tag-inline" data-testid="trajectory-tag-inline">
    <span class="label">Tags:</span>
    <div class="tag-line">
      {#each tags as tag (tag)}
        <span class="tag-chip">
          <span>{tag}</span>
          <button class="tag-remove" onclick={() => removeTag(tag)} aria-label={`Remove active tag ${tag}`}>x</button>
        </span>
      {/each}
      <label class="sr-only" for="active-trace-tag-input">Edit tags for active trajectory</label>
      <input
        id="active-trace-tag-input"
        class="tag-input"
        placeholder="type tag and press Enter"
        bind:value={tagDraft}
        disabled={!activeTraceId || !activeSessionId}
        list="active-trace-tag-suggestions"
        onkeydown={onTagEditorKeydown}
      />
      <datalist id="active-trace-tag-suggestions">
        {#each knownTags as knownTag (knownTag)}
          <option value={knownTag}></option>
        {/each}
      </datalist>
    </div>
    {#if tagError}
      <p class="error-text">{tagError}</p>
    {/if}
  </div>

  {#if hasComparisonForActive && trajectoryStore.trajectoryViewMode === 'reference'}
    {#if referenceTimelineSteps.length === 0}
      <p class="muted">Reference trajectory unavailable.</p>
    {:else}
      <Timeline steps={referenceTimelineSteps} />
    {/if}
  {:else if hasComparisonForActive && trajectoryStore.trajectoryViewMode === 'divergence'}
    <div class="diff-panel" data-testid="trajectory-diff-panel">
      {#if trajectoryStore.evalComparisonLoading}
        <p class="muted">Loading divergence...</p>
      {:else if trajectoryStore.evalComparisonError}
        <p class="error-text">{trajectoryStore.evalComparisonError}</p>
      {:else if divergenceRows.length === 0}
        <p class="muted">No divergence data available.</p>
      {:else}
        <div class="divergence-timeline">
          {#each divergenceRows as row (row.index)}
            <div class="timeline-item">
              <div class="line"></div>
              <div class="dot {row.hasDiff ? 'error' : ''}"></div>
              <article class="timeline-body" data-diff={row.hasDiff} data-branch-stop={row.branchStop}>
                <div class="row space-between align-center">
                  <div class="step-name">{row.stepName}</div>
                  {#if row.latencyMs}
                    <Pill variant="subtle" size="small">{row.latencyMs} ms</Pill>
                  {/if}
                </div>
                {#if row.thought}
                  <div class="thought">"{row.thought}"</div>
                {/if}
                <details class="step-details" open={row.hasDiff}>
                  <summary>Details</summary>
                  <div class="token-tree">
                    {#each row.sections as section (`${row.index}-${section.label}`)}
                      <DiffTreeNode node={section.tree} />
                    {/each}
                  </div>
                </details>
              </article>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {:else if trajectoryStore.isEmpty}
    <Empty inline title="No trajectory yet" subtitle="Send a prompt to see steps." />
  {:else}
    {#key sessionStore.activeTraceId}
      <Timeline steps={trajectoryStore.steps} />
    {/key}
  {/if}
</Card>

<style>
  .trajectory-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }

  .title-small {
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  :global(.trajectory-card) {
    flex: 0 1 auto;
    min-height: 100px;
    max-height: 40%;
    overflow-y: auto;
  }

  .view-toggle {
    display: flex;
    gap: 6px;
    margin-bottom: 8px;
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

  .tag-action[data-active='true'] {
    background: #edf8f7;
    border-color: var(--color-primary, #31a6a0);
  }

  .tag-inline {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 6px 8px;
    border: 1px solid var(--color-border, #e4ddd2);
    border-radius: 10px;
    background: #fffdf9;
    margin-bottom: 10px;
  }

  .label {
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text-secondary, #5f5a51);
  }

  .tag-line {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
    flex: 1;
    min-width: 0;
  }

  .tag-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border: 1px solid var(--color-border, #ddd5c8);
    border-radius: 999px;
    padding: 2px 8px;
    background: #fff;
    font-size: 11px;
  }

  .tag-input {
    width: 180px;
    border: 1px solid var(--color-border, #d9d0c4);
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 12px;
    color: var(--color-text, #1f1f1f);
    background: #fff;
  }

  .tag-remove {
    border: none;
    background: transparent;
    color: var(--color-text-secondary, #6b6255);
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }

  .diff-panel {
    border: 1px solid var(--color-border, #e4ddd2);
    border-radius: 10px;
    padding: 8px;
    background: #fffdf9;
    margin-bottom: 10px;
  }

  .divergence-timeline {
    padding: 4px 2px;
  }

  .timeline-item {
    position: relative;
    padding-left: 24px;
    padding-bottom: 12px;
  }

  .line {
    position: absolute;
    left: 6px;
    top: 12px;
    width: 2px;
    height: calc(100% - 4px);
    background: var(--color-border, #e8e1d7);
  }

  .timeline-item:last-child .line {
    display: none;
  }

  .dot {
    position: absolute;
    left: 2px;
    top: 4px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--color-primary, #31a6a0);
    border: 2px solid var(--color-card-bg, #fcfaf7);
  }

  .dot.error {
    background: var(--color-error-accent, #b24c4c);
  }

  .timeline-body {
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 10px;
    padding: 10px;
    display: grid;
    gap: 8px;
  }

  .timeline-body[data-diff='true'] {
    border-color: #d9a1a1;
    background: #fff7f7;
  }

  .row {
    display: flex;
  }

  .space-between {
    justify-content: space-between;
  }

  .align-center {
    align-items: center;
  }

  .step-name {
    font-weight: 600;
    font-size: 12px;
    color: var(--color-text, #1f1f1f);
  }

  .thought {
    font-size: 11px;
    font-style: italic;
    color: var(--color-muted, #6b665f);
    margin-top: -2px;
  }

  .step-details {
    margin-top: 2px;
  }

  .token-tree {
    display: grid;
    gap: 4px;
    margin-top: 4px;
  }

  .step-details summary {
    font-size: 10px;
    color: var(--color-muted, #7a756d);
    cursor: pointer;
  }

  .muted {
    margin: 0;
    font-size: 12px;
    color: var(--color-text-secondary, #5f5a51);
  }

  .error-text {
    margin: 0;
    font-size: 12px;
    color: #a33434;
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
