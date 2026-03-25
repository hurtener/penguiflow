"""Prompt generation for rich output components."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .registry import ComponentRegistry

_QUICK_REFERENCE = [
    ("Chart/Graph", "echarts", "Any data visualization"),
    ("Data table", "datagrid", "Tabular data, query results"),
    ("Diagram", "mermaid", "Flowcharts, sequences, ERDs"),
    ("Single metric", "metric", "KPIs, key numbers"),
    ("Formatted text", "markdown", "Rich text with formatting"),
    ("Code snippet", "code", "Source code, examples"),
    ("User input", "form", "Collect parameters (PAUSES)"),
    ("Confirmation", "confirm", "Yes/No decisions (PAUSES)"),
    ("Selection", "select_option", "Choose from options (PAUSES)"),
    ("Multi-section doc", "report", "Reports with text + charts"),
    ("Dashboard grid", "grid", "Multiple components in columns"),
    ("Tabbed content", "tabs", "Related but distinct views"),
]

_CATEGORY_ORDER = ["visualization", "data", "document", "interactive", "layout", "media"]
_CATEGORY_TITLES = {
    "visualization": "Visualization",
    "data": "Data Display",
    "document": "Document & Text",
    "interactive": "Interactive (Human-in-the-Loop)",
    "layout": "Layout & Composition",
    "media": "Media & Embeds",
}


def generate_component_system_prompt(
    registry: ComponentRegistry,
    *,
    allowlist: Sequence[str] | None = None,
    include_examples: bool = False,
) -> str:
    """Generate a system prompt section describing available components."""

    allowed = set(allowlist or [])
    components = registry.allowlist(allowed if allowlist else None)

    lines: list[str] = [
        "# Rich Output Components",
        "",
        "You can create rich, interactive outputs beyond plain text. Use typed `build_*` and `render_*`",
        "tools for structured UI work. Use `render_component` only as the advanced escape hatch when a",
        "typed tool does not fit.",
        "",
        "## Quick Reference",
        "",
        "| Need | Component | When to Use |",
        "|------|-----------|-------------|",
    ]

    for need, comp, when in _QUICK_REFERENCE:
        if allowlist and comp not in allowed:
            continue
        if comp not in components:
            continue
        lines.append(f"| {need} | `{comp}` | {when} |")

    lines.extend([""])
    build_lines: list[str] = []
    wrapper_lines: list[str] = []
    if "echarts" in components:
        build_lines.append("- `build_chart_echarts(...)` to create a reusable chart ref")
        wrapper_lines.append("- `render_chart_echarts(...)` for charts")
    if "datagrid" in components:
        build_lines.append("- `build_table(...)` to create a reusable data-grid ref")
        wrapper_lines.append("- `render_table(...)` for data grids")
    if "report" in components:
        wrapper_lines.append("- `render_report(...)` for document-style reports")
    if "grid" in components:
        build_lines.append("- `build_grid(...)` to create a reusable dashboard/grid ref")
        wrapper_lines.append("- `render_grid(...)` for dashboard-style layouts")
    if "tabs" in components:
        build_lines.append("- `build_tabs(...)` to create reusable tabs")
        wrapper_lines.append("- `render_tabs(...)` for related views in one artifact")
    if "accordion" in components:
        build_lines.append("- `build_accordion(...)` to create a reusable accordion ref")
        wrapper_lines.append("- `render_accordion(...)` for collapsible structured sections")
    if build_lines:
        lines.extend(
            [
                "## Builder Tools",
                "",
                "For reusable or complex pieces, build them first and keep them off-screen until the",
                "final visible render. Builder tools return `artifact_ref` values you can reuse later:",
                *build_lines,
                "",
            ]
        )
    if wrapper_lines:
        lines.extend(
            [
                "## Convenience Render Tools",
                "",
                "Use these for the final visible artifact. They are easier to call correctly than building",
                "nested `render_component(component=..., props=...)` payloads by hand:",
                *wrapper_lines,
                "",
            ]
        )

    if build_lines:
        lines.extend(
            [
                "## Preferred Workflow for Composite Outputs",
                "",
                "When you need multiple complex child components:",
                "1. Build each child first with `build_*` tools.",
                "2. If the children are independent, use `next_node=\"parallel\"` to build them concurrently.",
                "3. Reuse the returned `artifact_ref` values inside the parent component.",
                "4. Emit one final visible artifact with `render_report`, `render_grid`, `render_tabs`, or",
                "   `render_accordion`.",
                "",
                "Avoid one giant nested payload when the output has multiple charts, tables, tabs, or grid items.",
                "For simple text content, inline `content` is fine.",
                "For complex reusable children, prefer `artifact_ref`.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Preferred Workflow for Composite Outputs",
                "",
                "When no typed builder tools are available in this runtime, prefer the typed `render_*` wrappers",
                "that are available and keep nested payloads as small and simple as possible.",
                "If rich-output schema failures repeat, call `describe_component(name=...)` and simplify the layout",
                "instead of repeatedly retrying a giant nested payload.",
                "",
            ]
        )

    lines.extend(
        [
            "## Important: Interactive Components",
            "",
            "Components marked with (PAUSES) will pause your execution until the user responds:",
            "- `form` - Collect structured input",
            "- `confirm` - Get yes/no approval",
            "- `select_option` - Let user choose from options",
            "",
            "When the run resumes, you will receive a resume input payload containing the tool name and",
            "the user's response (JSON object with keys like `tool`, `component`, `result`). Parse it and",
            "continue the workflow using that structured response.",
            "",
            "## Schema on Demand",
            "",
            "If you need full component schemas, call the `describe_component` tool with a component name.",
            "",
            "## Component Details",
            "",
        ]
    )

    grouped: dict[str, list[tuple[str, Mapping[str, Any]]]] = defaultdict(list)
    for name, component in components.items():
        grouped[component.category or "other"].append((name, _component_payload(component)))

    for category in _CATEGORY_ORDER:
        if category not in grouped:
            continue
        lines.append(f"### {_CATEGORY_TITLES.get(category, category.title())}")
        lines.append("")

        for name, _payload in grouped[category]:
            definition = registry.components[name]
            pause_badge = " (PAUSES)" if definition.interactive else ""
            lines.append(f"#### `{name}`{pause_badge}")
            lines.append("")

            description = definition.description.split("\n", 1)[0]
            lines.append(description)
            lines.append("")

            props_schema = definition.props_schema
            required = props_schema.get("required", []) if isinstance(props_schema, Mapping) else []
            properties = props_schema.get("properties", {}) if isinstance(props_schema, Mapping) else {}

            if properties:
                lines.append("**Key props:**")
                for prop_name, prop_schema in list(properties.items())[:5]:
                    req = "(required)" if prop_name in required else ""
                    prop_type = "any"
                    if isinstance(prop_schema, Mapping):
                        prop_type = prop_schema.get("type", prop_type)
                        prop_desc = str(prop_schema.get("description", ""))[:60]
                    else:
                        prop_desc = ""
                    lines.append(f"  - `{prop_name}` {req}: {prop_desc}")
                lines.append("")

            if include_examples and definition.example:
                lines.append(f"**Example** ({definition.example.get('description', '')}):")
                lines.append("```json")
                example_json = json.dumps(
                    {"component": name, "props": definition.example.get("props", {})},
                    indent=2,
                )[:700]
                lines.append(example_json)
                lines.append("```")
                lines.append("")

    lines.extend(
        [
            "## Usage Patterns",
            "",
            "### Single Component",
            "```",
            "render_component(component='echarts', props={'option': {...}})",
            "```",
            "",
        ]
    )

    if build_lines:
        lines.extend(
            [
                "### Build First, Then Render Once",
                "When multiple child components are independent, build them in parallel and render the parent once:",
                "```json",
                "{",
                '  "next_node": "parallel",',
                '  "args": {',
                '    "steps": [',
                '      {"node": "build_chart_echarts", "args": {"title": "Revenue", "option": {...}}},',
                '      {"node": "build_table", "args": {"title": "Rows", "columns": [...], "rows": [...]}}',
                "    ]",
                "  }",
                "}",
                "```",
                "Then compose the parent with the returned refs:",
                "```json",
                "{",
                '  "next_node": "render_grid",',
                '  "args": {',
                '    "title": "Dashboard",',
                '    "items": [',
                '      {"artifact_ref": "artifact_1"},',
                '      {"artifact_ref": "artifact_2"}',
                "    ]",
                "  }",
                "}",
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "### Dashboard with Multiple Charts",
            "For a visible one-shot render, use `grid` to arrange multiple visualizations:",
            "```json",
            "{",
            '  "component": "grid",',
            '  "props": {',
            '    "columns": 2,',
            '    "items": [',
            '      {"component": "metric", "props": {...}},',
            '      {"component": "echarts", "props": {...}, "colSpan": 2}',
            "    ]",
            "  }",
            "}",
            "```",
            "",
            "### Report with Sections",
            "Use `report` for document-style output with text and embedded charts or reusable child refs:",
            "```json",
            "{",
            '  "component": "report",',
            '  "props": {',
            '    "title": "Analysis Report",',
            '    "sections": [',
            '      {"title": "Summary", "content": "...markdown..."},',
            '      {"title": "Data", "components": [{"component": "datagrid", "props": {...}}]}',
            "    ]",
            "  }",
            "}",
            "```",
            "",
            "### Artifact References (On Demand)",
            "Builder tools and some other tools return artifacts that are NOT in LLM context.",
            "Use the returned `artifact_ref` directly, or call `list_artifacts` to retrieve metadata and refs,",
            "then reference them inside components with `artifact_ref` to avoid embedding heavy payloads.",
            "",
            "```json",
            "{",
            '  "component": "report",',
            '  "props": {',
            '    "sections": [',
            '      {',
            '        "title": "Revenue Trend",',
            '        "components": [',
            '          {"artifact_ref": "artifact_3", "caption": "Figure 1: Revenue trend"}',
            "        ]",
            "      }",
            "    ]",
            "  }",
            "}",
            "```",
            "",
            "### Collecting User Input",
            "When you need user input before proceeding, call an interactive UI tool:",
            "```json",
            "{",
            '  "title": "Configure Report",',
            '  "fields": [',
            '    {"name": "period", "type": "select", "options": ["Q1", "Q2", "Q3", "Q4"]}',
            "  ]",
            "}",
            "```",
            "",
            "The user's response will be returned to you as the tool result.",
        ]
    )

    if build_lines:
        lines.extend(
            [
                "",
                "### Reusable Tabs",
                "Build complex child views first, then compose them into tabs by ref:",
                "```json",
                "{",
                '  "tabs": [',
                '    {"label": "Overview", "artifact_ref": "artifact_4"},',
                '    {"label": "Details", "artifact_ref": "artifact_5"}',
                "  ]",
                "}",
                "```",
            ]
        )

    return "\n".join(line.rstrip() for line in lines).strip()


def _component_payload(component: Any) -> dict[str, Any]:
    return {
        "name": component.name,
        "description": component.description,
        "propsSchema": component.props_schema,
        "interactive": component.interactive,
        "category": component.category,
        "tags": list(component.tags),
        "example": component.example,
    }


__all__ = ["generate_component_system_prompt"]
