from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts
from penguiflow.planner import Trajectory
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.planner.trajectory import BackgroundTaskResult
from penguiflow.rich_output.nodes import (
    _dedupe_key,
    _summarise_component,
    build_accordion,
    build_chart_echarts,
    build_grid,
    build_table,
    build_tabs,
    describe_component,
    list_artifacts,
    render_accordion,
    render_chart_echarts,
    render_component,
    render_grid,
    render_report,
    render_table,
    render_tabs,
    ui_confirm,
    ui_form,
    ui_select_option,
)
from penguiflow.rich_output.runtime import RichOutputConfig, configure_rich_output, reset_runtime
from penguiflow.rich_output.tools import (
    AccordionItem,
    BuildAccordionArgs,
    BuildChartEChartsArgs,
    BuildGridArgs,
    BuildTableArgs,
    BuildTabsArgs,
    DataGridColumn,
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
    TabItem,
    UIConfirmArgs,
    UIFormArgs,
    UISelectOptionArgs,
)


class PauseSignal(Exception):
    def __init__(self, payload: dict) -> None:
        super().__init__("paused")
        self.payload = payload


class DummyContext:
    def __init__(self) -> None:
        self._llm_context: dict = {}
        self._artifacts_store = InMemoryArtifactStore()
        self._scoped_artifacts = ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )
        self.tool_context: dict = {}
        self.emitted: list[dict] = []

    @property
    def llm_context(self):  # type: ignore[no-untyped-def]
        return self._llm_context

    @property
    def _artifacts(self):  # type: ignore[no-untyped-def]
        return self._artifacts_store

    @property
    def artifacts(self):  # type: ignore[no-untyped-def]
        return self._scoped_artifacts

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: dict,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: dict | None = None,
    ) -> None:
        self.emitted.append(
            {
                "stream_id": stream_id,
                "chunk": chunk,
                "done": done,
                "artifact_type": artifact_type,
                "meta": meta,
            }
        )

    async def pause(self, reason: str, payload: dict | None = None):
        raise PauseSignal(payload or {})


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    reset_runtime()


@pytest.mark.asyncio
async def test_render_component_emits_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["markdown"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    args = RenderComponentArgs(component="markdown", props={"content": "Hello"})
    result = await render_component(args, ctx)
    assert result.ok is True
    assert ctx.emitted
    emitted = ctx.emitted[0]
    assert emitted["artifact_type"] == "ui_component"
    assert emitted["chunk"]["component"] == "markdown"


@pytest.mark.asyncio
async def test_render_report_emits_report_component_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "markdown"], max_payload_bytes=4000, max_total_bytes=8000)
    )
    ctx = DummyContext()
    args = RenderReportArgs(
        title="Quarterly Report",
        sections=[ReportSection(title="Summary", content="All good.")],
    )
    result = await render_report(args, ctx)
    assert result.ok is True
    emitted = ctx.emitted[0]
    assert emitted["chunk"]["component"] == "report"
    assert emitted["chunk"]["props"]["title"] == "Quarterly Report"
    assert emitted["meta"]["source_tool"] == "render_report"


@pytest.mark.asyncio
async def test_render_chart_echarts_injects_title_when_missing() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["echarts"], max_payload_bytes=4000, max_total_bytes=8000)
    )
    ctx = DummyContext()
    args = RenderChartEChartsArgs(title="Revenue", option={"series": [{"type": "line", "data": [1, 2, 3]}]})
    result = await render_chart_echarts(args, ctx)
    assert result.ok is True
    emitted = ctx.emitted[0]
    assert emitted["chunk"]["component"] == "echarts"
    assert emitted["chunk"]["props"]["option"]["title"] == {"text": "Revenue"}
    assert emitted["meta"]["source_tool"] == "render_chart_echarts"


@pytest.mark.asyncio
async def test_render_table_emits_datagrid_component_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["datagrid"], max_payload_bytes=4000, max_total_bytes=8000)
    )
    ctx = DummyContext()
    args = RenderTableArgs(
        title="Results",
        columns=[DataGridColumn(field="name", header="Name")],
        rows=[{"name": "PenguiFlow"}],
    )
    result = await render_table(args, ctx)
    assert result.ok is True
    emitted = ctx.emitted[0]
    assert emitted["chunk"]["component"] == "datagrid"
    assert emitted["chunk"]["title"] == "Results"
    assert emitted["chunk"]["props"]["columns"][0]["field"] == "name"


