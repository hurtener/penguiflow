# Rich output with skills

## What it is / when to use it

This page explains how downstream teams can use **skills** to improve rich-output results and to support new renderer families without hard-coding all renderer behavior into tool descriptions.

Use this guide when you want:

- better renderer selection (`report` vs `grid` vs `tabs`)
- more consistent visual structure for a domain (finance, ops, support, BI)
- tenant- or persona-specific layout conventions
- playbooks for how a new custom renderer should be used
- richer outputs from small or general-purpose models without bloating the base prompt

In short:

- tools execute rich output
- skills teach the planner how and when to use it

## Non-goals / boundaries

- Skills do not render UI by themselves.
- Skills do not replace typed wrappers/builders.
- Skills are not a substitute for backend validation or frontend renderer implementation.
- Skills should not carry secrets or tool-only credentials.

## Contract surface

### How skills influence rich output

When skills are enabled, ReactPlanner can:

- inject “relevant skills” into the prompt before the run starts
- let the model explicitly call `skill_search`, `skill_get`, and `skill_list`
- filter skill visibility based on allowed tools/namespaces/tags

That means skills can teach the model things like:

- when to choose `render_report` over `render_grid`
- when to build reusable components first with `build_*`
- how a team wants dashboards titled, grouped, and ordered
- which custom renderer should be used for a specific workflow

### The main patterns

#### Pattern A: renderer-selection skill

Use a skill when you want a stable rule like:

- “Use `render_report` for narrative analysis with 3+ sections”
- “Use `render_grid` for KPI dashboards”
- “Use `render_tabs` when the user asked for multiple alternative views”

This is especially useful when the model has all the tools but chooses the wrong high-level layout.

#### Pattern B: build-first composition skill

Use a skill when you want to enforce the new preferred workflow:

- complex child components should use `build_*`
- independent child components should be built with `parallel`
- final visible output should be rendered once with `artifact_ref`

This is useful when the model still tends to emit one giant nested payload.

#### Pattern C: renderer-specific authoring skill

Use a skill when you add a new renderer and need to teach:

- what it is for
- what anti-patterns to avoid
- what data shape or narrative shape works best
- when to prefer a fallback like `markdown`

Example:

- a custom `sparkline` renderer for compact deltas in KPI grids
- a custom `trace_timeline` renderer for incident investigations
- a custom `citations_panel` renderer for RAG-heavy answers

#### Pattern D: tenant/persona-specific visual conventions

Use runtime-backed skills when different teams need different output conventions, for example:

- finance wants currency formatting, conservative layout, and summary tables first
- support wants timelines and collapsible case-history accordions
- product analytics wants tabs for segments and a comparison grid by default

This is a strong use case for runtime `skills_provider` or `skills_provider_factory`.

### Skills that target rich output should be capability-aware

Skills can declare applicability metadata such as:

- `required_tool_names`
- `required_namespaces`
- `required_tags`

For rich output, this is important because a skill should usually only surface when its tools are actually available.

Examples:

- a “dashboard composition” skill should only appear when `build_grid` / `render_grid` are available
- a custom renderer skill should only appear when that renderer’s wrapper/builder tools are in the allowed catalog

This prevents:

- stale instructions that mention unavailable renderers
- prompt confusion when a tenant/frontend does not support a renderer family

### What a rich-output-focused skill should contain

A good rich-output skill usually includes:

- when to use the renderer or layout
- when not to use it
- the preferred wrapper/builder path
- whether to use `parallel`
- how to compose `artifact_ref` children
- domain conventions for titles, captions, ordering, and summaries
- fallback behavior when the renderer schema cannot be satisfied

It should avoid:

- long raw JSON examples for many components
- hard-coded secrets or private identifiers
- references to tools that may be hidden by policy

### Skill authoring examples

#### Example: build-first dashboard skill

```yaml
name: ui.dashboard.build_first
title: Build-first dashboard composition
trigger: Use when the user wants a dashboard with multiple charts or tables.
required_tool_names:
  - build_chart_echarts
  - build_table
  - render_grid
steps:
  - Build complex child components first instead of emitting one large nested grid payload.
  - If the child components are independent, prefer next_node="parallel".
  - Reuse the returned artifact_ref values inside the final render_grid call.
  - Render exactly one visible grid artifact at the end.
```

#### Example: renderer-selection skill

```yaml
name: ui.analysis.layout_chooser
title: Choose the right rich-output layout
trigger: Use when deciding between report, grid, tabs, and accordion layouts.
required_tool_names:
  - render_report
  - render_grid
  - render_tabs
  - render_accordion
steps:
  - Use render_report for narrative analysis with multiple written sections.
  - Use render_grid for dashboards and KPI-first outputs.
  - Use render_tabs when the same dataset should be shown in alternative views.
  - Use render_accordion for FAQs, drill-down details, or dense supporting material.
```

### How skills help new custom renderers

When adding a new renderer family, the best sequence is usually:

1. add the registry entry and frontend renderer
2. add typed wrapper/builder tools if warranted
3. add a skill pack entry that teaches when and how to use the renderer
4. gate that skill with `required_tool_names`
5. rely on runtime skill retrieval instead of trying to bake all usage guidance into every system prompt

