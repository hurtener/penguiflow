<script lang="ts">
  import { getComponentRegistryStore, getInteractionsStore } from '$lib/stores';
  import ComponentRenderer from '$lib/renderers/ComponentRenderer.svelte';

  type ComponentEntry = {
    name: string;
    description: string;
    propsSchema: Record<string, unknown>;
    example?: { props?: Record<string, unknown>; description?: string };
    category: string;
    interactive: boolean;
  };

  const componentRegistryStore = getComponentRegistryStore();
  const interactionsStore = getInteractionsStore();
  const components = $derived(
    Object.values(componentRegistryStore.components || {}) as ComponentEntry[]
  );

  const sortedComponents = $derived.by(() =>
    [...components].sort((a, b) => `${a.category}-${a.name}`.localeCompare(`${b.category}-${b.name}`))
  );

  let selected = $state<string | null>(null);
  let payloadText = $state('');

  $effect(() => {
    if (!selected && sortedComponents.length) {
      const [first] = sortedComponents;
      if (first) {
        selected = first.name;
      }
    }
  });

  $effect(() => {
    if (selected && !payloadText) {
    const def = componentRegistryStore.getComponent(selected);
      const exampleProps = def?.example?.props ?? {};
      payloadText = JSON.stringify({ component: selected, props: exampleProps }, null, 2);
    }
  });

  const selectedDefinition = $derived.by(() =>
    selected ? componentRegistryStore.getComponent(selected) : undefined
  );

  type ParseResult = {
    preview: { component: string; props: Record<string, unknown> } | null;
    error: string | null;
  };

  const parseResult = $derived.by((): ParseResult => {
    try {
      const parsed = JSON.parse(payloadText);
      if (!parsed || typeof parsed !== 'object') {
        return { preview: null, error: 'Payload must be a JSON object.' };
      }
      if (!parsed.component || !parsed.props) {
        return { preview: null, error: 'Payload must include component and props keys.' };
      }
      return {
        preview: parsed as { component: string; props: Record<string, unknown> },
        error: null
      };
    } catch (err) {
      return {
        preview: null,
        error: err instanceof Error ? err.message : 'Invalid JSON.'
      };
    }
  });

  const preview = $derived(parseResult.preview);
  const parseError = $derived(parseResult.error);

  function useExample() {
    if (!selectedDefinition) return;
    payloadText = JSON.stringify(
      { component: selectedDefinition.name, props: selectedDefinition.example?.props ?? {} },
      null,
      2
    );
  }

  function useLastArtifact() {
    const last = interactionsStore.lastArtifact;
    if (!last) return;
    payloadText = JSON.stringify({ component: last.component, props: last.props }, null, 2);
  }

  function resetPayload() {
    payloadText = '';
  }

  function selectComponent(name: string) {
    selected = name;
    payloadText = '';
  }
</script>

