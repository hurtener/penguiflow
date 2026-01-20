# RFC: Rich Output Wrapper Tools (Typed Convenience Surface)

## Goal

Provide a strongly-typed tool surface for UI rendering that is easier for weaker models to use than the generic `render_component(component, props)` contract, while keeping `render_component` as the single execution primitive.

This RFC describes the *surface* only (tool names + schemas + behavior), not an implementation.

## Motivation

Weak or non-tool-tuned models often fail on `render_component` because:

- The tool args schema is generic (`component: str`, `props: object`), so the model must infer the correct nested `props` shape for each component.
- The runtime validates `props` against per-component JSON schemas, causing repeated retries when the model guesses wrong.

Wrapper tools shift schema complexity from “hidden in component registry” into “explicit tool args schema”, which models follow more reliably.

## Non-Goals

- Remove or deprecate `render_component`.
- Add new UI components to the registry (this is about *wrappers*, not new component types).
- Change front-end rendering behavior.

## Proposed Tools

All wrapper tools are **pure** and internally forward to `render_component` with the correct `component` name and well-formed `props`.

### 1) `render_report`

Purpose: Document-like output with sections, text, charts, and tables.

**Args (typed)**
- `title: str`
- `subtitle: str | None`
- `sections: list[ReportSection]`
- `toc: bool = True`
- `metadata: dict[str, Any] | None`

**ReportSection**
- `title: str`
- `content: str | None` (markdown)
- `components: list[ComponentItem] | None`
- `subsections: list[ReportSection] | None`

**ComponentItem**
- `component: Literal["markdown","echarts","datagrid","metric","plotly","mermaid","callout", ...]`
- `props: dict[str, Any]`
- optional layout hints (`colSpan`, `rowSpan`, `caption`, etc.) if the registry supports them

Behavior:
- Validates full report payload via tool args schema *and* via registry validation.
- Emits one UI artifact (same as `render_component(report, props=...)`).

### 2) `render_chart_echarts`

Purpose: Make the most common visualization path trivial.

**Args (typed)**
- `title: str | None`
- `option: dict[str, Any]` (ECharts option object)
- `height: int | None`
- `theme: str | None`

Behavior:
- Wraps into `render_component(component="echarts", props={...})`.
- Keeps the schema narrow but still flexible (`option` stays “any JSON object”).

### 3) `render_table`

Purpose: Common tabular result display without guessing nested props.

**Args (typed)**
- `columns: list[DataGridColumn]`
- `rows: list[dict[str, Any]]`
- `page_size: int = 25` (or `0` for no pagination)
- `sortable: bool = True`
- `filterable: bool = True`

**DataGridColumn**
- `field: str`
- `header: str`
- `format: Literal["text","number","currency","percent","date","datetime"] | None`
- `width: int | None`

Behavior:
- Wraps into `render_component(component="datagrid", props={...})`.

### 4) `render_dashboard_grid`

Purpose: A layout wrapper for multi-panel dashboards.

**Args (typed)**
- `title: str | None`
- `columns: int = 2`
- `items: list[GridItem]`

**GridItem**
- `component: str`
- `props: dict[str, Any]`
- `col_span: int | None`
- `row_span: int | None`

Behavior:
- Wraps into `render_component(component="grid", props={...})`.

## Relationship to Existing Tools

Wrappers do not replace these:

- `render_component` remains the core primitive.
- `describe_component` remains available for schema introspection and advanced/custom payloads.
- `list_artifacts` remains the bridge for referencing tool artifacts inside components.

## Compatibility and Migration

- Existing prompts and flows continue to work unchanged.
- For weak models, prompts can prefer wrapper tools (“use `render_report` instead of `render_component(report, ...)`”).

## Risks

- Tool surface area grows (more schemas to maintain).
- Some wrappers may overlap with evolving component registry schemas; wrappers must be versioned or kept permissive where needed.

## Recommendation

Start with `render_report`, `render_chart_echarts`, and `render_table`. These cover the most failure-prone paths with the highest UX value, while keeping the wrapper surface minimal.

