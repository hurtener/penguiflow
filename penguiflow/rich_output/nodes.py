"""Tool nodes for emitting rich UI components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from penguiflow.catalog import tool
from penguiflow.planner import ToolContext
from penguiflow.planner.artifact_registry import (
    get_artifact_registry,
    has_artifact_refs,
    resolve_artifact_refs,
)

from .runtime import get_runtime
from .tools import (
    ArtifactSummary,
    DescribeComponentArgs,
    DescribeComponentResult,
    ListArtifactsArgs,
    ListArtifactsResult,
    RenderComponentArgs,
    RenderComponentResult,
    UIConfirmArgs,
    UIFormArgs,
    UIInteractionResult,
    UISelectOptionArgs,
)


def _ensure_enabled() -> None:
    runtime = get_runtime()
    if not runtime.config.enabled:
        raise RuntimeError("Rich output is disabled for this planner")


def _emit_metadata(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = dict(extra or {})
    if "source_tool" not in metadata:
        metadata["source_tool"] = "render_component"
    return metadata


@tool(desc="Request a rich UI component render (passive).", tags=["rich_output", "ui"], side_effects="pure")
async def render_component(args: RenderComponentArgs, ctx: ToolContext) -> RenderComponentResult:
    runtime = get_runtime()
    if not runtime.config.enabled:
        raise RuntimeError("Rich output is disabled for this planner")

    payload = args.model_dump(by_alias=True)
    component = payload.get("component")
    props = payload.get("props") or {}

    if not isinstance(component, str):
        raise RuntimeError("render_component requires a component name")
    if not isinstance(props, Mapping):
        raise RuntimeError("render_component props must be an object")

    registry = get_artifact_registry(ctx)
    if has_artifact_refs(props):
        if registry is None:
            raise RuntimeError("artifact_ref usage requires an active planner run")
        session_id = ctx.tool_context.get("session_id")
        props = resolve_artifact_refs(
            props,
            registry=registry,
            trajectory=getattr(ctx, "_trajectory", None),
            session_id=str(session_id) if session_id is not None else None,
        )
        if not isinstance(props, Mapping):
            raise RuntimeError("artifact_ref resolution returned invalid props")

    runtime.validate_component(component, props, tool_context=ctx.tool_context)

    meta = _emit_metadata(args.metadata)
    meta.setdefault("registry_version", runtime.registry.version)

    await ctx.emit_artifact(
        "ui",
        {
            "id": payload.get("id"),
            "component": component,
            "props": props,
            "title": payload.get("title"),
        },
        done=True,
        artifact_type="ui_component",
        meta=meta,
    )
    return RenderComponentResult()


@tool(
    desc="List available artifacts for reuse in UI components.",
    tags=["rich_output", "artifacts"],
    side_effects="read",
)
async def list_artifacts(args: ListArtifactsArgs, ctx: ToolContext) -> ListArtifactsResult:
    _ensure_enabled()
    registry = get_artifact_registry(ctx)
    if registry is None:
        return ListArtifactsResult(artifacts=[])
    kind = None if args.kind == "all" else args.kind
    items = registry.list_records(kind=kind, source_tool=args.source_tool, limit=args.limit)
    return ListArtifactsResult(artifacts=[ArtifactSummary.model_validate(item) for item in items])


@tool(desc="Collect structured input via a UI form (pauses for user).", tags=["rich_output", "ui"], side_effects="pure")
async def ui_form(args: UIFormArgs, ctx: ToolContext) -> UIInteractionResult:
    runtime = get_runtime()
    _ensure_enabled()

    props = args.model_dump(by_alias=True, exclude_none=True)
    runtime.validate_component("form", props, tool_context=ctx.tool_context)

    await ctx.pause(
        "await_input",
        {
            "tool": "ui_form",
            "component": "form",
            "props": props,
            "registry_version": runtime.registry.version,
        },
    )
    return UIInteractionResult()


@tool(desc="Request user confirmation via UI (pauses for user).", tags=["rich_output", "ui"], side_effects="pure")
async def ui_confirm(args: UIConfirmArgs, ctx: ToolContext) -> UIInteractionResult:
    runtime = get_runtime()
    _ensure_enabled()

    props = args.model_dump(by_alias=True, exclude_none=True)
    runtime.validate_component("confirm", props, tool_context=ctx.tool_context)

    await ctx.pause(
        "await_input",
        {
            "tool": "ui_confirm",
            "component": "confirm",
            "props": props,
            "registry_version": runtime.registry.version,
        },
    )
    return UIInteractionResult()


@tool(desc="Request user selection via UI (pauses for user).", tags=["rich_output", "ui"], side_effects="pure")
async def ui_select_option(args: UISelectOptionArgs, ctx: ToolContext) -> UIInteractionResult:
    runtime = get_runtime()
    _ensure_enabled()

    props = args.model_dump(by_alias=True, exclude_none=True)
    runtime.validate_component("select_option", props, tool_context=ctx.tool_context)

    await ctx.pause(
        "await_input",
        {
            "tool": "ui_select_option",
            "component": "select_option",
            "props": props,
            "registry_version": runtime.registry.version,
        },
    )
    return UIInteractionResult()


@tool(desc="Describe a UI component and its schema.", tags=["rich_output", "ui"], side_effects="read")
async def describe_component(args: DescribeComponentArgs, ctx: ToolContext) -> DescribeComponentResult:
    _ensure_enabled()
    runtime = get_runtime()
    del ctx
    component = runtime.describe_component(args.name)
    return DescribeComponentResult(component=component)


__all__ = [
    "render_component",
    "list_artifacts",
    "ui_form",
    "ui_confirm",
    "ui_select_option",
    "describe_component",
]