<div class="component-lab">
  <aside class="component-list">
    <h3>Components</h3>
    <div class="component-scroll">
      {#each sortedComponents as comp}
        <button
          class={`component-item ${selected === comp.name ? 'active' : ''}`}
          onclick={() => selectComponent(comp.name)}
        >
          <span class="name">{comp.name}</span>
          <span class="meta">{comp.category}{comp.interactive ? ' • interactive' : ''}</span>
        </button>
      {/each}
    </div>
  </aside>

  <section class="component-detail">
    {#if selectedDefinition}
      <header>
        <div>
          <h2>{selectedDefinition.name}</h2>
          <p>{selectedDefinition.description}</p>
        </div>
        <div class="actions">
          <button onclick={useExample}>Use Example</button>
          <button onclick={useLastArtifact} disabled={!interactionsStore.lastArtifact}>Use Last Artifact</button>
          <button onclick={resetPayload}>Reset</button>
        </div>
      </header>

      <div class="detail-grid">
        <div class="schema">
          <h4>Props Schema</h4>
          <pre>{JSON.stringify(selectedDefinition.propsSchema, null, 2)}</pre>
        </div>
        <div class="example">
          <h4>Example</h4>
          <pre>{JSON.stringify(selectedDefinition.example?.props ?? {}, null, 2)}</pre>
        </div>
      </div>

      <div class="editor">
        <h4>Payload</h4>
        <textarea bind:value={payloadText}></textarea>
        {#if parseError}
          <div class="error">{parseError}</div>
        {/if}
      </div>

      <div class="preview">
        <h4>Preview</h4>
        {#if preview}
          <ComponentRenderer component={preview.component} props={preview.props} />
        {:else}
          <div class="empty">Fix payload to preview.</div>
        {/if}
      </div>
    {:else}
      <div class="empty">No registry loaded.</div>
    {/if}
  </section>
</div>

<style>
  .component-lab {
    display: grid;
    grid-template-columns: 200px 1fr;
    gap: 12px;
    flex: 1;
    overflow: hidden;
    padding: 10px;
  }

  .component-list {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--color-border, #e8e1d7);
    padding-right: 10px;
    overflow: hidden;
  }

  .component-list h3 {
    margin: 0 0 8px 0;
    font-size: 13px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
    flex-shrink: 0;
  }

  .component-scroll {
    display: flex;
    flex-direction: column;
    gap: 6px;
    flex: 1;
    overflow-y: auto;
  }

  .component-item {
    text-align: left;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid transparent;
    background: var(--color-code-bg, #fbf8f3);
    cursor: pointer;
  }

  .component-item:hover {
    background: var(--color-btn-ghost-bg, #f2eee8);
  }

  .component-item.active {
    border-color: var(--color-primary, #31a6a0);
    background: var(--color-tab-active-bg, #e8f6f2);
  }

  .component-item .name {
    font-weight: 600;
    font-size: 12px;
    color: var(--color-text, #1f1f1f);
  }

  .component-item .meta {
    display: block;
    font-size: 11px;
    color: var(--color-text-secondary, #3c3a36);
    margin-top: 2px;
  }

  .component-detail {
    display: flex;
    flex-direction: column;
    overflow-y: auto;
  }

  .component-detail header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 12px;
    flex-shrink: 0;
  }

  .component-detail h2 {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: var(--color-text, #1f1f1f);
  }

  .component-detail p {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--color-text-secondary, #3c3a36);
  }

  .actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }

  .actions button {
    border: 1px solid var(--color-border, #e8e1d7);
    background: var(--color-btn-ghost-bg, #f2eee8);
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    color: var(--color-text, #1f1f1f);
  }

  .actions button:hover {
    background: var(--color-btn-ghost-hover, #e8e4de);
  }

  .actions button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    flex-shrink: 0;
  }

  .schema, .example {
    background: var(--color-code-bg, #fbf8f3);
    padding: 10px;
    border-radius: 8px;
    border: 1px solid var(--color-border, #e8e1d7);
  }

  .schema h4, .example h4, .editor h4, .preview h4 {
    margin: 0 0 6px 0;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-secondary, #3c3a36);
  }

  pre {
    margin: 0;
    font-size: 11px;
    font-family: var(--font-mono, ui-monospace);
    white-space: pre-wrap;
    color: var(--color-text, #1f1f1f);
    max-height: 100px;
    overflow-y: auto;
  }

  .editor {
    margin-top: 12px;
    flex-shrink: 0;
  }

  .editor textarea {
    width: 100%;
    min-height: 100px;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid var(--color-border, #e8e1d7);
    font-family: var(--font-mono, ui-monospace);
    font-size: 11px;
    background: var(--color-code-bg, #fbf8f3);
    resize: vertical;
    outline: none;
  }

  .editor textarea:focus {
    border-color: var(--color-primary, #31a6a0);
  }

  .preview {
    margin-top: 12px;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    background: var(--color-code-bg, #fbf8f3);
    border: 1px solid var(--color-border, #e8e1d7);
    border-radius: 8px;
    padding: 10px;
  }

  .error {
    color: var(--color-error, #dc2626);
    margin-top: 6px;
    font-size: 11px;
  }

  .empty {
    padding: 16px;
    color: var(--color-text-secondary, #3c3a36);
    font-size: 12px;
    text-align: center;
  }
</style>
