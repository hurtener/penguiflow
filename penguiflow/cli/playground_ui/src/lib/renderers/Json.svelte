<script lang="ts">
  import Json from './Json.svelte';

  interface Props {
    data: unknown;
    expandLevel?: number;
    sortKeys?: boolean;
    theme?: 'light' | 'dark';
    level?: number;
  }

  let { data, expandLevel = 2, sortKeys = false, theme = 'light', level = 0 }: Props = $props();

  const isObject = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);

  const isArray = (value: unknown): value is unknown[] => Array.isArray(value);

  const entries = $derived.by(() => {
    if (isObject(data)) {
      const keys = Object.keys(data);
      if (sortKeys) keys.sort();
      return keys.map((key) => [key, data[key]] as [string, unknown]);
    }
    if (isArray(data)) {
      return data.map((value, idx) => [String(idx), value] as [string, unknown]);
    }
    return [];
  });
</script>

<div class={`json-viewer ${theme}`}>
  {#if isObject(data) || isArray(data)}
    <details open={level < expandLevel}>
      <summary>
        {isArray(data) ? `Array(${entries.length})` : `Object(${entries.length})`}
      </summary>
      <div class="json-children">
        {#each entries as [key, value]}
          <div class="json-row">
            <span class="json-key">{key}</span>
            <span class="json-sep">:</span>
            <div class="json-value">
              {#if typeof value === 'object' && value !== null}
                <Json data={value} {expandLevel} {sortKeys} {theme} level={level + 1} />
              {:else}
                <span class={`json-primitive type-${typeof value}`}>
                  {JSON.stringify(value)}
                </span>
              {/if}
            </div>
          </div>
        {/each}
      </div>
    </details>
  {:else}
    <span class={`json-primitive type-${typeof data}`}>
      {JSON.stringify(data)}
    </span>
  {/if}
</div>

<style>
  .json-viewer {
    font-family: var(--font-mono, ui-monospace);
    font-size: 0.8125rem;
    padding: 0.75rem 1rem;
    background: var(--color-card-bg, #fcfaf7);
    color: var(--color-text, #1f1f1f);
    border: 1px solid var(--color-border, #f0ebe4);
    border-radius: var(--radius-xl, 16px);
  }

  .json-viewer.dark {
    background: #0f172a;
    color: #e2e8f0;
    border-color: #334155;
  }

  .json-row {
    display: flex;
    gap: 0.5rem;
    margin: 0.15rem 0;
  }

  .json-key {
    color: var(--color-primary-text, #1f6c68);
  }

  .json-viewer.dark .json-key {
    color: #93c5fd;
  }

  .json-sep {
    color: var(--color-muted-light, #8a847c);
  }

  .json-children {
    margin-left: 1rem;
    border-left: 1px dashed var(--color-border, #f0ebe4);
    padding-left: 0.75rem;
  }

  .json-viewer.dark .json-children {
    border-left-color: #334155;
  }

  .json-primitive {
    color: var(--color-text, #1f1f1f);
  }

  .json-viewer.dark .json-primitive {
    color: #e2e8f0;
  }

  summary {
    cursor: pointer;
    color: var(--color-muted, #6b665f);
  }

  .json-viewer.dark summary {
    color: #94a3b8;
  }
</style>