@pytest.mark.asyncio
async def test_render_grid_emits_grid_component_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["grid", "metric"], max_payload_bytes=4000, max_total_bytes=8000)
    )
    ctx = DummyContext()
    args = RenderGridArgs(
        title="Dashboard",
        items=[GridItem(component="metric", props={"label": "Users", "value": 42}, colSpan=2)],
    )
    result = await render_grid(args, ctx)
    assert result.ok is True
    emitted = ctx.emitted[0]
    assert emitted["chunk"]["component"] == "grid"
    assert emitted["chunk"]["props"]["items"][0]["colSpan"] == 2
    assert emitted["meta"]["source_tool"] == "render_grid"


@pytest.mark.asyncio
async def test_render_tabs_emits_tabs_component_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["tabs", "markdown"], max_payload_bytes=4000, max_total_bytes=8000)
    )
    ctx = DummyContext()
    args = RenderTabsArgs(
        title="Views",
        tabs=[
            TabItem(label="Overview", content="Hello"),
            TabItem(label="Details", component="markdown", props={"content": "World"}),
        ],
        defaultTab=1,
    )
    result = await render_tabs(args, ctx)
    assert result.ok is True
    emitted = ctx.emitted[0]
    assert emitted["chunk"]["component"] == "tabs"
    assert emitted["chunk"]["props"]["defaultTab"] == 1
    assert emitted["meta"]["source_tool"] == "render_tabs"


@pytest.mark.asyncio
async def test_render_accordion_emits_accordion_component_artifact() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["accordion", "markdown"],
            max_payload_bytes=4000,
            max_total_bytes=8000,
        )
    )
    ctx = DummyContext()
    args = RenderAccordionArgs(
        title="FAQ",
        items=[AccordionItem(title="One", content="First", defaultOpen=True)],
        allowMultiple=True,
    )
    result = await render_accordion(args, ctx)
    assert result.ok is True
    emitted = ctx.emitted[0]
    assert emitted["chunk"]["component"] == "accordion"
    assert emitted["chunk"]["props"]["allowMultiple"] is True
    assert emitted["chunk"]["props"]["items"][0]["defaultOpen"] is True
    assert emitted["meta"]["source_tool"] == "render_accordion"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allowlist", "builder", "args"),
    [
        (
            ["echarts"],
            build_chart_echarts,
            BuildChartEChartsArgs(option={"series": [{"type": "line", "data": [1, 2, 3]}]}, title="Revenue"),
        ),
        (
            ["datagrid"],
            build_table,
            BuildTableArgs(
                title="Rows",
                columns=[DataGridColumn(field="name", header="Name")],
                rows=[{"name": "PenguiFlow"}],
            ),
        ),
        (
            ["grid", "markdown"],
            build_grid,
            BuildGridArgs(items=[GridItem(component="markdown", props={"content": "A"})]),
        ),
        (
            ["tabs"],
            build_tabs,
            BuildTabsArgs(tabs=[TabItem(label="Overview", content="Hello")]),
        ),
        (
            ["accordion"],
            build_accordion,
            BuildAccordionArgs(items=[AccordionItem(title="Details", content="More")]),
        ),
    ],
)
async def test_build_tools_register_artifacts_without_emitting(
    allowlist: list[str],
    builder,
    args,
) -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=allowlist, max_payload_bytes=4000, max_total_bytes=8000)
    )
    ctx = DummyContext()
    registry = ArtifactRegistry()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)

    result = await builder(args, ctx)

    assert result.ok is True
    assert isinstance(result.artifact_ref, str)
    assert result.artifact_ref.startswith("artifact_")
    assert ctx.emitted == []
    records = registry.list_records()
    assert len(records) == 1
    assert records[0]["ref"] == result.artifact_ref


@pytest.mark.asyncio
async def test_render_component_raises_when_disabled() -> None:
    configure_rich_output(RichOutputConfig(enabled=False))
    ctx = DummyContext()
    args = RenderComponentArgs(component="markdown", props={"content": "Hello"})
    with pytest.raises(RuntimeError, match="Rich output is disabled"):
        await render_component(args, ctx)


