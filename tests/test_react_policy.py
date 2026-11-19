"""Phase 4 policy filtering tests for ReactPlanner."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerFinish, ReactPlanner, ToolPolicy
from penguiflow.planner.models import JSONLLMClient
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class Query(BaseModel):
    """Query payload."""

    question: str


class Answer(BaseModel):
    """Answer payload."""

    answer: str


class StubClient(JSONLLMClient):
    """Deterministic LLM stub returning scripted planner actions."""

    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [json.dumps(item) for item in responses]
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
            msg = "No stub responses remaining"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


def _register(
    registry: ModelRegistry,
    *pairs: tuple[str, type[BaseModel], type[BaseModel]],
) -> None:
    for name, args_model, out_model in pairs:
        registry.register(name, args_model, out_model)


@pytest.mark.asyncio()
async def test_tool_policy_filters_catalog() -> None:
    """Tool policy filters catalog."""
    registry = ModelRegistry()
    _register(
        registry,
        ("tool_a", Query, Answer),
        ("tool_b", Query, Answer),
        ("tool_c", Query, Answer),
    )

    @tool(desc="Tool A", tags=["safe"])
    async def tool_a(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"A:{args.question}")

    @tool(desc="Tool B", tags=["safe", "expensive"])
    async def tool_b(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"B:{args.question}")

    @tool(desc="Tool C", tags=["unsafe"])
    async def tool_c(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"C:{args.question}")

    nodes = [
        Node(tool_a, name="tool_a"),
        Node(tool_b, name="tool_b"),
        Node(tool_c, name="tool_c"),
    ]
    catalog = build_catalog(nodes, registry)

    policy = ToolPolicy(allowed_tools={"tool_a", "tool_b"})
    client = StubClient(
        [
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "OK"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        tool_policy=policy,
    )

    assert set(planner.spec_by_name) == {"tool_a", "tool_b"}


@pytest.mark.asyncio()
async def test_tool_policy_denies_tools() -> None:
    """Tool policy denies tools."""
    registry = ModelRegistry()
    _register(
        registry, ("good_tool", Query, Answer), ("bad_tool", Query, Answer)
    )

    @tool(desc="Good tool", tags=["safe"])
    async def good_tool(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"good:{args.question}")

    @tool(desc="Bad tool", tags=["safe"])
    async def bad_tool(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"bad:{args.question}")

    catalog = build_catalog(
        [Node(good_tool, name="good_tool"), Node(bad_tool, name="bad_tool")],
        registry,
    )

    policy = ToolPolicy(
        allowed_tools={"good_tool", "bad_tool"},
        denied_tools={"bad_tool"},
    )
    client = StubClient(
        [
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "OK"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client, catalog=catalog, tool_policy=policy
    )

    assert set(planner.spec_by_name) == {"good_tool"}
    assert "bad_tool" not in planner.spec_by_name


@pytest.mark.asyncio()
async def test_tool_policy_requires_tags() -> None:
    """Tool policy requires tags."""
    registry = ModelRegistry()
    _register(
        registry, ("safe_tool", Query, Answer), ("unsafe_tool", Query, Answer)
    )

    @tool(desc="Safe tool", tags=["safe", "approved"])
    async def safe_tool(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"safe:{args.question}")

    @tool(desc="Unsafe tool", tags=["unsafe"])
    async def unsafe_tool(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"unsafe:{args.question}")

    catalog = build_catalog(
        [
            Node(safe_tool, name="safe_tool"),
            Node(unsafe_tool, name="unsafe_tool"),
        ],
        registry,
    )

    policy = ToolPolicy(require_tags={"safe"})
    client = StubClient(
        [
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "OK"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client, catalog=catalog, tool_policy=policy
    )

    assert set(planner.spec_by_name) == {"safe_tool"}
    assert "unsafe_tool" not in planner.spec_by_name


@pytest.mark.asyncio()
async def test_tool_policy_llm_error_on_forbidden_tool() -> None:
    """Tool policy LLM error on forbidden tool."""
    registry = ModelRegistry()
    _register(
        registry, ("allowed", Query, Answer), ("forbidden", Query, Answer)
    )

    @tool(desc="Allowed tool", tags=["safe"])
    async def allowed(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"allowed:{args.question}")

    @tool(desc="Forbidden tool", tags=["restricted"])
    async def forbidden(args: Query, _ctx: object) -> Answer:
        return Answer(answer=f"forbidden:{args.question}")

    catalog = build_catalog(
        [Node(allowed, name="allowed"), Node(forbidden, name="forbidden")],
        registry,
    )

    policy = ToolPolicy(allowed_tools={"allowed"})
    client = StubClient(
        [
            {
                "thought": "Try forbidden",
                "next_node": "forbidden",
                "args": {"question": "test"},
            },
            {
                "thought": "Use allowed",
                "next_node": "allowed",
                "args": {"question": "test"},
            },
            {
                "thought": "Done",
                "next_node": None,
                "args": {"raw_answer": "OK"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client, catalog=catalog, tool_policy=policy
    )

    result = await planner.run("Test forbidden tool")

    assert isinstance(result, PlannerFinish)
    errors = [step.get("error") for step in result.metadata["steps"]]
    assert any("forbidden" in (error or "") for error in errors)
    assert "allowed" in planner.spec_by_name
    assert "forbidden" not in planner.spec_by_name
