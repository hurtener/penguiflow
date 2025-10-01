from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import PlannerPause, ReactPlanner
from penguiflow.planner.react import Trajectory
from penguiflow.registry import ModelRegistry


class Query(BaseModel):
    question: str


class Intent(BaseModel):
    intent: str


class Documents(BaseModel):
    documents: list[str]


class Answer(BaseModel):
    answer: str


@tool(desc="Detect intent", tags=["nlp"])
async def triage(args: Query, ctx: object) -> Intent:
    return Intent(intent="docs")


@tool(desc="Search knowledge base", side_effects="read")
async def retrieve(args: Intent, ctx: object) -> Documents:
    return Documents(documents=[f"Answering about {args.intent}"])


@tool(desc="Compose final answer")
async def respond(args: Answer, ctx: object) -> Answer:
    return args


@tool(desc="Return invalid documents")
async def broken(args: Intent, ctx: object) -> Documents:  # type: ignore[return-type]
    return "boom"  # type: ignore[return-value]


@tool(desc="Approval required before proceeding")
async def approval_gate(args: Intent, ctx: Any) -> Intent:
    await ctx.pause("approval_required", {"intent": args.intent})
    return args


class PlannerTimeout(RuntimeError):
    def __init__(self, message: str, suggestion: str) -> None:
        super().__init__(message)
        self.suggestion = suggestion


@tool(desc="Remote fetch that may timeout", side_effects="external")
async def unstable(args: Intent, ctx: object) -> Documents:
    raise PlannerTimeout("upstream timeout", "use_cache")


@tool(desc="Use cached retrieval", side_effects="read")
async def cached(args: Intent, ctx: object) -> Documents:
    return Documents(documents=[f"Cached docs for {args.intent}"])


class StubClient:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0)


class SummarizerStub:
    def __init__(self) -> None:
        self.calls: list[list[Mapping[str, str]]] = []

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        self.calls.append(list(messages))
        return json.dumps(
            {
                "goals": ["stub"],
                "facts": {"note": "compact"},
                "pending": [],
                "last_output_digest": "stub",
                "note": "stub",
            }
        )


def make_planner(client: StubClient, **kwargs: object) -> ReactPlanner:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("retrieve", Intent, Documents)
    registry.register("respond", Answer, Answer)
    registry.register("broken", Intent, Documents)

    nodes = [
        Node(triage, name="triage"),
        Node(retrieve, name="retrieve"),
        Node(respond, name="respond"),
        Node(broken, name="broken"),
    ]
    catalog = build_catalog(nodes, registry)
    return ReactPlanner(llm_client=client, catalog=catalog, **kwargs)


@pytest.mark.asyncio()
async def test_react_planner_runs_end_to_end() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "What is PenguiFlow?"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "final",
                "next_node": None,
                "args": {"answer": "PenguiFlow is lightweight."},
            },
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Tell me about PenguiFlow")

    assert result.reason == "answer_complete"
    assert result.payload == {"answer": "PenguiFlow is lightweight."}
    assert result.metadata["step_count"] == 2


