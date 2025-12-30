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
        "You can create rich, interactive outputs beyond plain text. Use the `render_component` tool to emit",
        "visualizations, data displays, forms, and composite layouts.",
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

    lines.extend(
        [
            "",
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
            "### Dashboard with Multiple Charts",
            "Use `grid` component to arrange multiple visualizations:",
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
            "Use `report` component for document-style output with text and embedded charts:",
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
            "Some tools emit large artifacts (charts, files) that are NOT in LLM context.",
            "Use the `list_artifacts` tool to retrieve metadata and refs, then reference them",
            "inside components with `artifact_ref` to avoid embedding heavy payloads.",
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
