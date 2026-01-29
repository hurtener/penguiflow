from typing import Any, Literal

from pydantic import BaseModel, Field

from ..catalog import ToolLoadingMode, tool
from .models import PlannerEvent


class ToolGetArgs(BaseModel):
    names: list[str] = Field(min_length=1, max_length=10)
    include_schemas: bool = True
    include_examples: bool = True


class ToolGetRecord(BaseModel):
    name: str
    desc: str
    side_effects: str
    loading_mode: ToolLoadingMode
    tags: list[str]
    auth_scopes: list[str]
    cost_hint: str | None = None
    latency_hint_ms: int | None = None
    safety_notes: str | None = None
    args_schema: dict[str, Any] | None = None
    out_schema: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)


class ToolGetResponse(BaseModel):
    tools: list[ToolGetRecord]
    requested: list[str]
    returned: int
    format: Literal["tool_record"] = "tool_record"


@tool(desc="Fetch a tool's schema/examples by name.", loading_mode=ToolLoadingMode.ALWAYS)
async def tool_get(args: ToolGetArgs, ctx: Any) -> ToolGetResponse:
    planner = getattr(ctx, "_planner", None)
    if planner is None:
        raise RuntimeError("tool_get requires a planner context")

    allowed_names = getattr(planner, "_tool_visibility_allowed_names", None)
    if allowed_names is None:
        execution_specs = getattr(planner, "_execution_specs", [])
        allowed_names = {spec.name for spec in execution_specs}

    execution_specs_by_name = getattr(planner, "_execution_spec_by_name", {}) or {}
    alias_to_real = getattr(planner, "_tool_aliases", {}) or {}

    records: list[ToolGetRecord] = []
    for raw_name in args.names:
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = alias_to_real.get(raw_name, raw_name)
        if name not in allowed_names:
            continue
        spec = execution_specs_by_name.get(name)
        if spec is None:
            continue
        record = spec.to_tool_record()
        if not args.include_schemas:
            record["args_schema"] = None
            record["out_schema"] = None
        if not args.include_examples:
            record["examples"] = []
        records.append(ToolGetRecord(**record))

    if not records:
        raise RuntimeError("tool not available")

    response = ToolGetResponse(
        tools=records,
        requested=list(args.names),
        returned=len(records),
    )

    planner._emit_event(
        PlannerEvent(
            event_type="tool_get",
            ts=planner._time_source(),
            trajectory_step=0,
            extra={
                "requested": list(args.names),
                "returned": len(records),
                "include_schemas": bool(args.include_schemas),
                "include_examples": bool(args.include_examples),
            },
        )
    )

    return response


__all__ = [
    "ToolGetArgs",
    "ToolGetRecord",
    "ToolGetResponse",
    "tool_get",
]
