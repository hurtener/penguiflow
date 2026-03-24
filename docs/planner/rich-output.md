# Rich output (UI components, builders, and interactive HITL)

## What it is / when to use it

“Rich output” is PenguiFlow’s typed tool surface for producing **structured UI artifacts** instead of only plain text.

Use it when you have a frontend that can render PenguiFlow UI artifacts and you want:

- charts, tables, reports, grids, tabs, and accordions instead of markdown-only responses
- reusable UI payloads that can be built once and referenced later by `artifact_ref`
- planner-side validation against a component registry
- interactive approval or data-entry steps (`form`, `confirm`, `select_option`)

Rich output now has three distinct authoring modes:

1. `render_component(...)`
   The generic escape hatch for any allowlisted component.
2. Typed `render_*` wrappers
   The preferred visible-render path for common components.
3. Typed `build_*` builders
   The preferred non-emitting path for complex child components you want to compose later.

## Non-goals / boundaries

- Rich output does not ship a frontend. Your app must render `artifact_chunk` events.
- Rich output is not a free-form UI framework. The backend owns validation and the frontend owns renderer implementations.
- Rich output does not replace binary artifacts/resources. Large or opaque payloads should still live in the artifact store.
- `build_*` tools are not general persistence APIs. They are in-run reusable component builders backed by the planner’s artifact registry.

## Contract surface

### Core runtime pieces

The main implementation surfaces are:

- `penguiflow.rich_output.runtime`
- `penguiflow.rich_output.nodes`
- `penguiflow.rich_output.registry`
- `penguiflow.rich_output.validate`
- `penguiflow.planner.artifact_registry`

### The downstream frontend contract does not change

Visible UI still reaches the frontend only through planner artifact streaming:

- planner event type: `artifact_chunk`
- `artifact_type="ui_component"`
- chunk payload:
  - `id?: str`
  - `component: str`
  - `props: object`
  - `title?: str`

That is the only contract the frontend must implement for visible rich output.

Builder tools do **not** emit any visible frontend artifact. They only register reusable component payloads in the in-run artifact registry and return an `artifact_ref`.

### Enabling rich output

Enable rich output by:

1. configuring the runtime
2. attaching rich output nodes to the planner catalog

Canonical pattern:

```python
from penguiflow import ModelRegistry
from penguiflow.catalog import build_catalog
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes

registry = ModelRegistry()
rich_nodes = attach_rich_output_nodes(
    registry,
    config=RichOutputConfig(
        enabled=True,
        allowlist=["markdown", "echarts", "datagrid", "report", "grid", "tabs", "accordion"],
    ),
)
catalog = build_catalog(list(rich_nodes), registry)
```

### Tool families

#### Generic visible renderer

| Tool | Emits visible UI | Typical use |
|------|------------------|-------------|
| `render_component(component, props, id?, title?, metadata?)` | Yes | Any allowlisted component, especially custom or lower-frequency components |

#### Typed visible wrappers

| Tool | Emits component | Emits visible UI | Typical use |
|------|-----------------|------------------|-------------|
| `render_chart_echarts(...)` | `echarts` | Yes | Charts and graphs |
| `render_report(...)` | `report` | Yes | Document-style multi-section output |
| `render_table(...)` | `datagrid` | Yes | Data grids and result tables |
| `render_grid(...)` | `grid` | Yes | Dashboard layouts |
| `render_tabs(...)` | `tabs` | Yes | Multiple related views |
| `render_accordion(...)` | `accordion` | Yes | Collapsible sections |

#### Silent reusable builders

| Tool | Builds component | Emits visible UI | Typical use |
|------|------------------|------------------|-------------|
| `build_chart_echarts(...)` | `echarts` | No | Build a chart once, compose later |
| `build_table(...)` | `datagrid` | No | Build a table once, compose later |
| `build_grid(...)` | `grid` | No | Build a layout subtree without showing it yet |
| `build_tabs(...)` | `tabs` | No | Build tabbed subviews for later reuse |
| `build_accordion(...)` | `accordion` | No | Build collapsible subviews for later reuse |

Builder tools return:

- `ok: bool`
- `component: str | None`
- `artifact_ref: str | None`
- `dedupe_key: str | None`
- `summary: str | None`
- `skipped: str | None`

They require an active planner run because the `artifact_ref` comes from the in-run artifact registry.

#### Schema/introspection and artifact discovery

| Tool | Purpose |
|------|---------|
| `describe_component(name)` | Return the component registry payload and schema |
| `list_artifacts(...)` | List reusable UI/binary artifacts and their refs |

#### Interactive UI tools

| Tool | Behavior |
|------|----------|
| `ui_form(...)` | Pauses and asks the user for structured input |
| `ui_confirm(...)` | Pauses for yes/no confirmation |
| `ui_select_option(...)` | Pauses for a structured selection |

