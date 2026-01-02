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
  }

  .json-viewer.dark {
    background: #0f172a;
    color: #e2e8f0;
  }

  .json-row {
    display: flex;
    gap: 0.5rem;
    margin: 0.15rem 0;
  }

  .json-key {
    color: #2563eb;
  }

  .json-viewer.dark .json-key {
    color: #93c5fd;
  }

  .json-sep {
    color: #94a3b8;
  }

  .json-children {
    margin-left: 1rem;
    border-left: 1px dashed #e2e8f0;
    padding-left: 0.75rem;
  }

  .json-viewer.dark .json-children {
    border-left-color: #334155;
  }

  .json-primitive {
    color: #0f172a;
  }

  .json-viewer.dark .json-primitive {
    color: #e2e8f0;
  }

  summary {
    cursor: pointer;
    color: #64748b;
  }

  .json-viewer.dark summary {
    color: #94a3b8;
  }
</style>
