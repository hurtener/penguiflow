from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory
from penguiflow.planner.validation_repair import _attempt_arg_fill


class _ArgsModel(BaseModel):
    component: str
    props: dict[str, Any] = {}
    id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] | None = None


class _OutModel(BaseModel):
    ok: bool = True


class _Spec:
    def __init__(self, name: str) -> None:
        self.name = name
        self.args_model = _ArgsModel
        self.out_model = _OutModel


class _Client:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ):
        del messages, response_format, stream, on_stream_chunk
        return self._raw, 0.0


async def _build_messages(_trajectory) -> list[dict[str, str]]:
    return [{"role": "system", "content": "stub"}]


def _emit_event(_event) -> None:
    return None


@pytest.mark.asyncio
async def test_arg_fill_for_render_component_preserves_props_from_action_args() -> None:
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
        spec=_Spec("render_component"),
        action=PlannerAction(next_node="render_component", args={}),
        missing_fields=["component"],
        build_messages=_build_messages,  # type: ignore[arg-type]
        client=_Client(raw),
        cost_tracker=type("_C", (), {"record_main_call": lambda *_args, **_kwargs: None})(),
        emit_event=_emit_event,
        time_source=lambda: 0.0,
    )
    assert filled is not None
    assert filled["component"] == "report"
    assert isinstance(filled.get("props"), dict)
    assert "sections" in filled["props"]


@pytest.mark.asyncio
async def test_arg_fill_for_non_render_component_still_extracts_only_missing_fields() -> None:
    raw = '{"component":"report","props":{"sections":[{"title":"x"}]}}'
    trajectory = Trajectory(query="q", llm_context={}, tool_context={})
    filled = await _attempt_arg_fill(
        trajectory=trajectory,
        spec=_Spec("other_tool"),
        action=PlannerAction(next_node="other_tool", args={}),
        missing_fields=["component"],
        build_messages=_build_messages,  # type: ignore[arg-type]
        client=_Client(raw),
        cost_tracker=type("_C", (), {"record_main_call": lambda *_args, **_kwargs: None})(),
        emit_event=_emit_event,
        time_source=lambda: 0.0,
    )
    assert filled == {"component": "report"}