@pytest.mark.asyncio()
async def test_react_planner_recovers_from_invalid_node() -> None:
    client = StubClient(
        [
            {"thought": "invalid", "next_node": "missing", "args": {}},
            {"thought": "triage", "next_node": "triage", "args": {"question": "What?"}},
            {"thought": "finish", "next_node": None, "args": {"answer": "done"}},
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Test invalid node")

    assert result.reason == "answer_complete"
    assert any("missing" in step["error"] for step in result.metadata["steps"])


@pytest.mark.asyncio()
async def test_react_planner_reports_validation_error() -> None:
    client = StubClient(
        [
            {"thought": "bad", "next_node": "retrieve", "args": {}},
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Q"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {"thought": "finish", "next_node": None, "args": {"answer": "ok"}},
        ]
    )
    planner = make_planner(client)

    result = await planner.run("Test validation path")

    errors = [step["error"] for step in result.metadata["steps"] if step["error"]]
    assert any("did not validate" in err for err in errors)


@pytest.mark.asyncio()
async def test_react_planner_reports_output_validation_error() -> None:
    client = StubClient(
        [
            {
                "thought": "broken",
                "next_node": "broken",
                "args": {"intent": "docs"},
            },
            {"thought": "finish", "next_node": None, "args": {"answer": "fallback"}},
        ]
    )
    registry = ModelRegistry()
    registry.register("broken", Intent, Documents)
    catalog = build_catalog([Node(broken, name="broken")], registry)
    planner = ReactPlanner(llm_client=client, catalog=catalog)

    result = await planner.run("Test output validation path")

    errors = [step["error"] for step in result.metadata["steps"] if step["error"]]
    assert any("returned data" in err for err in errors)


@pytest.mark.asyncio()
async def test_react_planner_replans_after_tool_failure() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Need docs"},
            },
            {
                "thought": "remote",
                "next_node": "unstable",
                "args": {"intent": "docs"},
            },
            {
                "thought": "fallback",
                "next_node": "cached",
                "args": {"intent": "docs"},
            },
            {
                "thought": "wrap",
                "next_node": "respond",
                "args": {"answer": "Using cached docs"},
            },
            {
                "thought": "final",
                "next_node": None,
                "args": {"answer": "Using cached docs"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("unstable", Intent, Documents)
    registry.register("cached", Intent, Documents)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(triage, name="triage"),
        Node(unstable, name="unstable"),
        Node(cached, name="cached"),
        Node(respond, name="respond"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        max_iters=5,
    )

    result = await planner.run("Fetch docs with fallback")

    assert result.reason == "answer_complete"
    failure_step = next(
        (step for step in result.metadata["steps"] if step.get("failure")),
        None,
    )
    assert failure_step is not None
    assert failure_step["failure"]["node"] == "unstable"

    failure_prompt = json.loads(client.calls[2][-1]["content"])
    assert failure_prompt["failure"]["suggestion"] == "use_cache"
    assert failure_prompt["failure"]["error_code"] == "PlannerTimeout"


def test_react_planner_requires_catalog_or_nodes() -> None:
    client = StubClient([])
    with pytest.raises(ValueError):
        ReactPlanner(llm_client=client)


def test_react_planner_requires_llm_or_client() -> None:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    nodes = [Node(triage, name="triage")]
    with pytest.raises(ValueError):
        ReactPlanner(nodes=nodes, registry=registry)


@pytest.mark.asyncio()
async def test_react_planner_iteration_limit_returns_no_path() -> None:
    client = StubClient(
        [
            {
                "thought": "loop",
                "next_node": "triage",
                "args": {"question": "still thinking"},
            }
        ]
    )
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog([Node(triage, name="triage")], registry),
        max_iters=1,
    )

    result = await planner.run("Explain")
    assert result.reason == "no_path"


@pytest.mark.asyncio()
async def test_react_planner_enforces_hop_budget_limits() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Budget"},
            },
            {
                "thought": "still need",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "retry",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
        ]
    )

    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("retrieve", Intent, Documents)

    nodes = [
        Node(triage, name="triage"),
        Node(retrieve, name="retrieve"),
    ]

    planner = ReactPlanner(
        llm_client=client,
        catalog=build_catalog(nodes, registry),
        hop_budget=1,
        max_iters=3,
    )

    result = await planner.run("Constrained plan")

    assert result.reason == "budget_exhausted"
    constraints = result.metadata["constraints"]
    assert constraints["hop_exhausted"] is True
    violation = json.loads(client.calls[2][-1]["content"])
    assert "Hop budget" in violation["error"]


@pytest.mark.asyncio()
async def test_react_planner_litellm_guard_raises_runtime_error() -> None:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    nodes = [Node(triage, name="triage")]
    planner = ReactPlanner(llm="dummy", nodes=nodes, registry=registry)
    trajectory = Trajectory(query="hi")
    with pytest.raises(RuntimeError) as exc:
        await planner.step(trajectory)
    assert "LiteLLM is not installed" in str(exc.value)


@pytest.mark.asyncio()
async def test_react_planner_step_repairs_invalid_action() -> None:
    client = StubClient(
        [
            "{}",
            {
                "thought": "recover",
                "next_node": "triage",
                "args": {"question": "fixed"},
            },
        ]
    )
    planner = make_planner(client)
    trajectory = Trajectory(query="recover")

    action = await planner.step(trajectory)
    assert action.next_node == "triage"
    assert len(client.calls) == 2
    repair_message = client.calls[1][-1]["content"]
    assert "invalid JSON" in repair_message


@pytest.mark.asyncio()
async def test_react_planner_compacts_history_when_budget_exceeded() -> None:
    long_answer = "PenguiFlow " * 30
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "What is the plan?"},
            },
            {
                "thought": "respond",
                "next_node": "respond",
                "args": {"answer": long_answer},
            },
            {"thought": "finish", "next_node": None, "args": {"answer": "done"}},
        ]
    )
    planner = make_planner(client, token_budget=180)

    result = await planner.run("Explain budget handling")

    assert result.reason == "answer_complete"
    assert any(
        msg["role"] == "system" and "Trajectory summary" in msg["content"]
        for msg in client.calls[1]
    )


