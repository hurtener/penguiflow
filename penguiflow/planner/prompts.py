"""Prompt helpers for the React planner."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def render_summary(summary: Mapping[str, Any]) -> str:
    return "Trajectory summary: " + _compact_json(summary)


def render_resume_user_input(user_input: str) -> str:
    return f"Resume input: {user_input}"


def render_planning_hints(hints: Mapping[str, Any]) -> str:
    lines: list[str] = []
    constraints = hints.get("constraints")
    if constraints:
        lines.append(f"Respect the following constraints: {constraints}")
    preferred = hints.get("preferred_order")
    if preferred:
        lines.append(f"Preferred order (if feasible): {preferred}")
    parallels = hints.get("parallel_groups")
    if parallels:
        lines.append(f"Allowed parallel groups: {parallels}")
    disallowed = hints.get("disallow_nodes")
    if disallowed:
        lines.append(f"Disallowed tools: {disallowed}")
    preferred_nodes = hints.get("preferred_nodes")
    if preferred_nodes:
        lines.append(f"Preferred tools: {preferred_nodes}")
    budget = hints.get("budget")
    if budget:
        lines.append(f"Budget hints: {budget}")
    if not lines:
        return ""
    return "\n".join(lines)


def render_disallowed_node(node_name: str) -> str:
    return (
        f"tool '{node_name}' is not permitted by constraints. "
        "Choose an allowed tool or revise the plan."
    )


def render_ordering_hint_violation(expected: Sequence[str], proposed: str) -> str:
    order = ", ".join(expected)
    return (
        "Ordering hint reminder: follow the preferred sequence "
        f"[{order}]. Proposed: {proposed}. Revise the plan."
    )


def render_parallel_limit(max_parallel: int) -> str:
    return (
        f"Parallel action exceeds max_parallel={max_parallel}. Reduce parallel fan-out."
    )


def render_sequential_only(node_name: str) -> str:
    return (
        f"tool '{node_name}' must run sequentially. "
        "Do not include it in a parallel plan."
    )


def render_parallel_setup_error(errors: Sequence[str]) -> str:
    detail = "; ".join(errors)
    return f"Parallel plan invalid: {detail}. Revise the plan and retry."


def render_empty_parallel_plan() -> str:
    return "Parallel plan must include at least one branch in 'plan'."


def render_parallel_with_next_node(next_node: str) -> str:
    return (
        f"Parallel plan cannot set next_node='{next_node}'. "
        "Use 'join' to continue or finish the run explicitly."
    )


def render_parallel_unknown_failure(node_name: str) -> str:
    return (
        f"tool '{node_name}' failed during parallel execution. "
        "Investigate the tool and adjust the plan."
    )


def build_summarizer_messages(
    query: str,
    history: Sequence[Mapping[str, Any]],
    base_summary: Mapping[str, Any],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a summariser producing compact JSON state. "
                "Respond with valid JSON matching the TrajectorySummary schema."
            ),
        },
        {
            "role": "user",
            "content": _compact_json(
                {
                    "query": query,
                    "history": list(history),
                    "current_summary": dict(base_summary),
                }
            ),
        },
    ]


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
    planning_hints: Mapping[str, Any] | None = None,
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
    if planning_hints:
        rendered_hints = render_planning_hints(planning_hints)
        if rendered_hints:
            prompt.extend(["", rendered_hints])
    return "\n".join(prompt)


def build_user_prompt(query: str, context_meta: Mapping[str, Any] | None = None) -> str:
    if context_meta:
        return _compact_json({"query": query, "context": dict(context_meta)})
    return _compact_json({"query": query})


def render_observation(
    *,
    observation: Any | None,
    error: str | None,
    failure: Mapping[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {}
    if observation is not None:
        payload["observation"] = observation
    if error:
        payload["error"] = error
    if failure:
        payload["failure"] = dict(failure)
    if not payload:
        payload["observation"] = None
    return _compact_json(payload)


def render_hop_budget_violation(limit: int) -> str:
    return (
        "Hop budget exhausted; you have used all available tool calls. "
        "Finish with the best answer so far or reply with no_path."
        f" (limit={limit})"
    )


def render_deadline_exhausted() -> str:
    return (
        "Deadline reached. Provide the best available conclusion or return no_path."
    )


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