def test_dedupe_key_falls_back_for_unserializable_payload() -> None:
    payload: dict[str, object] = {}
    payload["self"] = payload
    key = _dedupe_key(payload)
    assert len(key) == 16


@pytest.mark.parametrize(
    ("component", "props", "expected"),
    [
        ("report", {"sections": [1, 2]}, "Rendered report (2 sections)"),
        ("report", {}, "Rendered report"),
        ("grid", {"items": [1, 2, 3]}, "Rendered grid (3 items)"),
        ("tabs", {"tabs": [1]}, "Rendered tabs (1 tabs)"),
        ("accordion", {"items": [1]}, "Rendered accordion (1 items)"),
        ("custom", {}, "Rendered custom"),
    ],
)
def test_summarise_component_variants(component: str, props: dict, expected: str) -> None:
    assert _summarise_component(component, props) == expected


@pytest.mark.asyncio
async def test_render_component_validation_error_includes_schema_hint() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    # report requires sections, so this should fail validation.
    args = RenderComponentArgs(component="report", props={})
    with pytest.raises(RuntimeError) as exc:
        await render_component(args, ctx)
    message = str(exc.value)
    assert "describe_component" in message


@pytest.mark.asyncio
async def test_render_component_rejects_invalid_component_and_props_types() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["markdown"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()

    with pytest.raises(RuntimeError, match="requires a component name"):
        bad_component = RenderComponentArgs.model_construct(component=123, props={})
        await render_component(bad_component, ctx)

    with pytest.raises(RuntimeError, match="props must be an object"):
        bad_props = RenderComponentArgs.model_construct(component="markdown", props="nope")
        await render_component(bad_props, ctx)


@pytest.mark.asyncio
async def test_render_component_requires_registry_for_artifact_refs() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    args = RenderComponentArgs(
        component="report",
        props={"sections": [{"components": [{"artifact_ref": "artifact_0"}]}]},
    )

    with pytest.raises(RuntimeError, match="artifact_ref usage requires an active planner run"):
        await render_component(args, ctx)


@pytest.mark.asyncio
async def test_render_component_rejects_invalid_resolved_props() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=ArtifactRegistry())
    args = RenderComponentArgs(
        component="report",
        props={"sections": [{"components": [{"artifact_ref": "artifact_0"}]}]},
    )

    async def _resolve_invalid(*_args, **_kwargs):
        return []

    with pytest.raises(RuntimeError, match="artifact_ref resolution returned invalid props"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("penguiflow.rich_output.nodes.resolve_artifact_refs_async", _resolve_invalid)
            await render_component(args, ctx)


@pytest.mark.asyncio
async def test_ui_form_pauses_with_payload() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["form"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    args = UIFormArgs(fields=[{"name": "title", "type": "text"}])
    with pytest.raises(PauseSignal) as exc:
        await ui_form(args, ctx)
    assert exc.value.payload["component"] == "form"
    assert exc.value.payload["tool"] == "ui_form"


@pytest.mark.asyncio
async def test_render_component_resolves_artifact_refs() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=2000, max_total_bytes=4000)
    )
    registry = ArtifactRegistry()
    record = registry.register_tool_artifact(
        "gather_data_from_genie",
        "chart_artifacts",
        {
            "type": "echarts",
            "config": {"title": {"text": "Revenue"}, "series": [{"data": [1, 2, 3]}]},
        },
        step_index=0,
    )
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)
    args = RenderComponentArgs(
        component="report",
        props={
            "sections": [
                {
                    "title": "Section",
                    "components": [{"artifact_ref": record.ref, "caption": "Chart"}],
                }
            ],
        },
    )
    result = await render_component(args, ctx)
    assert result.ok is True
    emitted_props = ctx.emitted[0]["chunk"]["props"]
    component = emitted_props["sections"][0]["components"][0]
    assert component["component"] == "echarts"


