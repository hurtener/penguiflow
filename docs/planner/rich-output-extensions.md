# Rich output extensions & custom renderers

## What it is / when to use it

This page explains how downstream teams should extend PenguiFlow rich output when they add:

- a new frontend renderer
- a new backend registry entry
- a typed `render_*` wrapper
- a typed `build_*` silent builder
- a new composite parent component that needs ref-aware child composition

Use this guide when you want new rich-output behavior **without changing the downstream frontend event contract**.

## Non-goals / boundaries

- This is not a frontend design guide. It focuses on runtime contracts and extension wiring.
- This is not a general ToolNode guide. See **[Tooling](tooling.md)** for external tools.
- This is not a promise that all rich-output internals are public API. Some examples reference current in-repo implementation patterns that downstream teams may mirror in their own local integration layer.

## Contract surface

### The invariant you should preserve

When adding new visible renderers, the frontend contract should remain:

- planner event: `artifact_chunk`
- `artifact_type="ui_component"`
- chunk:
  - `id?: str`
  - `component: str`
  - `props: object`
  - `title?: str`

If you preserve that shape, existing renderer dispatch code can stay stable while the backend grows richer authoring tools.

### The extension layers

Rich output has three layers that must stay aligned:

1. **Registry layer**
   Declares the component name, description, category, tags, and `propsSchema`.
2. **Planner tool layer**
   Exposes `render_component`, typed `render_*`, typed `build_*`, and interactive tools.
3. **Frontend renderer layer**
   Maps `component` to the actual UI implementation.

The system is most reliable when the planner sees **typed tools**, while the frontend still only receives canonical `{component, props}` payloads.

### Choose the right extension pattern

#### Pattern A: registry-only leaf component

Use this when:

- the component is uncommon
- `render_component` is good enough
- you do not need special prompting, builder behavior, or typed convenience args

What to add:

- registry entry
- frontend renderer implementation
- renderer registry mapping

#### Pattern B: typed visible wrapper

Use this when:

- the component is common
- the model frequently gets the raw prop shape wrong
- you want a narrower, easier tool surface than `render_component`

Examples already in-tree:

- `render_chart_echarts`
- `render_table`
- `render_report`
- `render_grid`
- `render_tabs`
- `render_accordion`

#### Pattern C: typed silent builder

Use this when:

- the component is reusable
- it is often nested inside larger layouts
- you want ref-first composition without intermediate UI emission

Examples already in-tree:

- `build_chart_echarts`
- `build_table`
- `build_grid`
- `build_tabs`
- `build_accordion`

#### Pattern D: composite parent with ref-aware children

Use this when the component itself organizes other components, for example:

- report sections
- dashboard grids
- tabs
- accordion items

Composite parents should support both:

- inline child payloads for simple cases
- `artifact_ref` children for complex or reusable cases

### Extension checklist

When adding a new renderer family, review this list in order.

#### 1. Add the registry entry

Add the component definition to `penguiflow/rich_output/registry.json` or via `RichOutputExtension.registry_patch`.

Include:

- `name`
- `description`
- `category`
- `interactive`
- `tags`
- `propsSchema`
- `example` when practical

The registry is used for:

- runtime validation
- `describe_component`
- prompt catalog generation

#### 2. Add the frontend renderer

The frontend must be able to dispatch the new `component` value to a renderer implementation.

Preserve the existing contract:

- same `component` string as the backend registry
- renderer consumes `props`
- optional `title` remains top-level artifact chrome, not a renderer-specific prop unless you intentionally model it that way

#### 3. Decide whether you need a wrapper, builder, or both

Use this rule:

- leaf + rare: registry-only
- leaf + common: add `render_*`
- reusable/nestable: add both `render_*` and `build_*`
- composite parent: add typed wrapper and make child schemas ref-aware

#### 4. Implement canonical payload building

The important backend design rule is:

- model-facing tool args can be specialized
- backend should always collapse them into one canonical payload:
  - `{component, props, id?, title?, metadata?}`

That canonical payload is what you:

- validate
- register in the artifact registry
- optionally emit to the frontend

Do **not** maintain separate ad hoc code paths for visible render and silent build. That is how render/build drift starts.

#### 5. Split registration from visible emission

The current pattern is:

- build canonical payload
- resolve nested `artifact_ref`s
- validate against the registry
- register the payload in the artifact registry
- if visible render:
  - emit `artifact_chunk`
- if silent builder:
  - return `artifact_ref` only

This split is what keeps `build_*` and `render_*` in parity.

#### 6. Wire planner reliability helpers

If you add a new wrapper or builder, make sure it participates in:

- schema-aware arg-fill eligibility
- rich-output failure guidance
- duplicate suppression
- runtime prompt exposure
- allowlist-gated registration

If you add a new visible render tool, make sure it is treated like existing visible renderers for multi-action blocking.

If you add a new silent builder, make sure it remains eligible where pure/read-only tools are allowed.

#### 7. Keep builder and renderer dedupe separate

Do not let `build_*` dedupe suppress the matching `render_*`.

Correct behavior:

- same builder twice in adjacent steps -> dedupe
- same renderer twice in adjacent steps -> dedupe
- builder then renderer -> no cross-dedupe

#### 8. Preserve meaningful metadata across `artifact_ref`

If a builder stores meaningful top-level fields such as `title`, do not throw them away when resolving `artifact_ref`.

At minimum, preserve:

- `component`
- `props`
- `title` when the parent layout can make use of it
- `id` when stable component ids matter

#### 9. For composite parents, enforce ref-vs-inline exclusivity

