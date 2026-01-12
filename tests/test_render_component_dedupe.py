from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from penguiflow.catalog import build_catalog
from penguiflow.planner import PlannerEvent, ReactPlanner
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import (
    RichOutputConfig,
    attach_rich_output_nodes,
    configure_rich_output,
    reset_runtime,
)


class _StubClient:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self._responses = [json.dumps(item, ensure_ascii=False) for item in responses]

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del messages, response_format, stream, on_stream_chunk
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


def _make_catalog() -> object:
    reset_runtime()
    config = RichOutputConfig(enabled=True, allowlist=["report", "markdown", "echarts"])
    configure_rich_output(config)
    registry = ModelRegistry()
    nodes = list(attach_rich_output_nodes(registry, config=config))
    return build_catalog(nodes, registry)


@pytest.mark.asyncio
async def test_render_component_duplicate_is_skipped() -> None:
    catalog = _make_catalog()
    events: list[PlannerEvent] = []
    report_props = {"title": "t", "sections": [{"title": "s", "content": "c"}]}
    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "render",
                    "next_node": "render_component",
                    "args": {"component": "report", "props": report_props},
                },
                {
                    "thought": "render again",
                    "next_node": "render_component",
                    "args": {"component": "report", "props": report_props},
                },
                {"thought": "finish", "next_node": "final_response", "args": {"raw_answer": "ok"}},
            ]
        ),
        catalog=catalog,
        max_iters=4,
        event_callback=events.append,
    )
    result = await planner.run("make a report")
    assert result.reason == "answer_complete"

    artifact_chunks = [
        evt
        for evt in events
        if evt.event_type == "artifact_chunk" and evt.extra.get("artifact_type") == "ui_component"
    ]
    assert len(artifact_chunks) == 1

    tool_results = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result" and evt.extra.get("tool_name") == "render_component"
    ]
    assert len(tool_results) == 2
    parsed = [json.loads(str(evt.extra.get("result_json") or "{}")) for evt in tool_results]
    skipped = [item for item in parsed if item.get("skipped") == "duplicate_render"]
    assert skipped, "expected duplicate render to be skipped"
