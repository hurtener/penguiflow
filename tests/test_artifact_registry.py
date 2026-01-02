from __future__ import annotations

from penguiflow.artifacts import ArtifactRef
from penguiflow.planner.artifact_registry import ArtifactRegistry


def test_registry_registers_tool_artifact_and_resolves() -> None:
    registry = ArtifactRegistry()
    payload = {
        "type": "echarts",
        "config": {"title": {"text": "Revenue"}, "series": [{"data": [1, 2, 3]}]},
        "title": "Revenue Trend",
        "chart_type": "line",
    }

    record = registry.register_tool_artifact(
        "gather_data_from_genie",
        "chart_artifacts",
        payload,
        step_index=1,
    )

    assert record.kind == "ui_component"
    assert record.component == "echarts"
    summaries = registry.list_records()
    assert summaries[0]["component"] == "echarts"

    resolved = registry.resolve_ref(record.ref, trajectory=None, session_id=None)
    assert resolved is not None
    assert resolved["component"] == "echarts"
    assert resolved["props"]["option"]["title"]["text"] == "Revenue"


def test_registry_registers_binary_artifact_and_resolves() -> None:
    registry = ArtifactRegistry()
    ref = ArtifactRef(
        id="tableau_abc123",
        mime_type="image/png",
        size_bytes=2048,
        filename="sales.png",
    )

    record = registry.register_binary_artifact(ref, source_tool="tableau", step_index=2)
    summaries = registry.list_records(kind="binary")
    assert summaries[0]["artifact_id"] == "tableau_abc123"

    resolved = registry.resolve_ref(record.ref, trajectory=None, session_id="sess-1")
    assert resolved is not None
    assert resolved["component"] == "image"
    assert resolved["props"]["src"] == "/artifacts/tableau_abc123?session_id=sess-1"
