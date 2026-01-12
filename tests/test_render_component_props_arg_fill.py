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


class _ArgFillAwareClient:
    def __init__(self, *, first_action: Mapping[str, Any], final_action: Mapping[str, Any]) -> None:
        self._first_action = json.dumps(first_action, ensure_ascii=False)
        self._final_action = json.dumps(final_action, ensure_ascii=False)
        self.calls: list[list[Mapping[str, str]]] = []
        self._main_calls = 0

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del response_format, stream, on_stream_chunk
        self.calls.append(list(messages))
        last = messages[-1]["content"] if messages else ""

        if "FILL MISSING VALUES" in last:
            if '"component"' in last:
                return json.dumps({"component": "report"}, ensure_ascii=False), 0.0
            return (
                json.dumps(
                    {
                        "props": {
                            "sections": [
                                {"title": "Summary", "content": "Mock report section."},
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                0.0,
            )

        self._main_calls += 1
        if self._main_calls == 1:
            return self._first_action, 0.0
        return self._final_action, 0.0


def _make_rich_output_catalog() -> tuple[ModelRegistry, object]:
    reset_runtime()
    config = RichOutputConfig(enabled=True, allowlist=["report", "markdown", "echarts", "datagrid"])
    configure_rich_output(config)
    registry = ModelRegistry()
    nodes = list(attach_rich_output_nodes(registry, config=config))
    return registry, build_catalog(nodes, registry)


@pytest.mark.asyncio
async def test_render_component_schema_invalid_triggers_props_arg_fill() -> None:
    _, catalog = _make_rich_output_catalog()
    events: list[PlannerEvent] = []
    client = _ArgFillAwareClient(
        first_action={
            "thought": "render report",
            "next_node": "render_component",
            "args": {"component": "report"},
        },
        final_action={
            "thought": "done",
            "next_node": "final_response",
            "args": {"raw_answer": "ok"},
        },
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        max_iters=3,
        event_callback=events.append,
    )
    result = await planner.run("make a mock report")
    assert result.reason == "answer_complete"

    prop_attempts = [
        evt
        for evt in events
        if evt.event_type == "arg_fill_attempt" and evt.extra.get("missing_fields") == ["props"]
    ]
    assert prop_attempts, "expected arg-fill to run for render_component.props"

    tool_errors = [
        evt
        for evt in events
        if evt.event_type == "tool_call_result"
        and "Invalid props for 'report'" in str(evt.extra.get("result_json") or "")
    ]
    assert not tool_errors, "render_component should not be executed with invalid report props"


@pytest.mark.asyncio
async def test_render_component_autofill_component_then_props_in_same_step() -> None:
    _, catalog = _make_rich_output_catalog()
    events: list[PlannerEvent] = []
    client = _ArgFillAwareClient(
        first_action={
            "thought": "render report but forgot args",
            "next_node": "render_component",
            "args": {},
        },
        final_action={
            "thought": "done",
            "next_node": "final_response",
            "args": {"raw_answer": "ok"},
        },
    )
    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        max_iters=3,
        event_callback=events.append,
    )
    result = await planner.run("make a mock report")
    assert result.reason == "answer_complete"

    attempt_fields = [
        tuple(evt.extra.get("missing_fields") or [])
        for evt in events
        if evt.event_type == "arg_fill_attempt"
    ]
    assert ("component",) in attempt_fields
    assert ("props",) in attempt_fields
