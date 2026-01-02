<script lang="ts">
  import { getComponentRegistryStore } from '$lib/stores';
  import type { Component } from 'svelte';
  import { rendererRegistry } from './registry';
  import { toError, type Result } from '$lib/utils/result';

  interface RendererProps {
    onResult?: (result: unknown) => void;
    [key: string]: unknown;
  }

  interface Props {
    component: string;
    props?: Record<string, unknown>;
    onResult?: (result: unknown) => void;
  }

  let { component, props = {}, onResult = undefined }: Props = $props();

  const componentRegistryStore = getComponentRegistryStore();
  const definition = $derived(componentRegistryStore.getComponent(component));
  const isInteractive = $derived(definition?.interactive ?? false);

  type RendererPayload = {
    Renderer: Component<RendererProps>;
    props: Record<string, unknown>;
  };

  const RendererPromise = $derived.by(async (): Promise<Result<RendererPayload>> => {
    const entry = rendererRegistry[component];
    if (!entry) {
      return { ok: false, error: new Error(`Renderer not found: ${component}`) };
    }
    try {
      const module = await entry.load();
      const validation = entry.validateProps(props);
      if (!validation.ok) {
        return validation as Result<RendererPayload>;
      }
      return {
        ok: true,
        data: {
          Renderer: module.default as Component<RendererProps>,
          props: validation.data
        }
      };
    } catch (err) {
      return { ok: false, error: toError(err, `Failed to load renderer: ${component}`) };
    }
  });
</script>

{#await RendererPromise}
  <div class="renderer-loading">Loading...</div>
{:then result}
  {#if result.ok}
    {@const Renderer = result.data.Renderer}
    <div
      class="artifact"
      class:interactive={isInteractive}
      data-component={component}
    >
      <Renderer {...result.data.props} {onResult} />
    </div>
  {:else}
    <div class="artifact-error">
      <strong>Failed to render {component}:</strong> {result.error.message}
      <pre>{JSON.stringify(props, null, 2)}</pre>
    </div>
  {/if}
{/await}

<style>
  .artifact {
    margin: 0.75rem 0;
    border-radius: 0.5rem;
    overflow: hidden;
    background: var(--artifact-bg, #ffffff);
    border: 1px solid var(--artifact-border, #e5e7eb);
  }

  .artifact.interactive {
    border-color: var(--interactive-border, #2563eb);
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
  }

  .artifact-error {
    padding: 1rem;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 0.5rem;
    color: #dc2626;
  }

  .artifact-error pre {
    margin-top: 0.5rem;
    font-size: 0.75rem;
    overflow-x: auto;
  }

  .renderer-loading {
    padding: 0.75rem 1rem;
    font-size: 0.85rem;
    color: #6b7280;
  }
</style>
