from __future__ import annotations

import json
from typing import Any

from ...catalog import ToolLoadingMode, tool
from ...planner.llm import _coerce_llm_response, _sanitize_json_schema
from ...planner.models import PlannerEvent
from ..models import SkillProposalDraft, SkillProposeRequest, SkillProposeResponse


class SkillProposeArgs(SkillProposeRequest):
    pass


_SKILL_PROPOSAL_SCHEMA_NAME = "skill_proposal"


def _build_skill_proposal_messages(args: SkillProposeArgs) -> list[dict[str, str]]:
    hint_lines: list[str] = []
    if args.title_hint:
        hint_lines.append(f"title_hint: {args.title_hint}")
    if args.trigger_hint:
        hint_lines.append(f"trigger_hint: {args.trigger_hint}")
    if args.task_type:
        hint_lines.append(f"task_type: {args.task_type}")
    if args.required_tool_names:
        hint_lines.append(f"required_tool_names: {json.dumps(args.required_tool_names, ensure_ascii=False)}")
    if args.required_namespaces:
        hint_lines.append(f"required_namespaces: {json.dumps(args.required_namespaces, ensure_ascii=False)}")
    if args.required_tags:
        hint_lines.append(f"required_tags: {json.dumps(args.required_tags, ensure_ascii=False)}")
    hints = "\n".join(hint_lines) if hint_lines else "(none)"

    return [
        {
            "role": "system",
            "content": (
                "Draft a reusable skill playbook from the provided source material. "
                "Return JSON only. The draft must be implementation-ready, concise, and safe. "
                "Do not claim to save or persist anything. "
                "Prefer neutral names and clear operational steps. "
                "Only include required_tool_names, required_namespaces, and required_tags "
                "when they are well-supported by the source or hints."
            ),
        },
        {
            "role": "user",
            "content": (
                "Create a skill draft.\n\n"
                f"Hints:\n{hints}\n\n"
                f"Source material:\n{args.source_material}"
            ),
        },
    ]


@tool(desc="Draft a structured skill playbook from freeform source material.", loading_mode=ToolLoadingMode.ALWAYS)
async def skill_propose(args: SkillProposeArgs, ctx: Any) -> SkillProposeResponse:
    planner = getattr(ctx, "_planner", None)
    if planner is None or getattr(planner, "_client", None) is None:
        raise RuntimeError("skill_propose is not configured")

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": _SKILL_PROPOSAL_SCHEMA_NAME,
            "schema": _sanitize_json_schema(SkillProposalDraft.model_json_schema()),
        },
    }
    llm_result = await planner._client.complete(
        messages=_build_skill_proposal_messages(args),
        response_format=response_format,
    )
    raw, cost = _coerce_llm_response(llm_result)
    planner._cost_tracker.record_main_call(cost)
    draft = SkillProposalDraft.model_validate_json(raw)

    planner._emit_event(
        PlannerEvent(
            event_type="skill_propose",
            ts=planner._time_source(),
            trajectory_step=0,
            extra={
                "task_type": args.task_type,
                "required_tool_names": list(args.required_tool_names),
                "required_namespaces": list(args.required_namespaces),
                "required_tags": list(args.required_tags),
                "warning_count": len(draft.warnings),
                "assumption_count": len(draft.assumptions),
            },
        )
    )
    return SkillProposeResponse(draft=draft)


__all__ = ["SkillProposeArgs", "SkillProposeResponse", "skill_propose"]
