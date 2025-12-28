<script lang="ts">
  import { componentRegistryStore } from '$lib/stores/component_registry.svelte';
  import ECharts from './renderers/ECharts.svelte';
  import Mermaid from './renderers/Mermaid.svelte';
  import Plotly from './renderers/Plotly.svelte';
  import DataGrid from './renderers/DataGrid.svelte';
  import Json from './renderers/Json.svelte';
  import Metric from './renderers/Metric.svelte';
  import Markdown from './renderers/Markdown.svelte';
  import Code from './renderers/Code.svelte';
  import Latex from './renderers/Latex.svelte';
  import Callout from './renderers/Callout.svelte';
  import Form from './renderers/Form.svelte';
  import Confirm from './renderers/Confirm.svelte';
  import SelectOption from './renderers/SelectOption.svelte';
  import Report from './renderers/Report.svelte';
  import Grid from './renderers/Grid.svelte';
  import Tabs from './renderers/Tabs.svelte';
  import Accordion from './renderers/Accordion.svelte';
  import Image from './renderers/Image.svelte';
  import Html from './renderers/Html.svelte';
  import Video from './renderers/Video.svelte';
  import Embed from './renderers/Embed.svelte';

  const renderers: Record<string, any> = {
    echarts: ECharts,
    mermaid: Mermaid,
    plotly: Plotly,
    datagrid: DataGrid,
    json: Json,
    metric: Metric,
    markdown: Markdown,
    code: Code,
    latex: Latex,
    callout: Callout,
    form: Form,
    confirm: Confirm,
    select_option: SelectOption,
    report: Report,
    grid: Grid,
    tabs: Tabs,
    accordion: Accordion,
    image: Image,
    html: Html,
    video: Video,
    embed: Embed
  };

  interface Props {
    component: string;
    props?: Record<string, unknown>;
    onResult?: (result: unknown) => void;
  }

  let { component, props = {}, onResult = undefined }: Props = $props();

  const Renderer = $derived(renderers[component]);
  const definition = $derived(componentRegistryStore.getComponent(component));
  const isInteractive = $derived(definition?.interactive ?? false);
</script>

{#if Renderer}
  <div
    class="artifact"
    class:interactive={isInteractive}
    data-component={component}
  >
    <Renderer
      {...props}
      {onResult}
    />
  </div>
{:else}
  <div class="artifact-error">
    <strong>Unknown component:</strong> {component}
    <pre>{JSON.stringify(props, null, 2)}</pre>
  </div>
{/if}

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
</style>
