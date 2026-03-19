"""Tool nodes for emitting rich UI components."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from penguiflow.catalog import tool
from penguiflow.planner import ToolContext
from penguiflow.planner.artifact_registry import (
    _binary_component_name,
    _binary_summary,
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
    RenderAccordionArgs,
    RenderChartEChartsArgs,
    RenderComponentArgs,
    RenderComponentResult,
    RenderGridArgs,
    RenderReportArgs,
    RenderTableArgs,
    RenderTabsArgs,
    UIConfirmArgs,
    UIFormArgs,
    UIInteractionResult,
    UISelectOptionArgs,
    build_render_tool_payload,
)
from .validate import RichOutputValidationError

logger = logging.getLogger(__name__)


def _ensure_enabled() -> None:
    runtime = get_runtime()
    if not runtime.config.enabled:
        raise RuntimeError("Rich output is disabled for this planner")


def _emit_metadata(extra: Mapping[str, Any] | None, *, source_tool: str = "render_component") -> dict[str, Any]:
    metadata = dict(extra or {})
    if "source_tool" not in metadata:
        metadata["source_tool"] = source_tool
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
    component, props = build_render_tool_payload("render_component", args) or (args.component, dict(args.props))
    return await _render_component_payload(
        component=component,
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.metadata,
        source_tool="render_component",
        dedupe_payload=args.model_dump(mode="json"),
    )


@tool(
    desc="Render an ECharts visualization using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_chart_echarts(args: RenderChartEChartsArgs, ctx: ToolContext) -> RenderComponentResult:
    _, props = build_render_tool_payload("render_chart_echarts", args) or ("echarts", {})
    return await _render_component_payload(
        component="echarts",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_chart_echarts",
        dedupe_payload=args.model_dump(mode="json"),
    )


@tool(
    desc="Render a document-style report using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_report(args: RenderReportArgs, ctx: ToolContext) -> RenderComponentResult:
    _, props = build_render_tool_payload("render_report", args) or ("report", {})
    return await _render_component_payload(
        component="report",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_report",
        dedupe_payload=args.model_dump(mode="json"),
    )


@tool(
    desc="Render a data table using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_table(args: RenderTableArgs, ctx: ToolContext) -> RenderComponentResult:
    _, props = build_render_tool_payload("render_table", args) or ("datagrid", {})
    return await _render_component_payload(
        component="datagrid",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_table",
        dedupe_payload=args.model_dump(mode="json"),
    )


@tool(
    desc="Render a dashboard grid using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_grid(args: RenderGridArgs, ctx: ToolContext) -> RenderComponentResult:
    _, props = build_render_tool_payload("render_grid", args) or ("grid", {})
    return await _render_component_payload(
        component="grid",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_grid",
        dedupe_payload=args.model_dump(mode="json"),
    )


@tool(
    desc="Render tabbed content using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_tabs(args: RenderTabsArgs, ctx: ToolContext) -> RenderComponentResult:
    _, props = build_render_tool_payload("render_tabs", args) or ("tabs", {})
    return await _render_component_payload(
        component="tabs",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_tabs",
        dedupe_payload=args.model_dump(mode="json"),
    )


@tool(
    desc="Render an accordion using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_accordion(args: RenderAccordionArgs, ctx: ToolContext) -> RenderComponentResult:
    _, props = build_render_tool_payload("render_accordion", args) or ("accordion", {})
    return await _render_component_payload(
        component="accordion",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_accordion",
        dedupe_payload=args.model_dump(mode="json"),
    )


async def _render_component_payload(
    *,
    component: str,
    props: Mapping[str, Any] | dict[str, Any],
    ctx: ToolContext,
    component_id: str | None,
    title: str | None,
    metadata: Mapping[str, Any] | None,
    source_tool: str,
    dedupe_payload: Any | None = None,
) -> RenderComponentResult:
    runtime = get_runtime()
    if not runtime.config.enabled:
        raise RuntimeError("Rich output is disabled for this planner")

    payload = {
        "component": component,
        "props": props,
        "id": component_id,
        "title": title,
        "metadata": metadata,
    }
    dedupe = _dedupe_key(dedupe_payload if dedupe_payload is not None else payload)

    if not isinstance(component, str):
        raise RuntimeError(f"{source_tool} requires a component name")
    if not isinstance(props, Mapping):
        raise RuntimeError(f"{source_tool} props must be an object")

    resolved_props: Mapping[str, Any] = props
    registry = get_artifact_registry(ctx)
    if has_artifact_refs(resolved_props):
        if registry is None:
            raise RuntimeError("artifact_ref usage requires an active planner run")
        session_id = ctx.tool_context.get("session_id")
        resolved_props = await resolve_artifact_refs_async(
            resolved_props,
            registry=registry,
            trajectory=getattr(ctx, "_trajectory", None),
            session_id=str(session_id) if session_id is not None else None,
            artifact_store=getattr(ctx, "artifacts", None),
        )
        if not isinstance(resolved_props, Mapping):
            raise RuntimeError("artifact_ref resolution returned invalid props")

    try:
        runtime.validate_component(component, resolved_props, tool_context=ctx.tool_context)
    except RichOutputValidationError as exc:
        if source_tool == "render_component":
            hint = (
                f"{exc}\n"
                "To fix this, call `describe_component` with the component name to get the exact props schema, "
                "then retry `render_component` with props matching that schema.\n"
                f'Example: {{"next_node":"describe_component","args":{{"name":"{component}"}}}}'
            )
        else:
            hint = (
                f"{exc}\n"
                "To fix this, call `describe_component` with the component name to get the exact props schema, "
                f"then retry `{source_tool}` with arguments that produce valid `{component}` props.\n"
                f'Example: {{"next_node":"describe_component","args":{{"name":"{component}"}}}}'
            )
        raise RuntimeError(hint) from exc

    meta = _emit_metadata(metadata, source_tool=source_tool)
    meta.setdefault("registry_version", runtime.registry.version)

    summary = _summarise_component(component, resolved_props)

    artifact_ref: str | None = None
    if registry is not None:
        trajectory = getattr(ctx, "_trajectory", None)
        step_index = len(getattr(trajectory, "steps", []) or [])
        record = registry.register_tool_artifact(
            source_tool,
            "ui",
            {
                "component": component,
                "props": dict(resolved_props),
                "title": title,
                "summary": summary,
                "metadata": dict(meta),
            },
            step_index=step_index,
        )
        artifact_ref = record.ref
        metadata_state = getattr(trajectory, "metadata", None)
        if isinstance(metadata_state, dict):
            registry.write_snapshot(metadata_state)

    await ctx.emit_artifact(
        "ui",
        {
            "id": component_id,
            "component": component,
            "props": resolved_props,
            "title": title,
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
    # Backward/behavioral compatibility: callers often use kind="tool_artifact"
    # when they really mean "any tool-produced artifact" (including ui_component).
    kind = None if args.kind in {"all", "tool_artifact"} else args.kind

    items: list[dict[str, Any]] = []

    # -- Step 1: Query in-run ArtifactRegistry (if available) --
    registry = get_artifact_registry(ctx)
    if registry is not None:
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
                pass
        items.extend(registry.list_records(kind=kind, source_tool=args.source_tool))

    # -- Step 2: Query persistent ArtifactStore (appended after registry) --
    if kind is None or kind == "binary":
        scoped = getattr(ctx, "artifacts", None)
        if scoped is not None:
            try:
                refs = await scoped.list()
                # Build set of IDs already in items (from registry) for dedup
                seen_ids = {item.get("artifact_id") for item in items if item.get("artifact_id")}
                for ref in refs:
                    # Persistent store wins dedup: replace registry entry if same ID
                    if ref.id in seen_ids:
                        items = [item for item in items if item.get("artifact_id") != ref.id]
                    source_tool = ref.source.get("tool")
                    if args.source_tool and source_tool != args.source_tool:
                        continue
                    items.append({
                        "ref": ref.id,
                        "kind": "binary",
                        "source_tool": source_tool,
                        "component": _binary_component_name(ref.mime_type),
                        "title": ref.filename,
                        "summary": _binary_summary(ref),
                        "artifact_id": ref.id,
                        "mime_type": ref.mime_type,
                        "size_bytes": ref.size_bytes,
                        "created_step": None,
                        "renderable": True,
                        "metadata": {},
                    })
            except Exception as e:
                logger.debug("Failed to list persistent artifacts: %s", e, exc_info=True)

    # -- Step 3: Apply limit --
    # Persistent store items come after registry items, so [-limit:] favors them.
    if args.limit and args.limit > 0:
        items = items[-args.limit:]

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
    "render_chart_echarts",
    "render_report",
    "render_table",
    "render_grid",
    "render_tabs",
    "render_accordion",
    "list_artifacts",
    "ui_form",
    "ui_confirm",
    "ui_select_option",
    "describe_component",
]