@pytest.mark.asyncio
async def test_build_artifact_refs_render_report_cleanly() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["report", "grid", "markdown"],
            max_payload_bytes=6000,
            max_total_bytes=12000,
        )
    )
    registry = ArtifactRegistry()
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)

    built = await build_grid(
        BuildGridArgs(items=[GridItem(component="markdown", props={"content": "Nested"})], title="Nested Grid"),
        ctx,
    )
    result = await render_report(
        RenderReportArgs(
            title="Quarterly",
            sections=[
                ReportSection(
                    title="Summary",
                    components=[{"artifact_ref": built.artifact_ref, "caption": "Embedded grid"}],
                )
            ],
        ),
        ctx,
    )

    assert result.ok is True
    assert len(ctx.emitted) == 1
    component = ctx.emitted[0]["chunk"]["props"]["sections"][0]["components"][0]
    assert component["component"] == "grid"
    assert component["caption"] == "Embedded grid"


@pytest.mark.asyncio
async def test_build_artifact_refs_render_grid_cleanly_with_mixed_inline_items() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["grid", "echarts", "datagrid", "markdown"],
            max_payload_bytes=8000,
            max_total_bytes=16000,
        )
    )
    registry = ArtifactRegistry()
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)

    chart = await build_chart_echarts(
        BuildChartEChartsArgs(title="Revenue", option={"series": [{"type": "line", "data": [1, 2, 3]}]}),
        ctx,
    )
    table = await build_table(
        BuildTableArgs(
            columns=[DataGridColumn(field="name", header="Name")],
            rows=[{"name": "PenguiFlow"}],
            title="Rows",
        ),
        ctx,
    )
    result = await render_grid(
        RenderGridArgs(
            title="Dashboard",
            items=[
                GridItem.model_validate({"artifact_ref": chart.artifact_ref, "colSpan": 2}),
                GridItem.model_validate({"artifact_ref": table.artifact_ref}),
                GridItem(component="markdown", props={"content": "Inline note"}),
            ],
        ),
        ctx,
    )

    assert result.ok is True
    assert len(ctx.emitted) == 1
    items = ctx.emitted[0]["chunk"]["props"]["items"]
    assert items[0]["component"] == "echarts"
    assert items[1]["component"] == "datagrid"
    assert "title" not in items[1]
    assert items[2]["component"] == "markdown"


@pytest.mark.asyncio
async def test_build_artifact_refs_render_tabs_cleanly() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["tabs", "datagrid", "markdown"],
            max_payload_bytes=6000,
            max_total_bytes=12000,
        )
    )
    registry = ArtifactRegistry()
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)

    table = await build_table(
        BuildTableArgs(columns=[DataGridColumn(field="name")], rows=[{"name": "A"}]),
        ctx,
    )
    result = await render_tabs(
        RenderTabsArgs(
            tabs=[
                TabItem(label="Overview", content="Hello"),
                TabItem.model_validate({"label": "Data", "artifact_ref": table.artifact_ref}),
            ]
        ),
        ctx,
    )

    assert result.ok is True
    tabs = ctx.emitted[0]["chunk"]["props"]["tabs"]
    assert tabs[0]["content"] == "Hello"
    assert tabs[1]["component"] == "datagrid"


@pytest.mark.asyncio
async def test_build_artifact_refs_render_accordion_cleanly() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["accordion", "grid", "markdown"],
            max_payload_bytes=6000,
            max_total_bytes=12000,
        )
    )
    registry = ArtifactRegistry()
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)

    grid = await build_grid(
        BuildGridArgs(items=[GridItem(component="markdown", props={"content": "Nested"})]),
        ctx,
    )
    result = await render_accordion(
        RenderAccordionArgs(
            items=[
                AccordionItem(title="Inline", content="Text"),
                AccordionItem.model_validate({"title": "Built", "artifact_ref": grid.artifact_ref}),
            ]
        ),
        ctx,
    )

    assert result.ok is True
    items = ctx.emitted[0]["chunk"]["props"]["items"]
    assert items[0]["content"] == "Text"
    assert items[1]["component"] == "grid"


@pytest.mark.asyncio
async def test_list_artifacts_reads_registry() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=2000, max_total_bytes=4000)
    )
    registry = ArtifactRegistry()
    record = registry.register_tool_artifact(
        "gather_data_from_genie",
        "chart_artifacts",
        {"type": "echarts", "config": {"title": {"text": "Revenue"}}},
        step_index=0,
    )
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)
    result = await list_artifacts(ListArtifactsArgs(), ctx)
    assert result.artifacts
    assert result.artifacts[0].ref == record.ref


@pytest.mark.asyncio
async def test_list_artifacts_returns_empty_without_registry() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    result = await list_artifacts(ListArtifactsArgs(), ctx)
    assert result.artifacts == []


