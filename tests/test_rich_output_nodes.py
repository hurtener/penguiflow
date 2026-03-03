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
    describe_component,
    list_artifacts,
    render_component,
    ui_confirm,
    ui_form,
    ui_select_option,
)
from penguiflow.rich_output.runtime import RichOutputConfig, configure_rich_output, reset_runtime
from penguiflow.rich_output.tools import (
    ListArtifactsArgs,
    RenderComponentArgs,
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
