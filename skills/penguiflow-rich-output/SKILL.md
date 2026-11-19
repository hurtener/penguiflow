---
name: penguiflow-rich-output
description: Add structured UI components (charts, tables, reports, grids, tabs, accordions, forms) to a PenguiFlow ReactPlanner — enable rich output via `attach_rich_output_nodes(registry, config=RichOutputConfig(enabled=True, allowlist=[...]))`, pick the right tool family (generic `render_component`, typed `render_*` wrappers like `render_chart_echarts`/`render_table`/`render_report`/`render_grid`/`render_tabs`/`render_accordion`, non-emitting `build_*` builders that return `artifact_ref`, interactive `ui_form`/`ui_confirm`/`ui_select_option`), describe schemas with `describe_component`, list artifacts with `list_artifacts`, and consume `artifact_chunk(artifact_type="ui_component")` on the frontend. Use when a user says "render a chart", "show a table", "rich output", "UI components", "interactive form", or names `render_component`, `RichOutputConfig`, `attach_rich_output_nodes`.
---

# PenguiFlow Rich Output

## When to use
- The agent needs to render charts, tables, reports, grids, tabs, accordions — not just markdown.
- You need interactive UI tools (`ui_form`, `ui_confirm`, `ui_select_option`) for structured user input.
- You want typed planner-side validation against a component registry.
- You want builder tools that compose reusable payloads referenced by `artifact_ref`.

## When NOT to use
- Frontend has no rich renderer → stick to text/markdown.
- A2A specialist serving downstream machines → use [[penguiflow-reactplanner-config]] step 9 (hide UI-only tools).
- Streaming raw tokens (not components) → use [[penguiflow-streaming]].
- Persisting binary artifacts (PDFs, images) → use [[penguiflow-statestore]] (`ArtifactStore`).

## Hard boundaries
This skill is the **rich-output tool surface** and component contract. The frontend renderer is your app's responsibility — this skill specifies what the backend emits and the wire shape, not how to draw it. The `penguiflow-reactplanner-config` skill no longer enumerates rich-output bullets; this skill owns them.

## Workflow

### 1) Enable rich output
```python
from penguiflow import ModelRegistry
from penguiflow.catalog import build_catalog
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes

registry = ModelRegistry()
rich_nodes = attach_rich_output_nodes(
    registry,
    config=RichOutputConfig(
        enabled=True,                              # default is False — must opt in
        allowlist=["markdown", "echarts", "datagrid", "report", "grid", "tabs", "accordion"],
        # include_prompt_catalog=True,             # library default
        # max_payload_bytes=250_000,               # library default
        # max_total_bytes=2_000_000,               # library default
    ),
)
catalog = build_catalog(list(rich_nodes), registry)
```

`RichOutputConfig.enabled=False` is the library default — nothing renders until you flip it. `allowlist` defaults to the entire `DEFAULT_ALLOWLIST` (markdown, json, echarts, mermaid, plotly, datagrid, metric, report, grid, tabs, accordion, code, latex, callout, image, video, form, confirm, select_option) — override it explicitly with the subset your frontend implements.

### 2) Always-visible set for tool discovery
Keep these visible regardless of deferral (see [[penguiflow-reactplanner-config]]): `render_component`, `describe_component`, `list_artifacts`, the interactive `ui_form`/`ui_confirm`/`ui_select_option` if enabled, plus any typed `render_*`/`build_*` you wired in.

### 3) Pick the right tool family
| Family | Tools | Visible? | Use for |
|---|---|---|---|
| Generic renderer | `render_component(component, props, id?, title?, metadata?)` | Yes | Any allowlisted component; escape hatch. Required for `markdown`, `json`, `mermaid`, `plotly`, `metric`, `code`, `latex`, `callout`, `image`, `video` (no typed wrappers). |
| Typed wrappers | `render_chart_echarts`, `render_report`, `render_table`, `render_grid`, `render_tabs`, `render_accordion` | Yes | Common components with stricter schemas. |
| Builders | `build_chart_echarts`, `build_table`, `build_grid`, `build_tabs`, `build_accordion` (no `build_report`/`build_markdown`) | No | Compose reusable payloads; returns `artifact_ref` for later inclusion. |
| Interactive (HITL) | `ui_form`, `ui_confirm`, `ui_select_option` | Yes (pauses planner) | Structured user input — pairs with [[penguiflow-hitl-pause-resume]]. |
| Introspection | `describe_component(name)`, `list_artifacts(...)` | Yes (no UI emit) | Discover what's renderable; list prior artifacts in this run. |