@pytest.mark.asyncio
async def test_list_artifacts_raises_when_disabled() -> None:
    configure_rich_output(RichOutputConfig(enabled=False))
    ctx = DummyContext()
    with pytest.raises(RuntimeError, match="Rich output is disabled"):
        await list_artifacts(ListArtifactsArgs(), ctx)


@pytest.mark.asyncio
async def test_list_artifacts_ignores_ingest_exceptions() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    registry = ArtifactRegistry()
    registry.ingest_background_results = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bg"))  # type: ignore[assignment]
    registry.ingest_llm_context = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ctx"))  # type: ignore[assignment]
    ctx = DummyContext()
    trajectory = Trajectory(query="x")
    ctx._planner = SimpleNamespace(_artifact_registry=registry, _active_trajectory=trajectory)

    result = await list_artifacts(ListArtifactsArgs(), ctx)
    assert result.artifacts == []


@pytest.mark.asyncio
async def test_list_artifacts_tool_artifact_kind_includes_ui_components() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=2000, max_total_bytes=4000)
    )
    registry = ArtifactRegistry()
    record = registry.register_tool_artifact(
        "gather_data_from_genie",
        "chart_artifacts",
        {"type": "echarts", "config": {"title": {"text": "Revenue"}}},
        step_index=0,
    )
    ctx = DummyContext()
    ctx._planner = SimpleNamespace(_artifact_registry=registry)
    result = await list_artifacts(ListArtifactsArgs(kind="tool_artifact"), ctx)
    assert [item.ref for item in result.artifacts] == [record.ref]


@pytest.mark.asyncio
async def test_list_artifacts_ingests_background_results_for_artifact_refs() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=5000, max_total_bytes=10000)
    )
    registry = ArtifactRegistry()
    ctx = DummyContext()
    trajectory = Trajectory(query="test")
    ctx._planner = SimpleNamespace(_artifact_registry=registry, _active_trajectory=trajectory)
    stored_payload = {
        "type": "echarts",
        "config": {"title": {"text": "From background"}, "series": [{"data": [1, 2, 3]}]},
    }
    ref = await ctx._artifacts.put_text(
        json.dumps(stored_payload, ensure_ascii=False),
        mime_type="application/json",
        filename="bg.echarts.json",
        namespace="test",
    )
    trajectory.background_results["t-bg"] = BackgroundTaskResult(
        task_id="t-bg",
        artifacts=[
            {
                "node": "gather_data_from_genie",
                "field": "chart_artifacts",
                "artifact": {"type": "echarts", "artifact": ref.model_dump(mode="json"), "title": "From bg"},
            }
        ],
    )

    listed = await list_artifacts(ListArtifactsArgs(sourceTool="gather_data_from_genie"), ctx)
    assert listed.artifacts
    artifact_ref = listed.artifacts[0].ref

    args = RenderComponentArgs(
        component="report",
        props={
            "sections": [
                {
                    "title": "Section",
                    "components": [{"artifact_ref": artifact_ref, "caption": "Chart"}],
                }
            ],
        },
    )
    result = await render_component(args, ctx)
    assert result.ok is True
    emitted_props = ctx.emitted[0]["chunk"]["props"]
    component = emitted_props["sections"][0]["components"][0]
    assert component["component"] == "echarts"


@pytest.mark.asyncio
async def test_ui_confirm_and_select_option_pause() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["confirm", "select_option"],
            max_payload_bytes=2000,
            max_total_bytes=2000,
        )
    )
    ctx = DummyContext()

    with pytest.raises(PauseSignal) as confirm_exc:
        await ui_confirm(UIConfirmArgs(message="Proceed?"), ctx)
    assert confirm_exc.value.payload["tool"] == "ui_confirm"
    assert confirm_exc.value.payload["component"] == "confirm"

    with pytest.raises(PauseSignal) as select_exc:
        await ui_select_option(
            UISelectOptionArgs(
                options=[{"label": "One", "value": "one"}],
            ),
            ctx,
        )
    assert select_exc.value.payload["tool"] == "ui_select_option"
    assert select_exc.value.payload["component"] == "select_option"