!!! warning
    Interactive tools pause the planner and require a resume UX. See **[Pause/resume (HITL)](pause-resume-hitl.md)**.

### Builder vs renderer workflow

The recommended workflow for complex outputs is:

1. Build complex children first with `build_*`
2. If children are independent, build them via `next_node="parallel"`
3. Reuse the returned `artifact_ref`s
4. Call one final `render_*` tool for the visible parent artifact

Example mental model:

- `build_chart_echarts` and `build_table` create reusable children
- `render_grid` or `render_report` is the single visible render

This avoids:

- one giant nested payload with many failure points
- visible UI spam from intermediate child renders
- repeated inlining of heavy payloads in LLM context

### Composition contract for composite parents

The composite wrappers are explicitly ref-aware.

#### `report.sections[].components[]`

Supported child forms:

- inline child:
  - `{ "component": "...", "props": {...}, "caption": "..." }`
- ref child:
  - `{ "artifact_ref": "artifact_7", "caption": "..." }`

#### `grid.items[]`

Supported child forms:

- inline child:
  - `{ "component": "...", "props": {...}, "colSpan": 2, "rowSpan": 1, "title": "..." }`
- ref child:
  - `{ "artifact_ref": "artifact_8", "colSpan": 2, "rowSpan": 1, "title": "..." }`

#### `tabs.tabs[]`

Supported child forms:

- content tab:
  - `{ "label": "Overview", "content": "..." }`
- inline component tab:
  - `{ "label": "Data", "component": "datagrid", "props": {...} }`
- ref tab:
  - `{ "label": "Data", "artifact_ref": "artifact_9" }`

#### `accordion.items[]`

Supported child forms:

- content item:
  - `{ "title": "Summary", "content": "..." }`
- inline component item:
  - `{ "title": "Details", "component": "grid", "props": {...}, "defaultOpen": true }`
- ref item:
  - `{ "title": "Details", "artifact_ref": "artifact_10", "defaultOpen": true }`

### Validation, artifact refs, and dedupe

Rich output is more than “just extra tools”. The planner/runtime provides specific behavior:

- payloads are validated against `penguiflow/rich_output/registry.json`
- typed wrappers and builders share the same canonical payload-building path
- nested `artifact_ref`s are resolved server-side before validation
- render and build tools both register reusable component payloads
- exact adjacent duplicate calls are deduped per tool, so:
  - `render_table` dedupes against `render_table`
  - `build_table` dedupes against `build_table`
  - `build_table` does **not** suppress a later `render_table`
- visible render tools stay blocked from alternate multi-action execution
- silent builders remain eligible for `parallel` and multi-action sequencing where pure tools are allowed

### Component registry, allowlist, and prompt catalog

Rich output validates `component + props` against a registry JSON:

- `penguiflow/rich_output/registry.json`

Key runtime knobs:

- `enabled`
- `allowlist`
- `include_prompt_catalog`
- `include_prompt_examples`
- `max_payload_bytes`
- `max_total_bytes`

If rich output is enabled and the prompt catalog is allowed, PenguiFlow injects component guidance automatically so the model can discover:

- available components
- typed `render_*` wrappers
- typed `build_*` builders
- the build-first / ref-first workflow

## Operational defaults

Recommended defaults for production teams:

- Keep the allowlist narrow. Only expose components your frontend actually renders.
- Prefer typed `render_*` wrappers over `render_component` when a wrapper exists.
- Prefer `build_*` for complex or reusable child components.
- Prefer `render_component` only as the advanced escape hatch for custom/rare components.
- Keep simple text inline. Use `artifact_ref` for charts, tables, nested grids, tabs, and accordions.
- Use `parallel` only for independent child builds.
- Keep `max_payload_bytes` and `max_total_bytes` bounded.
- Use `describe_component(name=...)` when smaller or less reliable models keep missing schema details.

Recommended decision rule:

- One visible simple component: call `render_*` or `render_component`
- Many independent child components: `build_*` first, then one parent `render_*`
- New or uncommon component without wrapper: `render_component`

## Failure modes & recovery

### `Rich output is disabled for this planner`

**Likely cause**

- runtime not enabled
- nodes not attached to the planner catalog

**Fix**

- enable `RichOutputConfig(enabled=True, ...)`
- attach nodes via `attach_rich_output_nodes(...)`

### `build_* requires an active planner run so it can return an artifact_ref`

**Likely cause**

- calling a builder outside a normal planner execution path
- missing planner artifact registry in custom test/manual invocation code

**Fix**

- use builders inside a real planner run
- if testing nodes directly, attach an `ArtifactRegistry` to the planner context

### Validation errors on props

**Symptoms**

- tool call raises with a hint to call `describe_component`

**Fix**

- call `describe_component` for the target component
- use typed wrappers/builders instead of the generic escape hatch when possible
- for composite outputs, stop retrying one giant payload and build children separately

### Unknown `artifact_ref`

**Likely cause**