@pytest.mark.asyncio()
async def test_react_planner_invokes_summarizer_client() -> None:
    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Summarise"},
            },
            {
                "thought": "respond",
                "next_node": "respond",
                "args": {"answer": "value"},
            },
            {"thought": "finish", "next_node": None, "args": {"answer": "ok"}},
        ]
    )
    planner = make_planner(client, token_budget=60)
    summarizer = SummarizerStub()
    planner._summarizer_client = summarizer  # type: ignore[attr-defined]

    await planner.run("Trigger summariser")

    assert summarizer.calls, "Expected summarizer to be invoked"


@pytest.mark.asyncio()
async def test_react_planner_pause_and_resume_flow() -> None:
    registry = ModelRegistry()
    registry.register("triage", Query, Intent)
    registry.register("approval", Intent, Intent)
    registry.register("retrieve", Intent, Documents)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(triage, name="triage"),
        Node(approval_gate, name="approval"),
        Node(retrieve, name="retrieve"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    client = StubClient(
        [
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Send report"},
            },
            {
                "thought": "approval",
                "next_node": "approval",
                "args": {"intent": "docs"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"answer": "Report sent"},
            },
        ]
    )
    planner = ReactPlanner(llm_client=client, catalog=catalog, pause_enabled=True)

    pause_result = await planner.run("Share metrics with approval")
    assert isinstance(pause_result, PlannerPause)
    assert pause_result.reason == "approval_required"

    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="approved",
    )
    assert resume_result.reason == "answer_complete"

    post_pause_calls = client.calls[2:]
    assert any(
        "Resume input" in msg["content"]
        for call in post_pause_calls
        for msg in call
    )


@pytest.mark.asyncio()
async def test_react_planner_resume_preserves_hop_budget() -> None:
    registry = ModelRegistry()
    registry.register("approval", Intent, Intent)
    registry.register("respond", Answer, Answer)

    nodes = [
        Node(approval_gate, name="approval"),
        Node(respond, name="respond"),
    ]
    catalog = build_catalog(nodes, registry)

    client = StubClient(
        [
            {
                "thought": "request approval",
                "next_node": "approval",
                "args": {"intent": "docs"},
            },
            {
                "thought": "follow up",
                "next_node": "respond",
                "args": {"answer": "Report"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"answer": "Report"},
            },
        ]
    )

    planner = ReactPlanner(
        llm_client=client,
        catalog=catalog,
        pause_enabled=True,
        hop_budget=1,
    )

    pause_result = await planner.run("Send report with approval")
    assert isinstance(pause_result, PlannerPause)
    assert pause_result.reason == "approval_required"

    resume_result = await planner.resume(
        pause_result.resume_token,
        user_input="approved",
    )
    assert resume_result.reason == "answer_complete"

    steps = resume_result.metadata["steps"]
    assert any(
        step.get("error") and "Hop budget" in step["error"]
        for step in steps
    ), "expected hop budget violation after resume"

    constraints = resume_result.metadata["constraints"]
    assert constraints["hops_used"] == 1
    assert constraints["hop_exhausted"] is True


@pytest.mark.asyncio()
async def test_react_planner_disallows_nodes_from_hints() -> None:
    client = StubClient(
        [
            {
                "thought": "bad",
                "next_node": "broken",
                "args": {"intent": "docs"},
            },
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Hi"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"answer": "done"},
            },
        ]
    )
    planner = make_planner(client, planning_hints={"disallow_nodes": ["broken"]})

    result = await planner.run("test hints")

    assert result.reason == "answer_complete"
    assert any(
        msg["role"] == "user" and "not permitted" in msg["content"]
        for msg in client.calls[1]
    )


@pytest.mark.asyncio()
async def test_react_planner_emits_ordering_hint_once() -> None:
    client = StubClient(
        [
            {
                "thought": "early",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "triage",
                "next_node": "triage",
                "args": {"question": "Order?"},
            },
            {
                "thought": "retrieve",
                "next_node": "retrieve",
                "args": {"intent": "docs"},
            },
            {
                "thought": "finish",
                "next_node": None,
                "args": {"answer": "done"},
            },
        ]
    )
    planner = make_planner(
        client,
        planning_hints={"ordering_hints": ["triage", "retrieve"]},
    )

    result = await planner.run("ordering")

    assert result.reason == "answer_complete"
    assert any(
        msg["role"] == "user" and "Ordering hint" in msg["content"]
        for msg in client.calls[1]
    )
