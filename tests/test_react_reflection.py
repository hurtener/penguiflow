"""Tests covering the reflection loop for the ReactPlanner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.react import ReflectionConfig, ReflectionCritique
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    question: str


class SearchResult(BaseModel):
    documents: list[str]


@tool(desc="Search knowledge base")
async def search(args: Query, ctx: object) -> SearchResult:  # pragma: no cover
    return SearchResult(documents=["Doc A about parallel", "Doc B about errors"])


class ReflectionStubClient:
    """Stubbed JSON LLM client returning pre-configured responses."""

    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]
        self.calls: list[list[Mapping[str, str]]] = []

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
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


@pytest.mark.asyncio()
async def test_reflection_improves_incomplete_answer() -> None:
    """Reflection should detect incomplete answers and request a revision."""

    main_client = ReflectionStubClient(
        [
            {
                "thought": "Need to search for parallel execution",
                "next_node": "search",
                "args": {"question": "parallel execution"},
            },
            {
                "thought": "Found info about parallel",
                "next_node": None,
                "args": {"raw_answer": "PenguiFlow uses asyncio.gather"},
            },
            {
                "thought": "Adding error recovery details",
                "next_node": None,
                "args": {
                    "raw_answer": (
                        "PenguiFlow uses asyncio.gather for parallel execution "
                        "with exponential backoff for error recovery"
                    ),
                },
            },
        ]
    )

    reflection_client = ReflectionStubClient(
        [
            {
                "score": 0.5,
                "passed": False,
                "feedback": (
                    "Answer only covers parallel execution, missing error "
                    "recovery"
                ),
                "issues": ["No mention of error handling"],
                "suggestions": [
                    "Add information about error recovery mechanism",
                ],
            },
            {
                "score": 0.95,
                "passed": True,
                "feedback": (
                    "Answer now covers both parallel execution and error "
                    "recovery"
                ),
                "issues": [],
                "suggestions": [],
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    nodes = [Node(search, name="search")]
    catalog = build_catalog(nodes, registry)

    planner = ReactPlanner(
        llm_client=main_client,
        catalog=catalog,
        reflection_config=ReflectionConfig(
            enabled=True,
            quality_threshold=0.8,
            max_revisions=2,
            use_separate_llm=True,
        ),
        reflection_llm="stub-model",
    )
    planner._reflection_client = reflection_client

    result = await planner.run("Explain parallel execution with error recovery")

    assert result.reason == "answer_complete"
    assert "reflection" in result.metadata

    reflection_meta = result.metadata["reflection"]
    assert reflection_meta["score"] >= 0.8
    assert reflection_meta["revisions"] == 1
    assert reflection_meta["passed"] is True
    assert "error recovery" in result.payload["raw_answer"].lower()


@pytest.mark.asyncio()
async def test_reflection_stops_after_max_revisions() -> None:
    """Reflection should stop after the configured number of revisions."""

    main_client = ReflectionStubClient(
        [
            {
                "thought": "Search",
                "next_node": "search",
                "args": {"question": "test"},
            },
            {
                "thought": "Answer",
                "next_node": None,
                "args": {"raw_answer": "Bad answer"},
            },
            {
                "thought": "Revised",
                "next_node": None,
                "args": {"raw_answer": "Still bad"},
            },
            {
                "thought": "Revised again",
                "next_node": None,
                "args": {"raw_answer": "Still not good"},
            },
            # Clarification response after max revisions exceeded
            {
                "text": "Unable to provide satisfactory answer",
                "confidence": "unsatisfied",
                "attempted_approaches": ["search"],
                "clarifying_questions": ["What exactly are you looking for?"],
                "suggestions": ["More context needed"],
                "reflection_score": 0.5,
                "revision_attempts": 2,
            },
        ]
    )

    reflection_client = ReflectionStubClient(
        [
            {
                "score": 0.3,
                "passed": False,
                "feedback": "Bad",
                "issues": [],
                "suggestions": [],
            },
            {
                "score": 0.4,
                "passed": False,
                "feedback": "Still bad",
                "issues": [],
                "suggestions": [],
            },
            {
                "score": 0.5,
                "passed": False,
                "feedback": "Still not good",
                "issues": [],
                "suggestions": [],
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    planner = ReactPlanner(
        llm_client=main_client,
        catalog=build_catalog([Node(search, name="search")], registry),
        reflection_config=ReflectionConfig(
            enabled=True,
            quality_threshold=0.8,
            max_revisions=2,
            use_separate_llm=True,
        ),
        reflection_llm="stub-model",
    )
    planner._reflection_client = reflection_client

    result = await planner.run("Test")

    assert result.reason == "answer_complete"
    reflection_meta = result.metadata["reflection"]
    assert reflection_meta["revisions"] == 2
    assert reflection_meta["score"] < 0.8
    assert reflection_meta["passed"] is False


@pytest.mark.asyncio()
async def test_reflection_disabled_by_default() -> None:
    """Reflection behaviour must be opt-in."""

    client = ReflectionStubClient(
        [
            {
                "thought": "Search",
                "next_node": "search",
                "args": {"question": "test"},
            },
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "Result"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(search, name="search")], registry),
    )

    result = await planner.run("Test")

    assert result.reason == "answer_complete"
    assert "reflection" not in result.metadata


@pytest.mark.asyncio()
async def test_reflection_respects_hop_budget() -> None:
    """Reflection loop should not exceed hop budgets."""

    client = ReflectionStubClient(
        [
            {
                "thought": "Search",
                "next_node": "search",
                "args": {"question": "test"},
            },
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "First"},
            },
            {
                "thought": "Revised",
                "next_node": None,
                "args": {"raw_answer": "Second"},
            },
        ]
    )

    reflection_client = ReflectionStubClient(
        [
            {
                "score": 0.3,
                "passed": False,
                "feedback": "Bad",
                "issues": [],
                "suggestions": [],
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(search, name="search")], registry),
        hop_budget=1,
        reflection_config=ReflectionConfig(
            enabled=True,
            max_revisions=5,
            use_separate_llm=True,
        ),
        reflection_llm="stub-model",
    )
    planner._reflection_client = reflection_client

    result = await planner.run("Test")

    constraints = result.metadata["constraints"]
    assert constraints["hop_exhausted"] is True
    assert constraints["hops_used"] == 1


@pytest.mark.asyncio()
async def test_reflection_event_emission() -> None:
    """Reflection should emit structured planner events."""

    events: list[Any] = []

    def event_callback(event: Any) -> None:
        events.append(event)

    client = ReflectionStubClient(
        [
            {
                "thought": "Search",
                "next_node": "search",
                "args": {"question": "test"},
            },
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "Answer"},
            },
        ]
    )

    reflection_client = ReflectionStubClient(
        [
            {
                "score": 0.9,
                "passed": True,
                "feedback": "Good",
                "issues": [],
                "suggestions": [],
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(search, name="search")], registry),
        reflection_config=ReflectionConfig(enabled=True, use_separate_llm=True),
        reflection_llm="stub-model",
        event_callback=event_callback,
    )
    planner._reflection_client = reflection_client

    await planner.run("Test")

    reflection_events = [
        event
        for event in events
        if getattr(event, "event_type", "") == "reflection_critique"
    ]
    assert reflection_events
    assert reflection_events[0].extra["score"] == 0.9
    assert reflection_events[0].extra["passed"] is True


def test_reflection_critique_model_enforces_bounds() -> None:
    """ReflectionCritique validates the provided score bounds."""

    with pytest.raises(ValueError):
        ReflectionCritique(score=1.5, passed=False, feedback="bad")

    critique = ReflectionCritique(score=0.7, passed=False, feedback="ok")
    assert critique.score == pytest.approx(0.7)


def test_reflection_config_defaults() -> None:
    """Reflection config defaults keep the feature disabled."""

    config = ReflectionConfig()
    assert config.enabled is False
    assert pytest.approx(config.quality_threshold) == 0.80
    assert config.max_revisions == 2


@pytest.mark.asyncio()
async def test_critique_uses_main_llm_when_shared_client() -> None:
    """When no separate LLM configured the main client handles critique calls."""

    critique_payload = {
        "score": 0.9,
        "passed": True,
        "feedback": "Great answer",
        "issues": [],
        "suggestions": [],
    }

    class CaptureClient(ReflectionStubClient):
        async def complete(
            self,
            *,
            messages: list[Mapping[str, str]],
            response_format: Mapping[str, object] | None = None,
            stream: bool = False,
            on_stream_chunk: object = None,
        ) -> str:
            del response_format, stream, on_stream_chunk
            self.calls.append(list(messages))
            if self._responses:
                return self._responses.pop(0)
            return json.dumps(critique_payload)

    client = CaptureClient(
        [
            {"thought": "Done", "next_node": None, "args": {"raw_answer": "Result"}},
        ]
    )

    registry = ModelRegistry()
    registry.register("search", Query, SearchResult)
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(search, name="search")], registry),
        reflection_config=ReflectionConfig(enabled=True),
    )

    await planner.run("Test")

    # First call is the planning action, second is the critique using same client
    assert len(client.calls) >= 2
    assert client.calls[-1][0]["role"] == "system"

