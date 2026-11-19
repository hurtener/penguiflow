"""Tests for arg fill render component."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import NodeSpec
from penguiflow.node import Node
from penguiflow.planner.models import JSONLLMClient
from penguiflow.planner.trajectory import Trajectory
from penguiflow.planner.validation_repair import _attempt_arg_fill

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.models import PlannerEvent


class _ArgsModel(BaseModel):
    component: str
    props: dict[str, Any] = {}
    id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None


class _OutModel(BaseModel):
    ok: bool = True


async def _noop(_args: _ArgsModel, _ctx: object) -> _OutModel:
    """No-op tool: only its spec takes part in the tests."""
    return _OutModel()


def _spec(name: str) -> NodeSpec:
    """Build the spec of a tool called ``name`` with the test models."""
    return NodeSpec(
        node=Node(_noop, name=name),
        name=name,
        desc="Test tool.",
        args_model=_ArgsModel,
        out_model=_OutModel,
    )


class _Client(JSONLLMClient):
    def __init__(self, raw: str) -> None:
        self._raw = raw

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        return self._raw, 0.0


async def _build_messages(_trajectory: Trajectory) -> list[dict[str, str]]:
    return [{"role": "system", "content": "stub"}]


def _emit_event(_event: PlannerEvent) -> None:
    return None


@pytest.mark.asyncio
async def test_arg_fill_render_component_keeps_action_props() -> None:
    """Arg fill for render component preserves props from action args."""
    raw = """
    {
      "next_node": "render_component",
      "args": {
        "component": "report",
        "props": {
          "sections": [{"title": "Summary", "content": "Hello"}]
        }
      }
    }
    """.strip()
    trajectory = Trajectory(query="q", llm_context={}, tool_context={})
    filled = await _attempt_arg_fill(
        trajectory=trajectory,
        spec=_spec("render_component"),
        missing_fields=["component"],
        build_messages=_build_messages,
        client=_Client(raw),
        cost_tracker=type(
            "_C", (), {"record_main_call": lambda *_args, **_kwargs: None}
        )(),
        emit_event=_emit_event,
        time_source=lambda: 0.0,
    )
    assert filled is not None
    assert filled["component"] == "report"
    assert isinstance(filled.get("props"), dict)
    assert "sections" in filled["props"]


@pytest.mark.asyncio
async def test_arg_fill_other_tools_extract_missing_fields() -> None:
    """Arg fill for non render component still extracts only missing fields."""
    raw = '{"component":"report","props":{"sections":[{"title":"x"}]}}'
    trajectory = Trajectory(query="q", llm_context={}, tool_context={})
    filled = await _attempt_arg_fill(
        trajectory=trajectory,
        spec=_spec("other_tool"),
        missing_fields=["component"],
        build_messages=_build_messages,
        client=_Client(raw),
        cost_tracker=type(
            "_C", (), {"record_main_call": lambda *_args, **_kwargs: None}
        )(),
        emit_event=_emit_event,
        time_source=lambda: 0.0,
    )
    assert filled == {"component": "report"}
