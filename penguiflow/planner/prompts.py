"""Prompt helpers for the React planner."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def _compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def render_tool(record: Mapping[str, Any]) -> str:
    args_schema = _compact_json(record["args_schema"])
    out_schema = _compact_json(record["out_schema"])
    tags = ", ".join(record.get("tags", ()))
    scopes = ", ".join(record.get("auth_scopes", ()))
    parts = [
        f"- name: {record['name']}",
        f"  desc: {record['desc']}",
        f"  side_effects: {record['side_effects']}",
        f"  args_schema: {args_schema}",
        f"  out_schema: {out_schema}",
    ]
    if tags:
        parts.append(f"  tags: {tags}")
    if scopes:
        parts.append(f"  auth_scopes: {scopes}")
    if record.get("cost_hint"):
        parts.append(f"  cost_hint: {record['cost_hint']}")
    if record.get("latency_hint_ms") is not None:
        parts.append(f"  latency_hint_ms: {record['latency_hint_ms']}")
    if record.get("safety_notes"):
        parts.append(f"  safety_notes: {record['safety_notes']}")
    if record.get("extra"):
        parts.append(f"  extra: {_compact_json(record['extra'])}")
    return "\n".join(parts)


def build_system_prompt(
    catalog: Sequence[Mapping[str, Any]],
    *,
    extra: str | None = None,
) -> str:
    rendered_tools = "\n".join(render_tool(item) for item in catalog)
    prompt = [
        "You are PenguiFlow ReactPlanner, a JSON-only planner.",
        "Follow these rules strictly:",
        "1. Respond with valid JSON matching the PlannerAction schema.",
        "2. Use the provided tools when necessary; never invent new tool names.",
        "3. Keep 'thought' concise and factual.",
        "4. When the task is complete, set 'next_node' to null "
        "and include the final payload in 'args'.",
        "5. Do not emit plain text outside JSON.",
        "",
        "Available tools:",
        rendered_tools or "(none)",
    ]
    if extra:
        prompt.extend(["", "Additional guidance:", extra])
    return "\n".join(prompt)


def build_user_prompt(query: str, context_meta: Mapping[str, Any] | None = None) -> str:
    if context_meta:
        return _compact_json({"query": query, "context": dict(context_meta)})
    return _compact_json({"query": query})


def render_observation(*, observation: Any | None, error: str | None) -> str:
    if error:
        return f"Observation: ERROR {error}"
    return f"Observation: {_compact_json(observation)}"


def render_validation_error(node_name: str, error: str) -> str:
    return (
        f"args for tool '{node_name}' did not validate: {error}. "
        "Return corrected JSON."
    )


def render_output_validation_error(node_name: str, error: str) -> str:
    return (
        f"tool '{node_name}' returned data that did not validate: {error}. "
        "Ensure the tool output matches the declared schema."
    )


def render_invalid_node(node_name: str, available: Sequence[str]) -> str:
    options = ", ".join(sorted(available))
    return (
        f"tool '{node_name}' is not in the catalog. Choose one of: {options}."
    )


def render_repair_message(error: str) -> str:
    return (
        "Previous response was invalid JSON or schema mismatch: "
        f"{error}. Reply with corrected JSON only."
    )
