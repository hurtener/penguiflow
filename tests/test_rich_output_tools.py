"""Tests for rich output tools."""

from __future__ import annotations

import pytest

from penguiflow.rich_output.tools import (
    AccordionItem,
    BuildAccordionArgs,
    BuildChartEChartsArgs,
    BuildGridArgs,
    BuildTableArgs,
    BuildTabsArgs,
    DataGridColumn,
    FormField,
    FormFieldValidation,
    GridItem,
    ListArtifactsArgs,
    RenderAccordionArgs,
    RenderChartEChartsArgs,
    RenderComponentArgs,
    RenderGridArgs,
    RenderReportArgs,
    RenderTableArgs,
    RenderTabsArgs,
    ReportSection,
    SelectOptionItem,
    TabItem,
    UIConfirmArgs,
    UIFormArgs,
    UISelectOptionArgs,
)


def test_render_component_args_defaults() -> None:
    """Render component args defaults."""
    args = RenderComponentArgs(component="markdown")
    assert args.props == {}
    assert args.model_dump(by_alias=True)["props"] == {}


def test_list_artifacts_args_defaults() -> None:
    """List artifacts args defaults."""
    args = ListArtifactsArgs()
    assert args.kind == "all"
    assert args.limit == 25


def test_render_chart_echarts_args_aliases() -> None:
    """Render chart echarts args aliases."""
    args = RenderChartEChartsArgs.model_validate(
        {"option": {"series": []}, "artifactMetadata": {"source": "test"}}
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["artifactMetadata"] == {"source": "test"}


def test_build_chart_echarts_args_aliases() -> None:
    """Build chart echarts args aliases."""
    args = BuildChartEChartsArgs.model_validate(
        {"option": {"series": []}, "artifactMetadata": {"source": "test"}}
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["artifactMetadata"] == {"source": "test"}


def test_render_report_args_accept_nested_sections() -> None:
    """Render report args accept nested sections."""
    args = RenderReportArgs(
        title="Quarterly",
        sections=[
            ReportSection(
                title="Summary",
                subsections=[
                    ReportSection(title="Highlights", content="Key points")
                ],
            )
        ],
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["sections"][0]["subsections"][0]["title"] == "Highlights"


def test_render_table_args_aliases() -> None:
    """Render table args aliases."""
    args = RenderTableArgs.model_validate(
        {
            "columns": [DataGridColumn(field="revenue", header="Revenue")],
            "rows": [{"revenue": 10}],
            "pageSize": 25,
            "artifactMetadata": {"source": "test"},
        }
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["pageSize"] == 25
    assert dumped["artifactMetadata"] == {"source": "test"}


def test_build_table_args_aliases() -> None:
    """Build table args aliases."""
    args = BuildTableArgs.model_validate(
        {
            "columns": [DataGridColumn(field="revenue", header="Revenue")],
            "rows": [{"revenue": 10}],
            "pageSize": 25,
            "artifactMetadata": {"source": "test"},
        }
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["pageSize"] == 25
    assert dumped["artifactMetadata"] == {"source": "test"}


def test_render_grid_args_aliases() -> None:
    """Render grid args aliases."""
    args = RenderGridArgs.model_validate(
        {
            "items": [
                GridItem.model_validate(
                    {
                        "component": "metric",
                        "props": {"label": "Users", "value": 1},
                        "colSpan": 2,
                    }
                )
            ],
            "equalHeight": False,
        }
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["equalHeight"] is False
    assert dumped["items"][0]["colSpan"] == 2


def test_build_grid_args_aliases() -> None:
    """Build grid args aliases."""
    args = BuildGridArgs.model_validate(
        {
            "items": [
                GridItem.model_validate(
                    {
                        "component": "metric",
                        "props": {"label": "Users", "value": 1},
                        "colSpan": 2,
                    }
                )
            ],
            "equalHeight": False,
        }
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["equalHeight"] is False
    assert dumped["items"][0]["colSpan"] == 2


def test_render_tabs_args_aliases() -> None:
    """Render tabs args aliases."""
    args = RenderTabsArgs.model_validate(
        {"tabs": [TabItem(label="Overview", content="Hi")], "defaultTab": 1}
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["defaultTab"] == 1


def test_build_tabs_args_aliases() -> None:
    """Build tabs args aliases."""
    args = BuildTabsArgs.model_validate(
        {"tabs": [TabItem(label="Overview", content="Hi")], "defaultTab": 1}
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["defaultTab"] == 1


def test_render_accordion_args_aliases() -> None:
    """Render accordion args aliases."""
    args = RenderAccordionArgs.model_validate(
        {
            "items": [
                AccordionItem.model_validate(
                    {
                        "title": "Details",
                        "content": "More",
                        "defaultOpen": True,
                    }
                )
            ],
            "allowMultiple": True,
        }
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["allowMultiple"] is True
    assert dumped["items"][0]["defaultOpen"] is True


def test_build_accordion_args_aliases() -> None:
    """Build accordion args aliases."""
    args = BuildAccordionArgs.model_validate(
        {
            "items": [
                AccordionItem.model_validate(
                    {
                        "title": "Details",
                        "content": "More",
                        "defaultOpen": True,
                    }
                )
            ],
            "allowMultiple": True,
        }
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["allowMultiple"] is True
    assert dumped["items"][0]["defaultOpen"] is True


def test_composite_items_accept_artifact_refs() -> None:
    """Composite items accept artifact refs."""
    section = ReportSection(
        title="Summary",
        components=[{"artifact_ref": "artifact_1", "caption": "Chart"}],
    )
    grid = BuildGridArgs(items=[{"artifact_ref": "artifact_2", "colSpan": 2}])
    tabs = BuildTabsArgs(
        tabs=[{"label": "Overview", "artifact_ref": "artifact_3"}]
    )
    accordion = BuildAccordionArgs(
        items=[{"title": "Details", "artifact_ref": "artifact_4"}]
    )

    assert section.components is not None
    assert section.components[0].artifact_ref == "artifact_1"
    assert grid.items[0].artifact_ref == "artifact_2"
    assert tabs.tabs[0].artifact_ref == "artifact_3"
    assert accordion.items[0].artifact_ref == "artifact_4"


def test_report_item_rejects_ref_with_inline_payload() -> None:
    """Report component item rejects mixed artifact ref and inline payload."""
    with pytest.raises(
        ValueError, match="either artifact_ref or component\\+props"
    ):
        ReportSection(
            title="Summary",
            components=[
                {
                    "artifact_ref": "artifact_1",
                    "component": "markdown",
                    "props": {"content": "Hi"},
                }
            ],
        )


def test_grid_item_rejects_mixed_artifact_ref_and_inline_payload() -> None:
    """Grid item rejects mixed artifact ref and inline payload."""
    with pytest.raises(
        ValueError, match="either artifact_ref or component\\+props"
    ):
        BuildGridArgs(
            items=[
                {
                    "artifact_ref": "artifact_2",
                    "component": "markdown",
                    "props": {"content": "Hi"},
                }
            ]
        )


def test_form_field_validation_aliases() -> None:
    """Form field validation aliases."""
    validation = FormFieldValidation.model_validate(
        {"minLength": 2, "maxLength": 5}
    )
    dumped = validation.model_dump(by_alias=True)
    assert dumped["minLength"] == 2
    assert dumped["maxLength"] == 5


def test_ui_form_args_accepts_aliases() -> None:
    """UI form args accepts aliases."""
    args = UIFormArgs.model_validate(
        {
            "title": "Upload",
            "fields": [FormField(name="email", type="email")],
            "submitLabel": "Send",
            "cancelLabel": "Skip",
        }
    )
    dumped = args.model_dump(by_alias=True)
    assert dumped["submitLabel"] == "Send"
    assert dumped["cancelLabel"] == "Skip"


def test_ui_confirm_args_defaults() -> None:
    """UI confirm args defaults."""
    args = UIConfirmArgs(message="Continue?")
    assert args.confirm_label == "Confirm"
    assert args.cancel_label == "Cancel"


def test_ui_select_option_aliases() -> None:
    """UI select option aliases."""
    args = UISelectOptionArgs.model_validate(
        {
            "options": [SelectOptionItem(value="a", label="Option A")],
            "minSelections": 2,
            "maxSelections": 3,
        }
    )
    dumped = args.model_dump(by_alias=True)
    assert dumped["minSelections"] == 2
    assert dumped["maxSelections"] == 3