This is especially useful when:

- the renderer is domain-specific
- the renderer is optional by tenant/persona
- the renderer has nuanced usage conventions that are too verbose for the default prompt catalog

### Dynamic runtime skills for rich output

Use runtime providers when rich-output guidance must vary by:

- tenant
- persona
- product surface
- frontend capabilities

Examples:

- mobile frontend only supports `metric`, `markdown`, and compact `sparkline`
- desktop analytics frontend supports `report`, `grid`, `tabs`, `accordion`, and a custom `trace_timeline`
- one tenant requires executive-summary-first reports, another requires evidence-first reports

The host app can surface only the relevant skills for that run, rather than shipping a giant universal prompt.

## Operational defaults

Recommended defaults for teams using skills with rich output:

- Keep the default rich-output prompt focused on structural behavior, not every domain-specific nuance.
- Put domain-specific layout conventions into skills.
- Gate renderer-specific skills with `required_tool_names`.
- Prefer short, operational skills over long prose.
- Use skills to teach *workflow*:
  - build first
  - use `parallel` when independent
  - compose with `artifact_ref`
- Use runtime-backed skills for tenant/persona-specific output conventions.

Good split of responsibilities:

- backend validation: hard correctness
- typed wrappers/builders: structural reliability
- skills: domain-specific judgment and renderer-selection behavior

## Failure modes & recovery

### Skills mention renderers or tools that are not visible

**Likely cause**

- missing applicability metadata
- stale skill content after a renderer was removed

**Fix**

- add `required_tool_names`
- keep skill packs versioned alongside renderer/tool changes

### Skills are too generic to improve outputs

**Symptoms**

- model still picks the wrong renderer or layout

**Fix**

- make the skill operational and explicit
- encode concrete selection rules and fallback rules
- include builder/render workflow guidance, not only “use charts when helpful”

### Skills fight the default prompt

**Symptoms**

- model oscillates between patterns

**Fix**

- keep the base prompt structural and generic
- move domain-specific rules into skills
- avoid duplicating the same rules in three places with slightly different wording

### Small models still emit giant nested payloads

**Fix**

- add a dedicated build-first composition skill
- ensure `build_*` tools are actually visible in the tool catalog
- use repeated-failure guidance plus skills, not either one alone

## Observability

For skills that target rich output, useful signals are:

- `skills_retrieved`
- `skill_search_query`
- `skill_get`
- rich-output validation failure rate before/after enabling the skill
- render/build tool selection rate by layout family
- duplicate skip rate
- final visible component distribution (`report`, `grid`, `tabs`, etc.)

Good questions to measure:

- Did the skill increase `build_*` adoption for composite outputs?
- Did it reduce invalid rich-output payloads?
- Did it improve layout selection consistency?

## Security / multi-tenancy notes

- Treat skills as LLM-visible prompt material.
- Do not encode secrets, tenant-private IDs, or hidden tool names in rich-output skills.
- If renderer behavior differs by tenant, expose that through scoped/runtime skills rather than a shared static pack.
- Keep skill visibility aligned with tool visibility so the model is never instructed to use hidden renderers.

## Runnable example: skill-guided rich output

This example shows the structural setup for enabling both rich output and skills. The actual skill content can come from packs or a runtime provider.

```python
from __future__ import annotations

from pathlib import Path

from penguiflow import ModelRegistry
from penguiflow.catalog import build_catalog
from penguiflow.planner import ReactPlanner
from penguiflow.rich_output.runtime import RichOutputConfig, attach_rich_output_nodes
from penguiflow.skills.models import SkillPackConfig, SkillsConfig


registry = ModelRegistry()
rich_nodes = attach_rich_output_nodes(
    registry,
    config=RichOutputConfig(
        enabled=True,
        allowlist=["echarts", "datagrid", "report", "grid", "tabs", "accordion"],
    ),
)
catalog = build_catalog(list(rich_nodes), registry)

planner = ReactPlanner(
    llm_client=...,  # your JSONLLMClient
    catalog=catalog,
    skills=SkillsConfig(
        enabled=True,
        top_k=4,
        max_tokens=2000,
        skill_packs=[
            SkillPackConfig(
                name="ui-playbooks",
                path=str(Path("skillpacks/ui-playbooks")),
            )
        ],
    ),
)
```

!!! note
    For tenant- or persona-specific renderer behavior, prefer `skills_provider` or `skills_provider_factory` instead of a single shared pack.

## Troubleshooting checklist

- Are the relevant rich-output tools actually in the catalog and allowlist?
- Does the skill declare `required_tool_names` so it only appears when those tools are visible?
- Is the skill teaching layout selection, build-first composition, or both?
- Are you using skills for domain-specific conventions instead of overloading the base prompt catalog?
- If the renderer is new, did you ship a matching skill entry alongside the wrapper/builder?
- Are runtime skills scoped correctly per tenant/persona/frontend capability set?
- If results are still weak, did you measure whether the skill is actually retrieved and injected?
- For custom renderer implementation details, see **[Rich output extensions & custom renderers](rich-output-extensions.md)**.
- For the base runtime contract, see **[Rich output](rich-output.md)**.