If a child item supports both:

- `artifact_ref`
- inline `component + props`

then the model validator should reject ambiguous mixed payloads instead of silently merging them.

#### 10. Add tests before shipping

Minimum recommended coverage for a new renderer family:

- registry entry is reachable through `describe_component`
- runtime registers the new tool(s) only when allowlisted
- wrapper emits canonical visible artifact
- builder returns `artifact_ref` without visible emission
- duplicate builder and duplicate render both dedupe correctly
- builder then matching render does not cross-dedupe
- ref resolution preserves required child metadata
- prompt catalog surfaces the new tool(s)

## Operational defaults

Recommended defaults for downstream teams extending rich output:

- Start with registry + frontend renderer first.
- Add a typed `render_*` wrapper only if the component is common enough to justify prompt space.
- Add a `build_*` tool only when the component is frequently nested or reused.
- Keep builder and renderer backed by the same canonical payload builder.
- Keep composite parents ref-aware from day one if they can contain child components.
- Keep your allowlist minimal per deployment surface.

## Failure modes & recovery

### Backend and frontend names drift

**Symptoms**

- planner validates and emits a component
- frontend cannot render it

**Fix**

- keep backend registry component names and frontend renderer keys identical
- add parity tests if your downstream app maintains its own renderer map

### Wrapper and generic render disagree

**Symptoms**

- `render_component(component="x", props=...)` behaves differently than `render_x(...)`

**Fix**

- unify both through a single canonical payload builder
- do not duplicate conversion logic in multiple places

### Builder and renderer drift apart

**Symptoms**

- `build_x` produces a payload that behaves differently from `render_x`

**Fix**

- split “register payload” from “emit visible artifact”
- both tools should share payload construction, ref resolution, and validation

### Child `artifact_ref`s resolve but lose behavior

**Symptoms**

- nested components work but lose titles, ids, or layout hints

**Fix**

- preserve meaningful top-level metadata when resolving stored component payloads
- verify nested parents actually have a place to use that metadata

### Ambiguous mixed child payloads

**Symptoms**

- caller provides both `artifact_ref` and inline props
- runtime silently prefers one path over the other

**Fix**

- add explicit model validation so the payload fails fast

## Observability

For custom renderer extensions, log or emit metrics for:

- wrapper invocation count
- builder invocation count
- visible artifact emission count
- validation failure count by component
- unknown `artifact_ref` count
- duplicate skip count by tool
- average payload byte size by component

If the renderer is critical to business workflows, add alerting for:

- repeated validation failures
- frontend render failures for the component
- missing renderer/registry parity in deploy checks

## Security / multi-tenancy notes

- Treat new rich-output components as user-visible surfaces, not “internal implementation details”.
- Never leak tenant-specific secrets through props or metadata.
- If the component renders external URLs, HTML, or app embeds, review it as a privileged integration.
- Scope artifact refs to the session/tenant boundary your host app enforces.
- Keep tool visibility minimal: a renderer should only be available where the frontend can safely render it.

## Runnable example: register a custom renderer family

This example shows a minimal `RichOutputExtension` that adds a registry entry and custom nodes. The pattern is the same whether the node is visible-only or a builder/render pair.

```python
from __future__ import annotations

from pydantic import BaseModel

from penguiflow.node import Node
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import RichOutputExtension, register_rich_output_extension


class RenderSparklineArgs(BaseModel):
    data: list[float]
    title: str | None = None


class RenderSparklineOut(BaseModel):
    ok: bool = True


async def render_sparkline(_args: RenderSparklineArgs, _ctx) -> RenderSparklineOut:
    # In a real implementation:
    # 1. build canonical {component, props}
    # 2. validate against the rich-output registry
    # 3. register tool artifact
    # 4. emit artifact_chunk if visible
    return RenderSparklineOut()


def register_nodes(registry: ModelRegistry) -> list[Node]:
    registry.register("render_sparkline", RenderSparklineArgs, RenderSparklineOut)
    return [Node(render_sparkline, name="render_sparkline")]


register_rich_output_extension(
    RichOutputExtension(
        name="custom-sparkline",
        registry_patch={
            "sparkline": {
                "name": "sparkline",
                "description": "Render a compact trend line.",
                "category": "visualization",
                "interactive": False,
                "tags": ["chart", "sparkline"],
                "propsSchema": {
                    "type": "object",
                    "required": ["data"],
                    "properties": {
                        "data": {"type": "array", "items": {"type": "number"}},
                        "title": {"type": "string"},
                    },
                },
            }
        },
        register_nodes=register_nodes,
    )
)
```

!!! note
    For production-grade wrappers/builders, mirror the existing rich-output pattern: canonical payload builder, artifact registration, optional visible emission, dedupe plumbing, prompt exposure, and tests.

## Troubleshooting checklist

- Did you add the component to the registry and the frontend renderer map?
- Does the `component` string match exactly on both sides?
- Did you decide explicitly whether the new component needs:
  - registry-only support
  - `render_*`
  - `build_*`
  - both
- Are builder and renderer backed by the same canonical payload builder?
- Did you wire prompt exposure so the model can discover the new tool?
- Did you add dedupe, validation, and multi-action behavior that matches the tool’s role?
- If the component is composite, are child schemas explicitly ref-aware?
- Did you preserve useful metadata like `title` across `artifact_ref` resolution?
- Did you add tests for visible render, silent build, and ref-based composition?
- If the renderer needs domain-specific guidance, are you pairing it with skills? See **[Rich output with skills](rich-output-skills.md)**.