- stale ref from another run/session
- build tool never executed successfully
- artifact registry missing from the active planner context

**Fix**

- keep build and render steps within the same planner run
- call `list_artifacts(...)` if you need to inspect currently available refs

### Duplicate suppression when you did not expect it

**Symptoms**

- repeated same-tool build or render returns `skipped`

**Fix**

- this is expected for exact adjacent duplicates of the same tool
- if you intended a different result, change the args or use the correct phase:
  - `build_*` for reusable hidden child
  - `render_*` for visible output

### Composite outputs keep failing

**Fix sequence**

1. `describe_component(name=...)`
2. build child components separately with `build_*`
3. use `parallel` when children are independent
4. compose via `artifact_ref`
5. render the final parent once

## Observability

Useful planner signals for rich output:

- `tool_call_start` / `tool_call_result` for all rich-output tools
- `artifact_chunk` for visible UI emission
- `planner_args_invalid` for schema/arg-fill failures
- `arg_fill_attempt` / `arg_fill_success` / `arg_fill_failure`
- `multi_action_enqueued` if silent builders are auto-sequenced

Recommended metrics:

- visible UI artifact rate by session
- builder artifact count by session
- rich-output validation failure rate
- unknown `artifact_ref` rate
- duplicate render/build skip rate
- payload bytes per component type

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Treat all rich-output props as potentially LLM-visible and user-visible.
- Do not place secrets or raw internal tokens in component props.
- Scope artifacts per tenant/session.
- Keep `allowlist` and tool visibility minimal per surface/persona.
- If a component can render HTML, remote URLs, or app embeds, treat it as a privileged surface and review it separately.

## Runnable example: build children, then render one final dashboard

This example uses a scripted planner to:

1. build a chart
2. build a table
3. render one final grid that references both child artifacts

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from penguiflow import ModelRegistry
from penguiflow.catalog import build_catalog
from penguiflow.planner import PlannerEvent, PlannerFinish, ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes


class ScriptedClient(JSONLLMClient):
    def __init__(self) -> None:
        self._step = 0

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        del messages, response_format, stream, on_stream_chunk
        self._step += 1
        if self._step == 1:
            return json.dumps(
                {
                    "next_node": "build_chart_echarts",
                    "args": {
                        "title": "Revenue",
                        "option": {"series": [{"type": "line", "data": [1, 2, 3]}]},
                    },
                },
                ensure_ascii=False,
            )
        if self._step == 2:
            return json.dumps(
                {
                    "next_node": "build_table",
                    "args": {
                        "title": "Rows",
                        "columns": [{"field": "name", "header": "Name"}],
                        "rows": [{"name": "PenguiFlow"}],
                    },
                },
                ensure_ascii=False,
            )
        if self._step == 3:
            return json.dumps(
                {
                    "next_node": "render_grid",
                    "args": {
                        "title": "Dashboard",
                        "items": [
                            {"artifact_ref": "artifact_0", "colSpan": 2},
                            {"artifact_ref": "artifact_1"},
                        ],
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps({"next_node": "final_response", "args": {"answer": "done"}}, ensure_ascii=False)


async def main() -> None:
    registry = ModelRegistry()
    rich_nodes = attach_rich_output_nodes(
        registry,
        config=RichOutputConfig(enabled=True, allowlist=["echarts", "datagrid", "grid"]),
    )
    catalog = build_catalog(list(rich_nodes), registry)

    def on_event(ev: PlannerEvent) -> None:
        if ev.event_type == "artifact_chunk":
            print(ev.extra.get("artifact_type"), ev.extra.get("chunk"))

    planner = ReactPlanner(llm_client=ScriptedClient(), catalog=catalog, event_callback=on_event)
    result = await planner.run("build and render a dashboard", tool_context={"session_id": "demo"})
    assert isinstance(result, PlannerFinish)


if __name__ == "__main__":
    asyncio.run(main())
```

!!! note
    In a real run, the model would consume the builder results and use the returned `artifact_ref`s instead of hard-coding `artifact_0` / `artifact_1`.

## Troubleshooting checklist

- Did you enable rich output and attach the nodes to the catalog?
- Is the target component in the allowlist?
- Are you using `build_*` for reusable hidden children and `render_*` for the final visible artifact?
- Are you resolving child complexity through `artifact_ref` instead of one giant nested payload?
- Did you call `describe_component` when schema failures repeated?
- Does your frontend render `artifact_chunk` events with `artifact_type="ui_component"`?
- Are your refs scoped to the same planner run/session?
- Are payload limits rejecting large props that should have been moved into artifacts?
- If you added a new renderer, did you update both backend registry/runtime and frontend renderer dispatch? See **[Rich output extensions & custom renderers](rich-output-extensions.md)**.
- If you want richer or more domain-specific visual outputs, are you using skills to steer layout and renderer choice? See **[Rich output with skills](rich-output-skills.md)**.