@pytest.mark.asyncio
async def test_ui_interactions_return_when_pause_does_not_raise() -> None:
    configure_rich_output(
        RichOutputConfig(
            enabled=True,
            allowlist=["form", "confirm", "select_option"],
            max_payload_bytes=2000,
            max_total_bytes=2000,
        )
    )

    class SoftPauseContext(DummyContext):
        async def pause(self, reason: str, payload: dict | None = None):
            self.tool_context["pause"] = {"reason": reason, "payload": payload}
            return None

    ctx = SoftPauseContext()

    form_result = await ui_form(UIFormArgs(fields=[{"name": "title", "type": "text"}]), ctx)
    confirm_result = await ui_confirm(UIConfirmArgs(message="Proceed?"), ctx)
    select_result = await ui_select_option(
        UISelectOptionArgs(options=[{"label": "One", "value": "one"}]),
        ctx,
    )

    assert form_result.ok is True
    assert confirm_result.ok is True
    assert select_result.ok is True


@pytest.mark.asyncio
async def test_describe_component_returns_component_schema() -> None:
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["markdown"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    result = await describe_component(args=SimpleNamespace(name="markdown"), ctx=ctx)  # type: ignore[arg-type]
    assert result.component["name"] == "markdown"


@pytest.mark.asyncio
async def test_list_artifacts_persistent_store_fallback() -> None:
    """Persistent store artifacts are returned even when no in-run registry exists."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    # No registry -- ctx._planner is not set, so get_artifact_registry returns None.
    ref = await ctx.artifacts.upload(
        b"binary content",
        mime_type="application/octet-stream",
        filename="data.bin",
        meta={"tool": "web_fetch"},
    )
    result = await list_artifacts(ListArtifactsArgs(), ctx)
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact.kind == "binary"
    assert artifact.artifact_id == ref.id
    assert artifact.renderable is True


@pytest.mark.asyncio
async def test_list_artifacts_deduplication_persistent_store_wins() -> None:
    """When registry and persistent store have the same artifact_id, persistent store wins."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report", "echarts"], max_payload_bytes=5000, max_total_bytes=10000)
    )
    ctx = DummyContext()
    # Upload a binary artifact to the persistent store
    ref = await ctx.artifacts.upload(
        b"binary content",
        mime_type="image/png",
        filename="chart.png",
        meta={"tool": "gather_data"},
    )
    # Register an artifact in the in-run registry, then manually set its artifact_id
    # to match the persistent store entry (register_tool_artifact does not accept artifact_id).
    registry = ArtifactRegistry()
    record = registry.register_tool_artifact(
        "gather_data",
        "chart_artifacts",
        {"type": "echarts", "config": {}},
        step_index=0,
    )
    record.artifact_id = ref.id
    ctx._planner = SimpleNamespace(_artifact_registry=registry)
    result = await list_artifacts(ListArtifactsArgs(), ctx)
    # The persistent store entry should replace the registry entry with the same artifact_id.
    matching = [a for a in result.artifacts if a.artifact_id == ref.id]
    assert len(matching) == 1
    assert matching[0].kind == "binary"


@pytest.mark.asyncio
async def test_list_artifacts_source_tool_filter_persistent_store() -> None:
    """source_tool filter is applied to persistent store entries."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    await ctx.artifacts.upload(
        b"binary content",
        mime_type="application/octet-stream",
        meta={"tool": "web_fetch"},
    )
    # Filter for a different tool -- should exclude the persistent artifact
    result = await list_artifacts(ListArtifactsArgs(sourceTool="other_tool"), ctx)
    assert result.artifacts == []

    # Filter for the correct tool -- should include it
    result = await list_artifacts(ListArtifactsArgs(sourceTool="web_fetch"), ctx)
    assert len(result.artifacts) == 1


@pytest.mark.asyncio
async def test_list_artifacts_ui_component_kind_skips_persistent_store() -> None:
    """kind='ui_component' skips persistent store (persistent artifacts are 'binary')."""
    configure_rich_output(
        RichOutputConfig(enabled=True, allowlist=["report"], max_payload_bytes=2000, max_total_bytes=2000)
    )
    ctx = DummyContext()
    await ctx.artifacts.upload(
        b"binary content",
        mime_type="application/octet-stream",
    )
    result = await list_artifacts(ListArtifactsArgs(kind="ui_component"), ctx)
    assert result.artifacts == []
