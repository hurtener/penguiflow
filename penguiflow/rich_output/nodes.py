"""Tool nodes for emitting rich UI components."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from penguiflow.catalog import tool
from penguiflow.planner import ToolContext
from penguiflow.planner.artifact_registry import (
    get_artifact_registry,
    has_artifact_refs,
    resolve_artifact_refs_async,
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
from .validate import RichOutputValidationError


def _ensure_enabled() -> None:
    runtime = get_runtime()
    if not runtime.config.enabled:
        raise RuntimeError("Rich output is disabled for this planner")


def _emit_metadata(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    metadata = dict(extra or {})
    if "source_tool" not in metadata:
        metadata["source_tool"] = "render_component"
    return metadata


def _dedupe_key(payload: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _summarise_component(component: str, props: Mapping[str, Any]) -> str:
    if component == "report":
        sections = props.get("sections")
        if isinstance(sections, list):
            return f"Rendered report ({len(sections)} sections)"
        return "Rendered report"
    if component == "grid":
        items = props.get("items")
        if isinstance(items, list):
            return f"Rendered grid ({len(items)} items)"
        return "Rendered grid"
    if component == "tabs":
        tabs = props.get("tabs")
        if isinstance(tabs, list):
            return f"Rendered tabs ({len(tabs)} tabs)"
        return "Rendered tabs"
    if component == "accordion":
        items = props.get("items")
        if isinstance(items, list):
            return f"Rendered accordion ({len(items)} items)"
        return "Rendered accordion"
    return f"Rendered {component}"


@tool(desc="Request a rich UI component render (passive).", tags=["rich_output", "ui"], side_effects="pure")
async def render_component(args: RenderComponentArgs, ctx: ToolContext) -> RenderComponentResult:
    runtime = get_runtime()
    if not runtime.config.enabled:
        raise RuntimeError("Rich output is disabled for this planner")

    payload = args.model_dump(by_alias=True)
    component = payload.get("component")
    props = payload.get("props") or {}
    dedupe = _dedupe_key(payload)

    if not isinstance(component, str):
        raise RuntimeError("render_component requires a component name")
    if not isinstance(props, Mapping):
        raise RuntimeError("render_component props must be an object")

    registry = get_artifact_registry(ctx)
    if has_artifact_refs(props):
        if registry is None:
            raise RuntimeError("artifact_ref usage requires an active planner run")
        session_id = ctx.tool_context.get("session_id")
        props = await resolve_artifact_refs_async(
            props,
            registry=registry,
            trajectory=getattr(ctx, "_trajectory", None),
            session_id=str(session_id) if session_id is not None else None,
            artifact_store=getattr(ctx, "artifacts", None),
        )
        if not isinstance(props, Mapping):
            raise RuntimeError("artifact_ref resolution returned invalid props")

    try:
        runtime.validate_component(component, props, tool_context=ctx.tool_context)
    except RichOutputValidationError as exc:
        hint = (
            f"{exc}\n"
            "To fix this, call `describe_component` with the component name to get the exact props schema, "
            "then retry `render_component` with props matching that schema.\n"
            f'Example: {{"next_node":"describe_component","args":{{"name":"{component}"}}}}'
        )
        raise RuntimeError(hint) from exc

    meta = _emit_metadata(args.metadata)
    meta.setdefault("registry_version", runtime.registry.version)

    summary = _summarise_component(component, props)

    # Register the rendered component payload into the in-run artifact registry (if available),
    # so the model can reference it later via artifact_ref and avoid re-render loops.
    artifact_ref: str | None = None
    if registry is not None:
        trajectory = getattr(ctx, "_trajectory", None)
        step_index = len(getattr(trajectory, "steps", []) or [])
        record = registry.register_tool_artifact(
            "render_component",
            "ui",
            {
                "component": component,
                "props": dict(props),
                "title": payload.get("title"),
                "summary": summary,
                "metadata": dict(meta),
            },
            step_index=step_index,
        )
        artifact_ref = record.ref
        metadata = getattr(trajectory, "metadata", None)
        if isinstance(metadata, dict):
            registry.write_snapshot(metadata)

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
    return RenderComponentResult(
        ok=True,
        component=component,
        artifact_ref=artifact_ref,
        dedupe_key=dedupe,
        summary=summary,
    )


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
    # Backward/behavioral compatibility: callers often use kind="tool_artifact"
    # when they really mean "any tool-produced artifact" (including ui_component).
    kind = None if args.kind in {"all", "tool_artifact"} else args.kind

    # Background task results live on the active trajectory (legacy data may still
    # be stored in llm_context). Those artifacts must be ingested into the in-run
    # registry so they can be referenced via artifact_ref and resolved later.
    planner = getattr(ctx, "_planner", None)
    trajectory = getattr(planner, "_active_trajectory", None)
    if trajectory is not None:
        try:
            registry.ingest_background_results(getattr(trajectory, "background_results", None))
        except Exception:
            pass
    llm_context = getattr(ctx, "llm_context", None)
    if llm_context is not None:
        try:
            registry.ingest_llm_context(llm_context)
        except Exception:
            # Never fail listing due to best-effort ingestion.
            pass
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
