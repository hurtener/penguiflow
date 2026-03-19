from __future__ import annotations

from penguiflow.rich_output.tools import (
    AccordionItem,
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
    args = RenderComponentArgs(component="markdown")
    assert args.props == {}
    assert args.model_dump(by_alias=True)["props"] == {}


def test_list_artifacts_args_defaults() -> None:
    args = ListArtifactsArgs()
    assert args.kind == "all"
    assert args.limit == 25


def test_render_chart_echarts_args_aliases() -> None:
    args = RenderChartEChartsArgs(option={"series": []}, artifactMetadata={"source": "test"})
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["artifactMetadata"] == {"source": "test"}


def test_render_report_args_accept_nested_sections() -> None:
    args = RenderReportArgs(
        title="Quarterly",
        sections=[
            ReportSection(
                title="Summary",
                subsections=[ReportSection(title="Highlights", content="Key points")],
            )
        ],
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["sections"][0]["subsections"][0]["title"] == "Highlights"


def test_render_table_args_aliases() -> None:
    args = RenderTableArgs(
        columns=[DataGridColumn(field="revenue", header="Revenue")],
        rows=[{"revenue": 10}],
        pageSize=25,
        artifactMetadata={"source": "test"},
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["pageSize"] == 25
    assert dumped["artifactMetadata"] == {"source": "test"}


def test_render_grid_args_aliases() -> None:
    args = RenderGridArgs(
        items=[GridItem(component="metric", props={"label": "Users", "value": 1}, colSpan=2)],
        equalHeight=False,
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["equalHeight"] is False
    assert dumped["items"][0]["colSpan"] == 2


def test_render_tabs_args_aliases() -> None:
    args = RenderTabsArgs(
        tabs=[TabItem(label="Overview", content="Hi")],
        defaultTab=1,
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["defaultTab"] == 1


def test_render_accordion_args_aliases() -> None:
    args = RenderAccordionArgs(
        items=[AccordionItem(title="Details", content="More", defaultOpen=True)],
        allowMultiple=True,
    )
    dumped = args.model_dump(by_alias=True, exclude_none=True)
    assert dumped["allowMultiple"] is True
    assert dumped["items"][0]["defaultOpen"] is True


def test_form_field_validation_aliases() -> None:
    validation = FormFieldValidation(minLength=2, maxLength=5)
    dumped = validation.model_dump(by_alias=True)
    assert dumped["minLength"] == 2
    assert dumped["maxLength"] == 5


def test_ui_form_args_accepts_aliases() -> None:
    args = UIFormArgs(
        title="Upload",
        fields=[FormField(name="email", type="email")],
        submitLabel="Send",
        cancelLabel="Skip",
    )
    dumped = args.model_dump(by_alias=True)
    assert dumped["submitLabel"] == "Send"
    assert dumped["cancelLabel"] == "Skip"


def test_ui_confirm_args_defaults() -> None:
    args = UIConfirmArgs(message="Continue?")
    assert args.confirm_label == "Confirm"
    assert args.cancel_label == "Cancel"


def test_ui_select_option_aliases() -> None:
    args = UISelectOptionArgs(
        options=[SelectOptionItem(value="a", label="Option A")],
        minSelections=2,
        maxSelections=3,
    )
    dumped = args.model_dump(by_alias=True)
    assert dumped["minSelections"] == 2
    assert dumped["maxSelections"] == 3