Prefer typed `render_*` over generic `render_component` when a wrapper exists — better validation, smaller LLM mistakes. Use `build_*` to assemble a complex child once and reference it from multiple parents.

### 4) Understand the wire contract
Every visible rich-output emission produces a planner event of shape:
```python
event_type = "artifact_chunk"
artifact_type = "ui_component"
payload = {
    "id": "<artifact id, optional>",
    "component": "echarts",
    "props": {...},                # component-specific schema
    "title": "<optional>",
}
```
This is what the frontend consumes. The planner event is identical regardless of the protocol your HTTP layer uses to deliver it.

Builders **don't** emit `artifact_chunk`. They register the payload in the in-run artifact registry and return:
```python
{"artifact_ref": "art_abc123"}
```
You then pass `artifact_ref` as a child slot in a parent renderer (e.g., a `tabs` panel referencing pre-built chart refs).

### 5) Handle interactive HITL tools
`ui_form`, `ui_confirm`, `ui_select_option` are pause tools. Calling one:
1. Emits the UI component as `artifact_chunk` (so the frontend renders it).
2. Pauses the planner with `reason="await_input"` and payload referencing the component.
3. Resumes when the user submits; `user_input` and `tool_context` carry the response.

Wire pause durability via [[penguiflow-hitl-pause-resume]]. Pause payload schema in `references/interactive-hitl.md`.

### 6) Use `describe_component(name)` for schema-on-demand
When the planner doesn't remember a component's prop schema, it calls `describe_component(name="echarts")` which returns the JSON schema. Always keep `describe_component` always-visible so the planner can recover from schema mistakes.

### 7) List artifacts mid-run
`list_artifacts(...)` returns artifacts produced earlier in the same planner run. Used to:
- Reference a `build_*` output from a later parent renderer.
- Show the user what they've already seen.
- Avoid recomputing the same chart.

### 8) Cross-channel rules
- For A2A specialists serving machines: hide rich-output tools via `ToolVisibilityPolicy` and set `tool_context["delivery_channel"]="a2a"` (see [[penguiflow-reactplanner-config]]).
- For embeddable widgets without a full UI: still emit `artifact_chunk`; the embed component subscribes only to it.
- For markdown-only fallback: enable `markdown` in allowlist and the planner can degrade gracefully.

## Troubleshooting (fast checks)
- **`render_component` says "component not allowlisted"** — add the component to `RichOutputConfig.allowlist`.
- **Frontend doesn't render** — confirm it handles `artifact_chunk` events with `artifact_type="ui_component"`.
- **Planner doesn't know a component** — make `describe_component` visible; the planner self-corrects.
- **Builder ref not found** — `artifact_ref` only lives within the **same run**; you can't reference it across `planner.run(...)` calls. Persist via [[penguiflow-statestore]] artifacts if needed.
- **Component validation fails on every call** — schema in `describe_component` and frontend renderer diverged; align both.
- **UI tools render but don't pause** — `pause_enabled=False` on the planner; flip it.
- **`ui_form` re-prompts after submit** — your resume `tool_context` didn't carry the input; pass it through (see [[penguiflow-hitl-pause-resume]] resume mechanics).
- **A2A caller gets empty answer** — UI-only tools were called for a machine consumer; apply visibility policy + textual `llm_context` guidance.

## Worked examples
- Integration tests under `tests/test_rich_output*.py` exercise the contract end-to-end.

## References (load only as needed)
- `references/components-and-render-tools.md` — every component in the canonical allowlist, prop schemas, `render_*` vs `build_*` choice.
- `references/builders-and-artifact-refs.md` — `build_*` tools, in-run artifact registry, ref composition patterns.
- `references/interactive-hitl.md` — `ui_form`/`ui_confirm`/`ui_select_option` pause payloads, resume flow, cross-link to [[penguiflow-hitl-pause-resume]].
