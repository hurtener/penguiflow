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
    grid-template-columns: 240px 1fr;
    gap: 1rem;
    height: 100%;
  }

  .component-list {
    border-right: 1px solid #e5e7eb;
    padding-right: 0.75rem;
  }

  .component-scroll {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-height: 70vh;
    overflow-y: auto;
  }

  .component-item {
    text-align: left;
    padding: 0.5rem;
    border-radius: 0.5rem;
    border: 1px solid transparent;
    background: #f8fafc;
    cursor: pointer;
  }

  .component-item.active {
    border-color: #2563eb;
    background: #eff6ff;
  }

  .component-item .name {
    font-weight: 600;
  }

  .component-item .meta {
    display: block;
    font-size: 0.7rem;
    color: #64748b;
  }

  .component-detail header {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  .component-detail h2 {
    margin: 0;
  }

  .component-detail p {
    margin: 0.25rem 0 0;
    color: #64748b;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
  }

  .actions button {
    border: 1px solid #d1d5db;
    background: #ffffff;
    padding: 0.35rem 0.6rem;
    border-radius: 0.375rem;
    font-size: 0.75rem;
    cursor: pointer;
  }

  .detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
  }

  .schema, .example {
    background: #f8fafc;
    padding: 0.75rem;
    border-radius: 0.5rem;
  }

  pre {
    margin: 0.5rem 0 0;
    font-size: 0.7rem;
    white-space: pre-wrap;
  }

  .editor textarea {
    width: 100%;
    min-height: 140px;
    padding: 0.5rem;
    border-radius: 0.5rem;
    border: 1px solid #d1d5db;
    font-family: var(--font-mono, ui-monospace);
    font-size: 0.75rem;
  }

  .preview {
    margin-top: 1rem;
  }

  .error {
    color: #dc2626;
    margin-top: 0.25rem;
    font-size: 0.75rem;
  }

  .empty {
    padding: 1rem;
    color: #9ca3af;
  }
</style>
