# Rich output (UI components and interactive HITL)

## What it is / when to use it

“Rich output” is PenguiFlow’s typed tool surface for producing **structured UI artifacts** (charts, tables, reports, forms) instead of only plain text.

At runtime, rich output tools:

- emit `artifact_chunk` events with `artifact_type="ui_component"`,
- optionally pause for user input (forms/confirm/select),
- register UI payloads in the in-run artifact registry so they can be referenced later without re-render loops.

Use rich output when you have a UI runtime (e.g., the PenguiFlow Playground or an AG-UI compatible frontend) and you want:

- higher-signal outputs (tables/plots/reports),
- interactive approval and data entry,
- reproducible UI payloads (validated against a component registry).

## Non-goals / boundaries

- Rich output does not ship a frontend. You must render UI artifacts in your app.
- Rich output is not a general-purpose visualization library; it’s a validated envelope around component payloads.
- Rich output does not replace artifacts/resources: large data should still be stored as artifacts and referenced, not inlined in props.

## Contract surface

### Core runtime pieces

Rich output is implemented in:

- `penguiflow.rich_output.runtime` (global runtime + config)
- `penguiflow.rich_output.nodes` (tools: render_component, describe_component, ui_form, …)
- `penguiflow.rich_output.registry` (component registry loader)

### Enabling rich output

Enable rich output by:

1) configuring the runtime, and
2) adding rich output nodes to the planner catalog.

The canonical helper:

- `attach_rich_output_nodes(registry, config=RichOutputConfig(...)) -> list[Node]`

This registers models in your `ModelRegistry` and returns `Node` entries you can pass to `build_catalog(...)`.

### Tool surface

Passive output:

- `render_component(component, props, id?, title?, metadata?) -> RenderComponentResult`

Schemas/introspection:

- `describe_component(name) -> DescribeComponentResult` (returns props schema)
- `list_artifacts(...) -> ListArtifactsResult` (helps reuse artifacts and UI payloads)

Interactive HITL:

- `ui_form(...)` (pauses with a form payload)
- `ui_confirm(...)` (pauses with a confirm payload)
- `ui_select_option(...)` (pauses with a selection payload)

!!! warning
    Interactive rich output tools pause the planner. They require a resume UX (see **[Pause/resume (HITL)](pause-resume-hitl.md)**).

### Component registry and allowlist

Rich output validates `component` + `props` against a registry JSON (shipped as `penguiflow/rich_output/registry.json`).

Key config:

- `enabled: bool`
- `allowlist: Sequence[str]` (default includes markdown/json/echarts/report/grid/…)
- `include_prompt_catalog: bool` (injects a prompt catalog of components and schemas)
- `max_payload_bytes`, `max_total_bytes` (guardrails for prop sizes)

### Planner integration: prompt catalog auto-injection

If the planner sees a `render_component` tool in its catalog, it will attempt to inject the rich output “prompt catalog” into the system prompt automatically when:

- rich output runtime is enabled, and
- `RichOutputConfig.include_prompt_catalog=True`

This helps models call components without you manually copying schema docs into prompts.

### Artifact refs and re-render dedupe

Rich output is designed to avoid expensive prompt bloat and UI spam:

- `render_component` registers payloads in the in-run artifact registry and returns `artifact_ref` when available.
- tool execution includes a render-component dedupe guard that can skip exact duplicates across adjacent steps.
- props can reference previous artifacts (`artifact_ref`) and be resolved into concrete values when the registry is active.

## Operational defaults (recommended)

- Keep the allowlist small for production (only the components you render and support).
- Prefer `describe_component` for weak models instead of embedding all schemas.
- Enforce payload size limits (`max_payload_bytes`, `max_total_bytes`) and keep heavy data in artifacts.
- Use guardrails to prevent sensitive data from being rendered into UI props (see **[Guardrails](guardrails.md)**).

## Failure modes & recovery

### `Rich output is disabled for this planner`

**Likely cause**

- runtime is not enabled (default is disabled)

**Fix**

- call `attach_rich_output_nodes(..., config=RichOutputConfig(enabled=True, ...))` during planner setup.

### Validation errors on props

**Symptoms**

- `render_component` raises with a hint to call `describe_component`

**Fix**

- call `describe_component` for the exact schema
- keep props JSON-only and respect aliases (many props use `camelCase` in UI payloads)

### Re-render loops / spammy UI

If the model repeats the same `render_component` call, the runtime can dedupe exact duplicates and return a compact “skipped” observation.

**Fix**

- ensure your UI renderer treats duplicate artifacts idempotently
- include `artifact_ref` in your reasoning when you need to reference a previously rendered component

## Observability

Rich output emits planner events just like any tool call:

- `tool_call_start` / `tool_call_result` for `render_component` and UI tools
- `artifact_chunk` with `artifact_type="ui_component"` for the emitted UI payload

Recommended metrics:

- UI artifact rate per session
- render_component validation failure rate
- payload sizes (bytes) and clamp/rejection rate

See **[Planner observability](observability.md)**.

## Security / multi-tenancy notes

- Treat UI props as LLM-visible and user-visible. Never include secrets.
- Scope artifacts per session/tenant and enforce access checks in your artifact store.
- Keep the component allowlist minimal per product surface; do not expose components you cannot safely render.

## Runnable example: render a markdown component and capture artifact_chunk events

This example enables rich output, renders a markdown component, and prints emitted artifact events.

```python
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from penguiflow import ModelRegistry
from penguiflow.catalog import build_catalog
from penguiflow.planner import PlannerFinish, ReactPlanner
from penguiflow.planner.models import JSONLLMClient, PlannerEvent
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
                    "next_node": "render_component",
                    "args": {"component": "markdown", "props": {"text": "# Hello\\nThis is rich output."}},
                },
                ensure_ascii=False,
            )
        return json.dumps({"next_node": "final_response", "args": {"answer": "done"}}, ensure_ascii=False)


async def main() -> None:
    registry = ModelRegistry()
    rich_nodes = attach_rich_output_nodes(registry, config=RichOutputConfig(enabled=True))
    catalog = build_catalog(list(rich_nodes), registry)

    def on_event(ev: PlannerEvent) -> None:
        if ev.event_type == "artifact_chunk":
            print(ev.extra.get("artifact_type"), ev.extra.get("chunk"))

    planner = ReactPlanner(llm_client=ScriptedClient(), catalog=catalog, event_callback=on_event)
    result = await planner.run("demo", tool_context={"session_id": "demo"})
    assert isinstance(result, PlannerFinish)


if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting checklist

- Did you enable the runtime and attach rich output nodes to the catalog?
- Are you rendering `artifact_chunk` events in your UI transport layer?
- Are payload size limits blocking your props?
- Are you using `describe_component` when a model keeps failing validation?

