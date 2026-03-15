<script lang="ts">
  import { Card, Empty } from '$lib/components/composites';
  import { Pill } from '$lib/components/primitives';
  import { listTraces, setTraceTags } from '$lib/services/api';
  import { getSessionStore, getTrajectoryStore } from '$lib/stores';
  import Timeline from './Timeline.svelte';

  const sessionStore = getSessionStore();
  const trajectoryStore = getTrajectoryStore();

  let tags = $state<string[]>([]);
  let knownTags = $state<string[]>([]);
  let tagDraft = $state('');
  let tagError = $state<string | null>(null);

  const activeTraceId = $derived(trajectoryStore.traceId ?? sessionStore.activeTraceId);
  const activeSessionId = $derived(trajectoryStore.sessionId ?? null);

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
    {#if activeTraceId}
      <Pill variant="subtle" size="small">trace {activeTraceId.slice(0, 8)}</Pill>
    {/if}
  </div>
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
  {#if trajectoryStore.isEmpty}
    <Empty
      inline
      title="No trajectory yet"
      subtitle="Send a prompt to see steps."
    />
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

  :global(.trajectory-card) {
    flex: 0 1 auto;
    min-height: 100px;
    max-height: 40%;
    overflow-y: auto;
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
