import type { Component } from 'svelte';
import type { Result } from '$lib/utils/result';
import { validateObjectProps, requireRecord, requireString } from './validation';

export interface RendererModule {
  default: Component<never>;
}

export interface RendererEntry {
  load: () => Promise<RendererModule>;
  validateProps: (props: unknown) => Result<Record<string, unknown>>;
}

const validateDefault = (props: unknown) => validateObjectProps(props);

const validateEcharts = (props: unknown) => {
  const base = validateObjectProps(props, ['option']);
  if (!base.ok) return base;
  return requireRecord(base.data, 'option');
};

const validateMermaid = (props: unknown) => {
  const base = validateObjectProps(props, ['code']);
  if (!base.ok) return base;
  return requireString(base.data, 'code');
};

export const rendererRegistry: Record<string, RendererEntry> = {
  echarts: { load: () => import('./ECharts.svelte'), validateProps: validateEcharts },
  mermaid: { load: () => import('./Mermaid.svelte'), validateProps: validateMermaid },
  plotly: { load: () => import('./Plotly.svelte'), validateProps: validateDefault },
  datagrid: { load: () => import('./DataGrid.svelte'), validateProps: validateDefault },
  json: { load: () => import('./Json.svelte'), validateProps: validateDefault },
  metric: { load: () => import('./Metric.svelte'), validateProps: validateDefault },
  markdown: { load: () => import('./Markdown.svelte'), validateProps: validateDefault },
  code: { load: () => import('./Code.svelte'), validateProps: validateDefault },
  latex: { load: () => import('./Latex.svelte'), validateProps: validateDefault },
  callout: { load: () => import('./Callout.svelte'), validateProps: validateDefault },
  form: { load: () => import('./Form.svelte'), validateProps: validateDefault },
  confirm: { load: () => import('./Confirm.svelte'), validateProps: validateDefault },
  select_option: { load: () => import('./internal/SelectOption.svelte'), validateProps: validateDefault },
  report: { load: () => import('./Report.svelte'), validateProps: validateDefault },
  grid: { load: () => import('./Grid.svelte'), validateProps: validateDefault },
  tabs: { load: () => import('./Tabs.svelte'), validateProps: validateDefault },
  accordion: { load: () => import('./Accordion.svelte'), validateProps: validateDefault },
  image: { load: () => import('./Image.svelte'), validateProps: validateDefault },
  html: { load: () => import('./Html.svelte'), validateProps: validateDefault },
  video: { load: () => import('./Video.svelte'), validateProps: validateDefault },
  embed: { load: () => import('./Embed.svelte'), validateProps: validateDefault }
};

export type RendererName = keyof typeof rendererRegistry;
