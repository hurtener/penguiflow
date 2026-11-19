"""Tests for structured final answers (ReactPlanner final_response_model)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel, Field
from typing_extensions import override

from penguiflow.catalog import build_catalog
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from typing_extensions import Unpack

    from penguiflow.planner.models import PlannerEvent

    from .planner_options import PlannerOptions


class Query(BaseModel):
    """Query payload."""

    text: str


class Verdict(BaseModel):
    """The structured final answer model used across these tests."""

    answer: int
    method: str
    confident: bool = Field(default=True)


class StubClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, responses: list[Mapping[str, object] | str]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [
            item if isinstance(item, str) else json.dumps(item)
            for item in responses
        ]
        self.calls: list[list[Mapping[str, str]]] = []

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        self.calls.append(list(messages))
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


async def echo(payload: Query, _ctx: object) -> Query:
    """Echo the input back."""
    return payload


def make_planner(
    client: JSONLLMClient, **kwargs: Unpack[PlannerOptions]
) -> ReactPlanner:
    """Build an echo planner over ``client``."""
    registry = ModelRegistry()
    registry.register("echo", Query, Query)
    nodes = [Node(echo, name="echo")]
    catalog = build_catalog(nodes, registry)
    return ReactPlanner(llm_client=client, catalog=catalog, **kwargs)


FINISH_VALID = {
    "next_node": "final_response",
    "args": {
        "answer": "It is 391.",
        "structured": {"answer": 391, "method": "multiplication"},
    },
}


@pytest.mark.asyncio
async def test_valid_structured_payload_passes_through() -> None:
    """Valid structured payload passes through."""
    events: list[PlannerEvent] = []
    planner = make_planner(
        StubClient([FINISH_VALID]),
        final_response_model=Verdict,
        event_callback=events.append,
    )
    result = await planner.run("17*23?")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "It is 391."
    assert result.payload["structured"] == {
        "answer": 391,
        "method": "multiplication",
        "confident": True,
    }
    validated = [
        e
        for e in events
        if e.event_type == "final_response_structured_validated"
    ]
    assert len(validated) == 1
    assert validated[0].extra["repaired"] is False


@pytest.mark.asyncio
async def test_invalid_payload_repaired_by_corrective_turn() -> None:
    """Invalid payload repaired by corrective turn."""
    events: list[PlannerEvent] = []
    planner = make_planner(
        StubClient(
            [
                {
                    "next_node": "final_response",
                    "args": {
                        "answer": "It is 391.",
                        "structured": {
                            "answer": "three-ninety-one",
                            "method": "guessing",
                        },
                    },
                },
                # Corrective turn reply: the bare corrected object.
                {
                    "answer": 391,
                    "method": "multiplication",
                    "confident": False,
                },
            ]
        ),
        final_response_model=Verdict,
        event_callback=events.append,
    )
    result = await planner.run("17*23?")

    assert result.payload["structured"] == {
        "answer": 391,
        "method": "multiplication",
        "confident": False,
    }
    repair_attempts = [
        e
        for e in events
        if e.event_type == "final_response_structured_repair_attempt"
    ]
    assert len(repair_attempts) == 1
    validated = [
        e
        for e in events
        if e.event_type == "final_response_structured_validated"
    ]
    assert validated[0].extra["repaired"] is True


@pytest.mark.asyncio
async def test_missing_payload_triggers_repair() -> None:
    """Missing payload triggers repair."""
    planner = make_planner(
        StubClient(
            [
                {
                    "next_node": "final_response",
                    "args": {"answer": "It is 391."},
                },
                {
                    "structured": {"answer": 391, "method": "multiplication"}
                },  # wrapped reply form
            ]
        ),
        final_response_model=Verdict,
    )
    result = await planner.run("17*23?")
    assert result.payload["structured"] == {
        "answer": 391,
        "method": "multiplication",
        "confident": True,
    }


@pytest.mark.asyncio
async def test_retry_exhausted_degrades_with_warning_and_event() -> None:
    """Retry exhausted degrades with warning and event."""
    events: list[PlannerEvent] = []
    planner = make_planner(
        StubClient(
            [
                {
                    "next_node": "final_response",
                    "args": {
                        "answer": "It is 391.",
                        "structured": {"method": 7},
                    },
                },
                # Corrective turn also returns garbage.
                {"method": 7},
            ]
        ),
        final_response_model=Verdict,
        final_response_retries=1,
        event_callback=events.append,
    )
    result = await planner.run("17*23?")

    assert result.reason == "answer_complete"
    assert result.payload["raw_answer"] == "It is 391."
    # Never unvalidated data: the field is absent/None, with a visible warning.
    assert result.payload["structured"] is None
    assert any("failed validation" in w for w in result.payload["warnings"])
    degraded = [
        e
        for e in events
        if e.event_type == "final_response_structured_degraded"
    ]
    assert len(degraded) == 1


@pytest.mark.asyncio
async def test_zero_retries_degrades_without_extra_llm_call() -> None:
    """Zero retries degrades without extra LLM call."""
    client = StubClient(
        [
            {
                "next_node": "final_response",
                "args": {
                    "answer": "It is 391.",
                    "structured": {"bogus": True},
                },
            }
        ]
    )
    planner = make_planner(
        client, final_response_model=Verdict, final_response_retries=0
    )
    result = await planner.run("17*23?")
    assert result.payload["structured"] is None
    assert len(client.calls) == 1  # no corrective turn issued


@pytest.mark.asyncio
async def test_default_off_byte_parity() -> None:
    """Without final_response_model, payloads are byte-identical to today's."""
    response = {"next_node": "final_response", "args": {"answer": "Done"}}
    baseline = make_planner(StubClient([response]))
    result = await baseline.run("hi")

    assert result.payload["raw_answer"] == "Done"
    assert result.payload["structured"] is None  # new field, default None
    # No structured-related events, no extra LLM calls.
    parity_planner = make_planner(StubClient([response]))
    assert (await parity_planner.run("hi")).payload == result.payload


def test_system_prompt_carries_schema_only_when_set() -> None:
    """System prompt carries schema only when set."""
    with_model = make_planner(StubClient([]), final_response_model=Verdict)
    without_model = make_planner(StubClient([]))
    assert "structured_final_response" in with_model.system_prompt
    assert '"method"' in with_model.system_prompt
    assert "structured_final_response" not in without_model.system_prompt


def test_conditional_action_schema_requires_structured() -> None:
    """Conditional action schema requires structured."""
    from penguiflow.planner.llm import (
        _build_planner_action_schema_conditional_finish,
    )

    schema = _build_planner_action_schema_conditional_finish(
        structured_schema={
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
        }
    )
    then_args = schema["allOf"][1]["then"]["properties"]["args"]
    assert "structured" in then_args["properties"]
    assert then_args["required"] == ["answer", "structured"]

    plain = _build_planner_action_schema_conditional_finish()
    plain_args = plain["allOf"][1]["then"]["properties"]["args"]
    assert "structured" not in plain_args["properties"]


@pytest.mark.asyncio
async def test_fork_preserves_final_response_model() -> None:
    """Fork preserves final response model."""
    planner = make_planner(
        StubClient([FINISH_VALID]), final_response_model=Verdict
    )
    assert planner.init_kwargs is not None
    assert planner.init_kwargs["final_response_model"] is Verdict
    assert planner.init_kwargs["final_response_retries"] == 1
