"""Pydantic models and shared helpers for rich output tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RICH_OUTPUT_RENDER_TOOL_NAMES = frozenset(
    {
        "render_component",
        "render_chart_echarts",
        "render_report",
        "render_table",
        "render_grid",
        "render_tabs",
        "render_accordion",
    }
)

RICH_OUTPUT_RENDER_TOOL_COMPONENTS = {
    "render_chart_echarts": "echarts",
    "render_report": "report",
    "render_table": "datagrid",
    "render_grid": "grid",
    "render_tabs": "tabs",
    "render_accordion": "accordion",
}

RICH_OUTPUT_RENDER_TOOL_COMPLEX_FIELDS = {
    "render_component": frozenset({"props"}),
    "render_chart_echarts": frozenset({"option"}),
    "render_report": frozenset({"sections"}),
    "render_table": frozenset({"columns", "rows"}),
    "render_grid": frozenset({"items"}),
    "render_tabs": frozenset({"tabs"}),
    "render_accordion": frozenset({"items"}),
}

RICH_OUTPUT_RENDER_TOOL_REPAIR_FIELDS = {
    "render_component": "props",
    "render_report": "sections",
    "render_grid": "items",
    "render_tabs": "tabs",
    "render_accordion": "items",
}


class RenderComponentArgs(BaseModel):
    component: str = Field(..., description="Registry component name")
    props: dict[str, Any] = Field(default_factory=dict, description="Component props")
    id: str | None = Field(default=None, description="Optional stable component id")
    title: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class RenderComponentResult(BaseModel):
    ok: bool = True
    component: str | None = Field(default=None, description="Rendered component name")
    artifact_ref: str | None = Field(
        default=None,
        description="Artifact registry ref for the rendered component payload (if available).",
    )
    dedupe_key: str | None = Field(
        default=None,
        description="Stable hash of the rendered payload; useful for de-duplication.",
    )
    summary: str | None = Field(default=None, description="Compact description of what was rendered")
    skipped: str | None = Field(
        default=None,
        description="If set, render was skipped (e.g. duplicate_render).",
    )

    model_config = ConfigDict(extra="forbid")


class RenderChartEChartsArgs(BaseModel):
    option: dict[str, Any] = Field(..., description="ECharts option object")
    title: str | None = Field(default=None, description="Optional chart title convenience field")
    height: str | None = None
    width: str | None = None
    theme: str | None = None
    loading: bool | None = None
    id: str | None = Field(default=None, description="Optional stable component id")
    artifact_metadata: dict[str, Any] | None = Field(default=None, alias="artifactMetadata")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ReportComponentItem(BaseModel):
    component: str
    props: dict[str, Any] = Field(default_factory=dict)
    caption: str | None = None

    model_config = ConfigDict(extra="forbid")


class ReportSection(BaseModel):
    id: str | None = None
    title: str | None = None
    content: str | None = None
    components: list[ReportComponentItem] | None = None
    subsections: list[ReportSection] | None = None

    model_config = ConfigDict(extra="forbid")


ReportSection.model_rebuild()


class RenderReportArgs(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    metadata: dict[str, Any] | None = None
    toc: bool = False
    sections: list[ReportSection]
    footer: str | None = None
    id: str | None = Field(default=None, description="Optional stable component id")
    artifact_metadata: dict[str, Any] | None = Field(default=None, alias="artifactMetadata")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class DataGridColumn(BaseModel):
    field: str
    header: str | None = None
    width: int | None = None
    sortable: bool | None = None
    filterable: bool | None = None
    format: Literal["text", "number", "currency", "percent", "date", "datetime", "boolean"] | None = None
    align: Literal["left", "center", "right"] | None = None

    model_config = ConfigDict(extra="forbid")


class RenderTableArgs(BaseModel):
    columns: list[DataGridColumn]
    rows: list[dict[str, Any]]
    page_size: int = Field(default=10, alias="pageSize")
    sortable: bool = True
    filterable: bool = False
    selectable: bool = False
    exportable: bool = False
    striped: bool = True
    compact: bool = False
    title: str | None = Field(default=None, description="Optional artifact title")
    id: str | None = Field(default=None, description="Optional stable component id")
    artifact_metadata: dict[str, Any] | None = Field(default=None, alias="artifactMetadata")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class GridItem(BaseModel):
    component: str
    props: dict[str, Any] = Field(default_factory=dict)
    col_span: int | None = Field(default=None, alias="colSpan")
    row_span: int | None = Field(default=None, alias="rowSpan")
    title: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RenderGridArgs(BaseModel):
    columns: int = 2
    gap: str = "1rem"
    items: list[GridItem]
    equal_height: bool = Field(default=True, alias="equalHeight")
    id: str | None = Field(default=None, description="Optional stable component id")
    title: str | None = Field(default=None, description="Optional artifact title")
    artifact_metadata: dict[str, Any] | None = Field(default=None, alias="artifactMetadata")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class TabItem(BaseModel):
    id: str | None = None
    label: str
    icon: str | None = None
    component: str | None = None
    props: dict[str, Any] | None = None
    content: str | None = None
    disabled: bool = False

    model_config = ConfigDict(extra="forbid")


class RenderTabsArgs(BaseModel):
    tabs: list[TabItem]
    default_tab: int = Field(default=0, alias="defaultTab")
    variant: Literal["line", "enclosed", "pills"] = "line"
    id: str | None = Field(default=None, description="Optional stable component id")
    title: str | None = Field(default=None, description="Optional artifact title")
    artifact_metadata: dict[str, Any] | None = Field(default=None, alias="artifactMetadata")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AccordionItem(BaseModel):
    title: str
    content: str | None = None
    component: str | None = None
    props: dict[str, Any] | None = None
    default_open: bool = Field(default=False, alias="defaultOpen")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RenderAccordionArgs(BaseModel):
    items: list[AccordionItem]
    allow_multiple: bool = Field(default=False, alias="allowMultiple")
    id: str | None = Field(default=None, description="Optional stable component id")
    title: str | None = Field(default=None, description="Optional artifact title")
    artifact_metadata: dict[str, Any] | None = Field(default=None, alias="artifactMetadata")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def get_render_tool_component_name(tool_name: str) -> str | None:
    """Return the component emitted by a typed render tool, if fixed."""

    return RICH_OUTPUT_RENDER_TOOL_COMPONENTS.get(tool_name)


def get_render_tool_complex_fields(tool_name: str) -> frozenset[str]:
    """Return complex fields that are safe for arg-fill on render tools."""

    return RICH_OUTPUT_RENDER_TOOL_COMPLEX_FIELDS.get(tool_name, frozenset())


def get_render_tool_repair_field(tool_name: str) -> str | None:
    """Return the top-level field to repair after rich-output schema failures."""

    return RICH_OUTPUT_RENDER_TOOL_REPAIR_FIELDS.get(tool_name)


def build_render_tool_payload(tool_name: str, args: BaseModel) -> tuple[str, Any] | None:
    """Build the canonical component payload emitted by a render tool."""

    if tool_name == "render_component" and isinstance(args, RenderComponentArgs):
        return args.component, args.props

    if tool_name == "render_chart_echarts" and isinstance(args, RenderChartEChartsArgs):
        option = dict(args.option)
        if args.title and "title" not in option:
            option["title"] = {"text": args.title}
        return (
            "echarts",
            {
                "option": option,
                **({"height": args.height} if args.height is not None else {}),
                **({"width": args.width} if args.width is not None else {}),
                **({"theme": args.theme} if args.theme is not None else {}),
                **({"loading": args.loading} if args.loading is not None else {}),
            },
        )

    if tool_name == "render_report" and isinstance(args, RenderReportArgs):
        return "report", args.model_dump(by_alias=True, exclude={"id", "artifact_metadata"}, exclude_none=True)

    if tool_name == "render_table" and isinstance(args, RenderTableArgs):
        return (
            "datagrid",
            args.model_dump(by_alias=True, exclude={"id", "title", "artifact_metadata"}, exclude_none=True),
        )

    if tool_name == "render_grid" and isinstance(args, RenderGridArgs):
        return "grid", args.model_dump(by_alias=True, exclude={"id", "title", "artifact_metadata"}, exclude_none=True)

    if tool_name == "render_tabs" and isinstance(args, RenderTabsArgs):
        return "tabs", args.model_dump(by_alias=True, exclude={"id", "title", "artifact_metadata"}, exclude_none=True)

    if tool_name == "render_accordion" and isinstance(args, RenderAccordionArgs):
        return (
            "accordion",
            args.model_dump(by_alias=True, exclude={"id", "title", "artifact_metadata"}, exclude_none=True),
        )

    return None


ArtifactKind = Literal["ui_component", "binary", "tool_artifact"]
ArtifactKindFilter = Literal["all", "ui_component", "binary", "tool_artifact"]


class ArtifactSummary(BaseModel):
    ref: str
    kind: ArtifactKind
    source_tool: str | None = Field(default=None, alias="sourceTool")
    component: str | None = None
    title: str | None = None
    summary: str | None = None
    artifact_id: str | None = Field(default=None, alias="artifactId")
    mime_type: str | None = Field(default=None, alias="mimeType")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    created_step: int | None = Field(default=None, alias="createdStep")
    renderable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ListArtifactsArgs(BaseModel):
    kind: ArtifactKindFilter = "all"
    source_tool: str | None = Field(default=None, alias="sourceTool")
    limit: int = 25

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ListArtifactsResult(BaseModel):
    ok: bool = True
    artifacts: list[ArtifactSummary] = Field(default_factory=list)


class DescribeComponentArgs(BaseModel):
    name: str = Field(..., description="Component name")

    model_config = ConfigDict(extra="forbid")


class DescribeComponentResult(BaseModel):
    component: dict[str, Any]


class FormFieldOption(BaseModel):
    value: str
    label: str

    model_config = ConfigDict(extra="forbid")


class FormFieldValidation(BaseModel):
    min: float | None = None
    max: float | None = None
    min_length: int | None = Field(default=None, alias="minLength")
    max_length: int | None = Field(default=None, alias="maxLength")
    pattern: str | None = None
    message: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


FormFieldType = Literal[
    "text",
    "number",
    "email",
    "password",
    "url",
    "tel",
    "textarea",
    "select",
    "multiselect",
    "checkbox",
    "radio",
    "switch",
    "date",
    "datetime",
    "time",
    "file",
    "range",
    "color",
]


class FormField(BaseModel):
    name: str
    type: FormFieldType
    label: str | None = None
    placeholder: str | None = None
    required: bool = False
    disabled: bool = False
    default: Any = None
    options: list[str | FormFieldOption] | None = None
    validation: FormFieldValidation | None = None
    help_text: str | None = Field(default=None, alias="helpText")
    width: Literal["full", "half", "third"] = "full"

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UIFormArgs(BaseModel):
    title: str | None = None
    description: str | None = None
    fields: list[FormField]
    submit_label: str = Field(default="Submit", alias="submitLabel")
    cancel_label: str | None = Field(default=None, alias="cancelLabel")
    layout: Literal["vertical", "horizontal", "inline"] = "vertical"

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UIConfirmArgs(BaseModel):
    title: str | None = None
    message: str
    confirm_label: str = Field(default="Confirm", alias="confirmLabel")
    cancel_label: str = Field(default="Cancel", alias="cancelLabel")
    variant: Literal["info", "warning", "danger", "success"] = "info"
    details: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SelectOptionItem(BaseModel):
    value: str
    label: str
    description: str | None = None
    icon: str | None = None
    disabled: bool = False
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class UISelectOptionArgs(BaseModel):
    title: str | None = None
    description: str | None = None
    options: list[SelectOptionItem]
    multiple: bool = False
    min_selections: int = Field(default=1, alias="minSelections")
    max_selections: int | None = Field(default=None, alias="maxSelections")
    layout: Literal["list", "grid", "cards"] = "list"
    searchable: bool = False

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UIInteractionResult(BaseModel):
    ok: bool = True


__all__ = [
    "RICH_OUTPUT_RENDER_TOOL_NAMES",
    "RICH_OUTPUT_RENDER_TOOL_COMPONENTS",
    "RICH_OUTPUT_RENDER_TOOL_COMPLEX_FIELDS",
    "RICH_OUTPUT_RENDER_TOOL_REPAIR_FIELDS",
    "RenderComponentArgs",
    "RenderComponentResult",
    "RenderChartEChartsArgs",
    "ReportComponentItem",
    "ReportSection",
    "RenderReportArgs",
    "DataGridColumn",
    "RenderTableArgs",
    "GridItem",
    "RenderGridArgs",
    "TabItem",
    "RenderTabsArgs",
    "AccordionItem",
    "RenderAccordionArgs",
    "get_render_tool_component_name",
    "get_render_tool_complex_fields",
    "get_render_tool_repair_field",
    "build_render_tool_payload",
    "ArtifactSummary",
    "ListArtifactsArgs",
    "ListArtifactsResult",
    "DescribeComponentArgs",
    "DescribeComponentResult",
    "FormField",
    "UIFormArgs",
    "UIConfirmArgs",
    "SelectOptionItem",
    "UISelectOptionArgs",
    "UIInteractionResult",
]
