"""Tool nodes for emitting rich UI components."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, overload

from penguiflow.catalog import tool
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
    BuildAccordionArgs,
    BuildChartEChartsArgs,
    BuildComponentResult,
    BuildGridArgs,
    BuildTableArgs,
    BuildTabsArgs,
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

if TYPE_CHECKING:
    from penguiflow.artifacts import ArtifactRef
    from penguiflow.planner import ToolContext
    from penguiflow.planner.artifact_registry import (
        ArtifactRecord,
        ArtifactRegistry,
    )

logger = logging.getLogger(__name__)


def _ensure_enabled() -> None:
    runtime = get_runtime()
    if not runtime.config.enabled:
        msg = "Rich output is disabled for this planner"
        raise RuntimeError(msg)


def _emit_metadata(
    extra: Mapping[str, Any] | None, *, source_tool: str = "render_component"
) -> dict[str, Any]:
    metadata = dict(extra or {})
    if "source_tool" not in metadata:
        metadata["source_tool"] = source_tool
    return metadata


def _dedupe_key(payload: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except Exception:
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _tool_dedupe_payload(
    source_tool: str, payload: object
) -> Mapping[str, Any]:
    return {
        "tool": source_tool,
        "payload": payload,
    }


def _summarise_component(
    component: str, props: Mapping[str, Any], *, verb: str = "Rendered"
) -> str:
    if component == "report":
        sections = props.get("sections")
        if isinstance(sections, list):
            return f"{verb} report ({len(sections)} sections)"
        return f"{verb} report"
    if component == "grid":
        items = props.get("items")
        if isinstance(items, list):
            return f"{verb} grid ({len(items)} items)"
        return f"{verb} grid"
    if component == "tabs":
        tabs = props.get("tabs")
        if isinstance(tabs, list):
            return f"{verb} tabs ({len(tabs)} tabs)"
        return f"{verb} tabs"
    if component == "accordion":
        items = props.get("items")
        if isinstance(items, list):
            return f"{verb} accordion ({len(items)} items)"
        return f"{verb} accordion"
    return f"{verb} {component}"


@tool(
    desc="Request a rich UI component render (passive).",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_component(
    args: RenderComponentArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render any registry component visibly."""
    component, props = build_render_tool_payload("render_component", args) or (
        args.component,
        dict(args.props),
    )
    return await _process_component_payload(
        component=component,
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.metadata,
        source_tool="render_component",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Render an ECharts visualization using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_chart_echarts(
    args: RenderChartEChartsArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render an ECharts chart visibly."""
    _, props = build_render_tool_payload("render_chart_echarts", args) or (
        "echarts",
        {},
    )
    return await _process_component_payload(
        component="echarts",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_chart_echarts",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Render a document-style report using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_report(
    args: RenderReportArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render a document-style report visibly."""
    _, props = build_render_tool_payload("render_report", args) or (
        "report",
        {},
    )
    return await _process_component_payload(
        component="report",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_report",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Render a data table using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_table(
    args: RenderTableArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render a data grid table visibly."""
    _, props = build_render_tool_payload("render_table", args) or (
        "datagrid",
        {},
    )
    return await _process_component_payload(
        component="datagrid",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_table",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Render a dashboard grid using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_grid(
    args: RenderGridArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render a grid of components visibly."""
    _, props = build_render_tool_payload("render_grid", args) or ("grid", {})
    return await _process_component_payload(
        component="grid",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_grid",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Render tabbed content using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_tabs(
    args: RenderTabsArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render tabs visibly."""
    _, props = build_render_tool_payload("render_tabs", args) or ("tabs", {})
    return await _process_component_payload(
        component="tabs",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_tabs",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Render an accordion using typed convenience args.",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def render_accordion(
    args: RenderAccordionArgs, ctx: ToolContext
) -> RenderComponentResult:
    """Render an accordion visibly."""
    _, props = build_render_tool_payload("render_accordion", args) or (
        "accordion",
        {},
    )
    return await _process_component_payload(
        component="accordion",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="render_accordion",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=True,
        result_model=RenderComponentResult,
    )


@tool(
    desc="Build a reusable ECharts visualization without emitting it yet.",
    tags=["rich_output", "ui", "builder"],
    side_effects="pure",
)
async def build_chart_echarts(
    args: BuildChartEChartsArgs, ctx: ToolContext
) -> BuildComponentResult:
    """Build an ECharts chart as a reusable artifact ref."""
    _, props = build_render_tool_payload("build_chart_echarts", args) or (
        "echarts",
        {},
    )
    return await _process_component_payload(
        component="echarts",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="build_chart_echarts",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=False,
        result_model=BuildComponentResult,
    )


@tool(
    desc="Build a reusable data table without emitting it yet.",
    tags=["rich_output", "ui", "builder"],
    side_effects="pure",
)
async def build_table(
    args: BuildTableArgs, ctx: ToolContext
) -> BuildComponentResult:
    """Build a data grid table as a reusable artifact ref."""
    _, props = build_render_tool_payload("build_table", args) or (
        "datagrid",
        {},
    )
    return await _process_component_payload(
        component="datagrid",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="build_table",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=False,
        result_model=BuildComponentResult,
    )


@tool(
    desc="Build a reusable grid layout without emitting it yet.",
    tags=["rich_output", "ui", "builder"],
    side_effects="pure",
)
async def build_grid(
    args: BuildGridArgs, ctx: ToolContext
) -> BuildComponentResult:
    """Build a grid of components as a reusable artifact ref."""
    _, props = build_render_tool_payload("build_grid", args) or ("grid", {})
    return await _process_component_payload(
        component="grid",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="build_grid",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=False,
        result_model=BuildComponentResult,
    )


@tool(
    desc="Build reusable tabs without emitting them yet.",
    tags=["rich_output", "ui", "builder"],
    side_effects="pure",
)
async def build_tabs(
    args: BuildTabsArgs, ctx: ToolContext
) -> BuildComponentResult:
    """Build tabs as a reusable artifact ref."""
    _, props = build_render_tool_payload("build_tabs", args) or ("tabs", {})
    return await _process_component_payload(
        component="tabs",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="build_tabs",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=False,
        result_model=BuildComponentResult,
    )


@tool(
    desc="Build a reusable accordion without emitting it yet.",
    tags=["rich_output", "ui", "builder"],
    side_effects="pure",
)
async def build_accordion(
    args: BuildAccordionArgs, ctx: ToolContext
) -> BuildComponentResult:
    """Build an accordion as a reusable artifact ref."""
    _, props = build_render_tool_payload("build_accordion", args) or (
        "accordion",
        {},
    )
    return await _process_component_payload(
        component="accordion",
        props=props,
        ctx=ctx,
        component_id=args.id,
        title=args.title,
        metadata=args.artifact_metadata,
        source_tool="build_accordion",
        dedupe_payload=args.model_dump(mode="json"),
        emit_visible=False,
        result_model=BuildComponentResult,
    )


def _describe_component_example(component: str) -> str:
    """Return an example ``describe_component`` call for ``component``."""
    call = {"next_node": "describe_component", "args": {"name": component}}
    return "Example: " + json.dumps(call, separators=(",", ":"))


def _checked_component_inputs(
    component: object, props: object, *, source_tool: str
) -> tuple[str, Mapping[str, Any]]:
    """Check the component name and props types (guards model_construct)."""
    if not isinstance(component, str):
        msg = f"{source_tool} requires a component name"
        raise TypeError(msg)
    if not isinstance(props, Mapping):
        msg = f"{source_tool} props must be an object"
        raise TypeError(msg)
    return component, props


def _validation_hint(
    exc: RichOutputValidationError, component: str, *, source_tool: str
) -> str:
    """Explain a props validation failure and how to recover from it."""
    if source_tool == "render_component":
        retry = (
            "then retry `render_component` with props matching that schema.\n"
        )
    else:
        retry = (
            f"then retry `{source_tool}` with arguments that produce "
            f"valid `{component}` props.\n"
        )
    return (
        f"{exc}\n"
        "To fix this, call `describe_component` with the component "
        "name to get the exact props schema, "
        f"{retry}"
        f"{_describe_component_example(component)}"
    )


@overload
async def _process_component_payload(
    *,
    component: str,
    props: Mapping[str, Any] | dict[str, Any],
    ctx: ToolContext,
    component_id: str | None,
    title: str | None,
    metadata: Mapping[str, Any] | None,
    source_tool: str,
    dedupe_payload: object = None,
    emit_visible: Literal[True],
    result_model: type[RenderComponentResult],
) -> RenderComponentResult: ...


@overload
async def _process_component_payload(
    *,
    component: str,
    props: Mapping[str, Any] | dict[str, Any],
    ctx: ToolContext,
    component_id: str | None,
    title: str | None,
    metadata: Mapping[str, Any] | None,
    source_tool: str,
    dedupe_payload: object = None,
    emit_visible: Literal[False],
    result_model: type[BuildComponentResult],
) -> BuildComponentResult: ...


async def _process_component_payload(
    *,
    component: object,
    props: object,
    ctx: ToolContext,
    component_id: str | None,
    title: str | None,
    metadata: Mapping[str, Any] | None,
    source_tool: str,
    dedupe_payload: object = None,
    emit_visible: bool,
    result_model: type[RenderComponentResult] | type[BuildComponentResult],
) -> RenderComponentResult | BuildComponentResult:
    runtime = get_runtime()
    if not runtime.config.enabled:
        msg = "Rich output is disabled for this planner"
        raise RuntimeError(msg)

    payload = {
        "component": component,
        "props": props,
        "id": component_id,
        "title": title,
        "metadata": metadata,
    }
    base_dedupe_payload = (
        dedupe_payload if dedupe_payload is not None else payload
    )
    dedupe = _dedupe_key(
        _tool_dedupe_payload(source_tool, base_dedupe_payload)
    )

    component, props = _checked_component_inputs(
        component, props, source_tool=source_tool
    )
    resolved_props: Mapping[str, Any] = props
    registry = get_artifact_registry(ctx)
    if registry is None and not emit_visible:
        msg = (
            f"{source_tool} requires an active planner run so it can "
            "return an artifact_ref"
        )
        raise RuntimeError(msg)
    if has_artifact_refs(resolved_props):
        if registry is None:
            msg = "artifact_ref usage requires an active planner run"
            raise RuntimeError(msg)
        session_id = ctx.tool_context.get("session_id")
        resolved: object = await resolve_artifact_refs_async(
            resolved_props,
            registry=registry,
            trajectory=getattr(ctx, "_trajectory", None),
            session_id=str(session_id) if session_id is not None else None,
            artifact_store=getattr(ctx, "artifacts", None),
        )
        if not isinstance(resolved, Mapping):
            msg = "artifact_ref resolution returned invalid props"
            raise TypeError(msg)
        resolved_props = resolved

    try:
        runtime.validate_component(
            component, resolved_props, tool_context=ctx.tool_context
        )
    except RichOutputValidationError as exc:
        hint = _validation_hint(exc, component, source_tool=source_tool)
        raise RuntimeError(hint) from exc

    meta = _emit_metadata(metadata, source_tool=source_tool)
    meta.setdefault("registry_version", runtime.registry.version)

    verb = "Rendered" if emit_visible else "Built"
    summary = _summarise_component(component, resolved_props, verb=verb)

    artifact_ref: str | None = None
    if registry is not None:
        record = _register_component_payload(
            ctx,
            registry=registry,
            source_tool=source_tool,
            component=component,
            props=resolved_props,
            component_id=component_id,
            title=title,
            meta=meta,
            summary=summary,
        )
        artifact_ref = record.ref

    if emit_visible:
        await _emit_component_artifact(
            ctx,
            component=component,
            props=resolved_props,
            component_id=component_id,
            title=title,
            meta=meta,
        )
    return result_model(
        ok=True,
        component=component,
        artifact_ref=artifact_ref,
        dedupe_key=dedupe,
        summary=summary,
    )


def _register_component_payload(
    ctx: ToolContext,
    *,
    registry: ArtifactRegistry,
    source_tool: str,
    component: str,
    props: Mapping[str, Any],
    component_id: str | None,
    title: str | None,
    meta: Mapping[str, Any],
    summary: str,
) -> ArtifactRecord:
    trajectory = getattr(ctx, "_trajectory", None)
    step_index = len(getattr(trajectory, "steps", []) or [])
    record = registry.register_tool_artifact(
        source_tool,
        "ui",
        {
            "id": component_id,
            "component": component,
            "props": dict(props),
            "title": title,
            "summary": summary,
            "metadata": dict(meta),
        },
        step_index=step_index,
    )
    metadata_state = getattr(trajectory, "metadata", None)
    if isinstance(metadata_state, dict):
        registry.write_snapshot(metadata_state)
    return record


async def _emit_component_artifact(
    ctx: ToolContext,
    *,
    component: str,
    props: Mapping[str, Any],
    component_id: str | None,
    title: str | None,
    meta: Mapping[str, Any],
) -> None:
    await ctx.emit_artifact(
        "ui",
        {
            "id": component_id,
            "component": component,
            "props": props,
            "title": title,
        },
        done=True,
        artifact_type="ui_component",
        meta=dict(meta),
    )


def _registry_items(
    ctx: ToolContext, *, kind: str | None, source_tool: str | None
) -> list[dict[str, Any]]:
    """List the in-run ArtifactRegistry records, if a registry is active."""
    registry = get_artifact_registry(ctx)
    if registry is None:
        return []
    planner = getattr(ctx, "planner", None)
    trajectory = getattr(planner, "active_trajectory", None)
    if trajectory is not None:
        with contextlib.suppress(Exception):
            registry.ingest_background_results(
                getattr(trajectory, "background_results", None)
            )
    llm_context = getattr(ctx, "llm_context", None)
    if llm_context is not None:
        with contextlib.suppress(Exception):
            registry.ingest_llm_context(llm_context)
    return list(registry.list_records(kind=kind, source_tool=source_tool))


def _stored_item(ref: ArtifactRef, source_tool: object) -> dict[str, Any]:
    """Describe a persistent artifact as a listing item."""
    return {
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
    }


async def _with_stored_items(
    ctx: ToolContext, items: list[dict[str, Any]], *, source_tool: str | None
) -> list[dict[str, Any]]:
    """Append persistent ArtifactStore items, replacing same-id records."""
    scoped = getattr(ctx, "artifacts", None)
    if scoped is None:
        return items
    try:
        refs = await scoped.list()
        seen_ids = {
            item.get("artifact_id")
            for item in items
            if item.get("artifact_id")
        }
        for ref in refs:
            # Persistent store wins dedup: replace registry entry if same ID
            if ref.id in seen_ids:
                items = [
                    item for item in items if item.get("artifact_id") != ref.id
                ]
            ref_tool = ref.source.get("tool")
            if source_tool and ref_tool != source_tool:
                continue
            items.append(_stored_item(ref, ref_tool))
    except Exception as e:
        logger.debug(
            "Failed to list persistent artifacts: %s", e, exc_info=True
        )
    return items


@tool(
    desc="List available artifacts for reuse in UI components.",
    tags=["rich_output", "artifacts"],
    side_effects="read",
)
async def list_artifacts(
    args: ListArtifactsArgs, ctx: ToolContext
) -> ListArtifactsResult:
    """List in-run and stored artifacts for reuse in components."""
    _ensure_enabled()
    # Backward/behavioral compatibility: callers often use kind="tool_artifact"
    # when they really mean "any tool-produced artifact" (including
    # ui_component).
    kind = None if args.kind in {"all", "tool_artifact"} else args.kind

    # Registry items come first; persistent store items are appended and win
    # deduplication on the same artifact id.
    items = _registry_items(ctx, kind=kind, source_tool=args.source_tool)
    if kind is None or kind == "binary":
        items = await _with_stored_items(
            ctx, items, source_tool=args.source_tool
        )

    # -- Step 3: Apply limit -- Persistent store items come after registry
    # items, so [-limit:] favors them.
    if args.limit and args.limit > 0:
        items = items[-args.limit :]

    return ListArtifactsResult(
        artifacts=[ArtifactSummary.model_validate(item) for item in items]
    )


@tool(
    desc="Collect structured input via a UI form (pauses for user).",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def ui_form(args: UIFormArgs, ctx: ToolContext) -> UIInteractionResult:
    """Show a form and pause until the user submits it."""
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


@tool(
    desc="Request user confirmation via UI (pauses for user).",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def ui_confirm(
    args: UIConfirmArgs, ctx: ToolContext
) -> UIInteractionResult:
    """Ask for a yes/no confirmation and pause until the user answers."""
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


@tool(
    desc="Request user selection via UI (pauses for user).",
    tags=["rich_output", "ui"],
    side_effects="pure",
)
async def ui_select_option(
    args: UISelectOptionArgs, ctx: ToolContext
) -> UIInteractionResult:
    """Ask the user to pick an option and pause until they choose."""
    runtime = get_runtime()
    _ensure_enabled()

    props = args.model_dump(by_alias=True, exclude_none=True)
    runtime.validate_component(
        "select_option", props, tool_context=ctx.tool_context
    )

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


@tool(
    desc="Describe a UI component and its schema.",
    tags=["rich_output", "ui"],
    side_effects="read",
)
async def describe_component(
    args: DescribeComponentArgs, _ctx: ToolContext
) -> DescribeComponentResult:
    """Return a component's full props schema and example."""
    _ensure_enabled()
    runtime = get_runtime()
    component = runtime.describe_component(args.name)
    return DescribeComponentResult(component=component)


__all__ = [
    "build_accordion",
    "build_chart_echarts",
    "build_grid",
    "build_table",
    "build_tabs",
    "describe_component",
    "list_artifacts",
    "render_accordion",
    "render_chart_echarts",
    "render_component",
    "render_grid",
    "render_report",
    "render_table",
    "render_tabs",
    "ui_confirm",
    "ui_form",
    "ui_select_option",
]
