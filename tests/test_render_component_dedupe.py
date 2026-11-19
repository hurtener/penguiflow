"""Tests for render component dedupe."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from penguiflow.catalog import NodeSpec, build_catalog
from penguiflow.planner import PlannerEvent, ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry
from penguiflow.rich_output.runtime import (
    RichOutputConfig,
    attach_rich_output_nodes,
    configure_rich_output,
    reset_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class _StubClient(JSONLLMClient):
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self._responses = [
            json.dumps(item, ensure_ascii=False) for item in responses
        ]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


def _make_catalog() -> list[NodeSpec]:
    reset_runtime()
    config = RichOutputConfig(
        enabled=True,
        allowlist=[
            "report",
            "markdown",
            "echarts",
            "grid",
            "tabs",
            "accordion",
            "datagrid",
        ],
    )
    configure_rich_output(config)
    registry = ModelRegistry()
    nodes = list(attach_rich_output_nodes(registry, config=config))
    return build_catalog(nodes, registry)


@pytest.mark.asyncio
async def test_render_component_duplicate_is_skipped() -> None:
    """Render component duplicate is skipped."""
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
                {
                    "thought": "finish",
                    "next_node": "final_response",
                    "args": {"raw_answer": "ok"},
                },
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
        if evt.event_type == "artifact_chunk"
        and evt.extra.get("artifact_type") == "ui_component"
    ]
    assert len(artifact_chunks) == 1

    tool_results = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result"
        and evt.extra.get("tool_name") == "render_component"
    ]
    assert len(tool_results) == 2
    parsed = [
        json.loads(str(evt.extra.get("result_json") or "{}"))
        for evt in tool_results
    ]
    skipped = [
        item for item in parsed if item.get("skipped") == "duplicate_render"
    ]
    assert skipped, "expected duplicate render to be skipped"


@pytest.mark.asyncio
async def test_render_report_duplicate_is_skipped() -> None:
    """Render report duplicate is skipped."""
    catalog = _make_catalog()
    events: list[PlannerEvent] = []
    report_args = {
        "title": "t",
        "sections": [{"title": "s", "content": "c"}],
    }
    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "render",
                    "next_node": "render_report",
                    "args": report_args,
                },
                {
                    "thought": "render again",
                    "next_node": "render_report",
                    "args": report_args,
                },
                {
                    "thought": "finish",
                    "next_node": "final_response",
                    "args": {"raw_answer": "ok"},
                },
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
        if evt.event_type == "artifact_chunk"
        and evt.extra.get("artifact_type") == "ui_component"
    ]
    assert len(artifact_chunks) == 1

    tool_results = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result"
        and evt.extra.get("tool_name") == "render_report"
    ]
    assert len(tool_results) == 2
    parsed = [
        json.loads(str(evt.extra.get("result_json") or "{}"))
        for evt in tool_results
    ]
    skipped = [
        item for item in parsed if item.get("skipped") == "duplicate_render"
    ]
    assert skipped, "expected duplicate render to be skipped"


@pytest.mark.asyncio
async def test_render_grid_duplicate_is_skipped() -> None:
    """Render grid duplicate is skipped."""
    catalog = _make_catalog()
    events: list[PlannerEvent] = []
    grid_args = {
        "items": [{"component": "markdown", "props": {"content": "A"}}],
        "columns": 1,
    }
    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "render",
                    "next_node": "render_grid",
                    "args": grid_args,
                },
                {
                    "thought": "render again",
                    "next_node": "render_grid",
                    "args": grid_args,
                },
                {
                    "thought": "finish",
                    "next_node": "final_response",
                    "args": {"raw_answer": "ok"},
                },
            ]
        ),
        catalog=catalog,
        max_iters=4,
        event_callback=events.append,
    )
    result = await planner.run("make a grid")
    assert result.reason == "answer_complete"

    artifact_chunks = [
        evt
        for evt in events
        if evt.event_type == "artifact_chunk"
        and evt.extra.get("artifact_type") == "ui_component"
    ]
    assert len(artifact_chunks) == 1

    tool_results = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result"
        and evt.extra.get("tool_name") == "render_grid"
    ]
    assert len(tool_results) == 2
    parsed = [
        json.loads(str(evt.extra.get("result_json") or "{}"))
        for evt in tool_results
    ]
    skipped = [
        item for item in parsed if item.get("skipped") == "duplicate_render"
    ]
    assert skipped, "expected duplicate render to be skipped"


@pytest.mark.asyncio
async def test_build_table_duplicate_is_skipped_without_visible_artifact() -> (
    None
):
    """Build table duplicate is skipped without visible artifact."""
    catalog = _make_catalog()
    events: list[PlannerEvent] = []
    table_args = {
        "title": "Rows",
        "columns": [{"field": "name", "header": "Name"}],
        "rows": [{"name": "PenguiFlow"}],
    }
    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "build",
                    "next_node": "build_table",
                    "args": table_args,
                },
                {
                    "thought": "build again",
                    "next_node": "build_table",
                    "args": table_args,
                },
                {
                    "thought": "finish",
                    "next_node": "final_response",
                    "args": {"raw_answer": "ok"},
                },
            ]
        ),
        catalog=catalog,
        max_iters=4,
        event_callback=events.append,
    )
    result = await planner.run("build a reusable table")
    assert result.reason == "answer_complete"

    artifact_chunks = [
        evt
        for evt in events
        if evt.event_type == "artifact_chunk"
        and evt.extra.get("artifact_type") == "ui_component"
    ]
    assert artifact_chunks == []

    tool_results = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result"
        and evt.extra.get("tool_name") == "build_table"
    ]
    assert len(tool_results) == 2
    parsed = [
        json.loads(str(evt.extra.get("result_json") or "{}"))
        for evt in tool_results
    ]
    skipped = [
        item for item in parsed if item.get("skipped") == "duplicate_build"
    ]
    assert skipped, "expected duplicate build to be skipped"


@pytest.mark.asyncio
async def test_build_then_render_table_not_cross_deduped() -> None:
    """Build then render table with same args is not cross deduped."""
    catalog = _make_catalog()
    events: list[PlannerEvent] = []
    table_args = {
        "title": "Rows",
        "columns": [{"field": "name", "header": "Name"}],
        "rows": [{"name": "PenguiFlow"}],
    }
    planner = ReactPlanner(
        llm_client=_StubClient(
            [
                {
                    "thought": "build",
                    "next_node": "build_table",
                    "args": table_args,
                },
                {
                    "thought": "render",
                    "next_node": "render_table",
                    "args": table_args,
                },
                {
                    "thought": "finish",
                    "next_node": "final_response",
                    "args": {"raw_answer": "ok"},
                },
            ]
        ),
        catalog=catalog,
        max_iters=5,
        event_callback=events.append,
    )
    result = await planner.run("build then render a reusable table")
    assert result.reason == "answer_complete"

    artifact_chunks = [
        evt
        for evt in events
        if evt.event_type == "artifact_chunk"
        and evt.extra.get("artifact_type") == "ui_component"
    ]
    assert len(artifact_chunks) == 1

    render_results = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result"
        and evt.extra.get("tool_name") == "render_table"
    ]
    assert len(render_results) == 1
    parsed = json.loads(
        str(render_results[0].extra.get("result_json") or "{}")
    )
    assert parsed.get("skipped") is None
