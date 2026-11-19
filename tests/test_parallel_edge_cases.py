"""Tests for parallel execution edge cases (planner/parallel.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Never

import pytest
from pydantic import BaseModel, model_validator
from typing_extensions import override

from penguiflow.catalog import build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ReactPlanner
from penguiflow.planner.constraints import _ConstraintTracker
from penguiflow.planner.models import (
    JoinInjection,
    JSONLLMClient,
    ParallelCall,
    ParallelJoin,
    PlannerAction,
)
from penguiflow.planner.parallel import (
    _BranchExecutionResult,
    execute_parallel_plan,
)
from penguiflow.planner.trajectory import Trajectory
from penguiflow.registry import ModelRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from penguiflow.planner import PlannerEvent
    from penguiflow.planner.context import ToolContext


class EchoOut(BaseModel):
    """Output of the echo tool."""

    echoed: str


class TestBranchExecutionResult:
    """Tests for _BranchExecutionResult dataclass."""

    def test_default_values(self) -> None:
        """Test default values for branch execution result."""
        result = _BranchExecutionResult()
        assert result.observation is None
        assert result.error is None
        assert result.failure is None
        assert result.pause is None

    def test_with_observation(self) -> None:
        """Test result with observation."""
        obs = EchoOut(echoed="test")
        result = _BranchExecutionResult(observation=obs)
        assert result.observation == obs
        assert result.error is None

    def test_with_error(self) -> None:
        """Test result with error."""
        result = _BranchExecutionResult(
            error="Tool failed",
            failure={"node": "failing", "error": "RuntimeError"},
        )
        assert result.error == "Tool failed"
        assert result.failure is not None

    def test_with_pause(self) -> None:
        """Test result with pause."""
        from penguiflow.planner.models import PlannerPause

        pause = PlannerPause(
            reason="approval_required",  # PlannerPauseReason is a Literal type
            payload={"action": "delete"},
            resume_token="token-123",
        )
        result = _BranchExecutionResult(pause=pause)
        assert result.pause == pause


class TestParallelModels:
    """Tests for parallel execution models."""

    def test_parallel_call_basic(self) -> None:
        """Test ParallelCall model creation."""
        call = ParallelCall(node="fetch", args={"query": "test"})
        assert call.node == "fetch"
        assert call.args == {"query": "test"}

    def test_parallel_call_default_args(self) -> None:
        """Test ParallelCall with default empty args."""
        call = ParallelCall(node="fetch")
        assert call.node == "fetch"
        assert call.args == {}

    def test_parallel_join_basic(self) -> None:
        """Test ParallelJoin model creation."""
        join = ParallelJoin(
            node="merge",
            args={"results": []},
        )
        assert join.node == "merge"
        assert join.args == {"results": []}
        assert join.inject is None

    def test_parallel_join_with_injection(self) -> None:
        """Test ParallelJoin with explicit injection."""
        join = ParallelJoin(
            node="merge",
            args={},
            inject=JoinInjection(
                mapping={"results": "$results", "expect": "$expect"}
            ),
        )
        assert join.inject is not None
        assert join.inject.mapping["results"] == "$results"

    def test_join_injection_shorthand(self) -> None:
        """Test JoinInjection accepts shorthand without 'mapping' wrapper."""
        # Shorthand form: field name mapped straight to the source.
        injection = JoinInjection.model_validate({"results": "$results"})
        assert injection.mapping["results"] == "$results"

    def test_join_injection_full_form(self) -> None:
        """Test JoinInjection with full 'mapping' wrapper."""
        injection = JoinInjection.model_validate(
            {"mapping": {"results": "$results"}}
        )
        assert injection.mapping["results"] == "$results"


class TestPlannerActionParallel:
    """Tests for PlannerAction with unified parallel opcode."""

    def test_action_with_parallel_plan(self) -> None:
        """Test PlannerAction with parallel plan."""
        action = PlannerAction(
            next_node="parallel",
            args={
                "steps": [
                    {"node": "fetch1", "args": {"id": 1}},
                    {"node": "fetch2", "args": {"id": 2}},
                ]
            },
            thought="Fetching data in parallel",
        )
        assert action.next_node == "parallel"
        assert len(action.args["steps"]) == 2

    def test_action_with_plan_and_join(self) -> None:
        """Test PlannerAction with plan and join."""
        action = PlannerAction(
            next_node="parallel",
            args={
                "steps": [{"node": "fetch", "args": {"id": 1}}],
                "join": {
                    "node": "merge",
                    "args": {},
                    "inject": {"mapping": {"results": "$results"}},
                },
            },
            thought="Parallel with join",
        )
        assert action.args["join"]["node"] == "merge"

    def test_action_with_next_node_and_plan_invalid(self) -> None:
        """Test that non-parallel next_node does not imply a parallel plan."""
        action = PlannerAction(
            next_node="other_node",
            args={},
            thought="Invalid combo",
        )
        assert action.next_node == "other_node"


class TestParallelExecutionErrors:
    """Targeted tests for execute_parallel_plan error branches."""

    @pytest.mark.asyncio
    async def test_rejects_non_parallel_next_node(self) -> None:
        """Rejects non parallel next node."""
        planner, _events = _make_planner()
        action = PlannerAction(next_node="echo", args={}, thought="bad")
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert observation is None
        assert pause is None
        assert trajectory.steps
        assert trajectory.steps[-1].error is not None

    @pytest.mark.asyncio
    async def test_rejects_empty_steps(self) -> None:
        """Rejects empty steps."""
        planner, _events = _make_planner()
        action = PlannerAction(
            next_node="parallel", args={"steps": []}, thought="bad"
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert observation is None
        assert pause is None
        assert trajectory.steps
        assert trajectory.steps[-1].error is not None

    @pytest.mark.asyncio
    async def test_rejects_plan_with_only_invalid_step_payloads(self) -> None:
        """Rejects plan with only invalid step payloads."""
        planner, _events = _make_planner()
        action = PlannerAction(
            next_node="parallel",
            args={"steps": [{"node": 123}]},
            thought="bad",
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert observation is None
        assert pause is None
        assert trajectory.steps
        assert trajectory.steps[-1].error is not None

    @pytest.mark.asyncio
    async def test_setup_error_for_unknown_tool_node(self) -> None:
        """Setup error for unknown tool node."""
        planner, _events = _make_planner()
        action = PlannerAction(
            next_node="parallel",
            args={"steps": [{"node": "missing_tool", "args": {}}]},
            thought="bad",
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert observation is None
        assert pause is None
        assert trajectory.steps
        assert trajectory.steps[-1].error is not None

    @pytest.mark.asyncio
    async def test_setup_error_handles_non_serializable_validator_context(
        self,
    ) -> None:
        """Setup error handles non serializable validator context."""

        class BadArgs(BaseModel):
            mode: str = "research"
            stream: bool = False

            @model_validator(mode="after")
            def validate_mode(self) -> BadArgs:
                if self.mode == "research" and not self.stream:
                    msg = "research requires stream=true"
                    raise ValueError(msg)
                return self

        class BadOut(BaseModel):
            ok: bool = True

        async def bad_tool(_args: BadArgs, _ctx: object) -> dict[str, bool]:
            return {"ok": True}

        planner, _events = _make_planner(
            ("bad_tool", BadArgs, BadOut, bad_tool)
        )
        action = PlannerAction(
            next_node="parallel",
            args={"steps": [{"node": "bad_tool", "args": {}}]},
            thought="bad",
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert observation is None
        assert pause is None
        assert trajectory.steps[-1].error is not None
        assert "research requires stream=true" in trajectory.steps[-1].error


class _NoopLLMClient(JSONLLMClient):
    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str:
        return '{"thought": "noop", "next_node": null}'


_ToolDef = tuple[
    str, type[BaseModel], type[BaseModel], Callable[..., Awaitable[object]]
]
"""A tool for ``_make_planner``: name, args model, output model, function."""


def _make_tracker() -> _ConstraintTracker:
    return _ConstraintTracker(
        deadline_s=None, hop_budget=None, time_source=lambda: 0.0
    )


def _make_planner(
    *nodes: _ToolDef,
) -> tuple[ReactPlanner, list[PlannerEvent]]:
    registry = ModelRegistry()
    node_specs = []
    for node_name, args_model, out_model, fn in nodes:
        wrapped = tool(desc=f"Tool {node_name}")(fn)
        registry.register(node_name, args_model, out_model)
        node_specs.append(Node(wrapped, name=node_name))

    catalog = build_catalog(node_specs, registry)
    events: list[PlannerEvent] = []

    planner = ReactPlanner(
        llm_client=_NoopLLMClient(),
        catalog=catalog,
        max_iters=1,
        event_callback=events.append,
    )
    return planner, events


class TestParallelJoinEdgeCases:
    """Covers join error handling and pause paths in execute_parallel_plan."""

    @pytest.mark.asyncio
    async def test_join_injection_invalid_source_creates_join_error(
        self,
    ) -> None:
        """Join injection invalid source creates join error."""

        class EchoArgs(BaseModel):
            text: str

        class JoinArgs(BaseModel):
            pass

        class JoinOut(BaseModel):
            ok: bool = True

        async def echo(args: EchoArgs, _ctx: object) -> dict[str, str]:
            return {"echoed": args.text}

        async def join_tool(_args: JoinArgs, _ctx: object) -> dict[str, bool]:
            return {"ok": True}

        planner, _events = _make_planner(
            ("echo", EchoArgs, EchoOut, echo),
            ("join_tool", JoinArgs, JoinOut, join_tool),
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        action = PlannerAction(
            next_node="parallel",
            args={
                "steps": [{"node": "echo", "args": {"text": "hello"}}],
                "join": {
                    "node": "join_tool",
                    "args": {},
                    "inject": {"mapping": {"results": "$bogus"}},
                },
            },
            thought="bad inject",
        )

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert pause is None
        assert observation is not None
        assert isinstance(observation, dict)
        assert "join" in observation
        assert observation["join"]["node"] == "join_tool"
        assert observation["join"].get("error")

    @pytest.mark.asyncio
    async def test_join_validation_error_when_required_field_missing(
        self,
    ) -> None:
        """Join validation error when required field missing."""

        class EchoArgs(BaseModel):
            text: str

        class JoinArgs(BaseModel):
            foo: int

        class JoinOut(BaseModel):
            ok: bool = True

        async def echo(args: EchoArgs, _ctx: object) -> dict[str, str]:
            return {"echoed": args.text}

        async def join_requires_foo(
            _args: JoinArgs, _ctx: object
        ) -> dict[str, bool]:
            return {"ok": True}

        planner, _events = _make_planner(
            ("echo", EchoArgs, EchoOut, echo),
            ("join_requires_foo", JoinArgs, JoinOut, join_requires_foo),
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        action = PlannerAction(
            next_node="parallel",
            args={
                "steps": [{"node": "echo", "args": {"text": "hello"}}],
                "join": {"node": "join_requires_foo", "args": {}},
            },
            thought="missing foo",
        )

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert pause is None
        assert observation is not None
        assert observation["join"]["node"] == "join_requires_foo"
        assert observation["join"].get("error")

    @pytest.mark.asyncio
    async def test_join_skipped_when_branch_failure_exists(self) -> None:
        """Join skipped when branch failure exists."""

        class EchoArgs(BaseModel):
            text: str

        class JoinArgs(BaseModel):
            pass

        class JoinOut(BaseModel):
            ok: bool = True

        async def ok(args: EchoArgs, _ctx: object) -> dict[str, str]:
            return {"echoed": args.text}

        async def fail(_args: EchoArgs, _ctx: object) -> Never:
            msg = "boom"
            raise RuntimeError(msg)

        async def join_tool(_args: JoinArgs, _ctx: object) -> dict[str, bool]:
            return {"ok": True}

        planner, _events = _make_planner(
            ("ok", EchoArgs, EchoOut, ok),
            ("fail", EchoArgs, EchoOut, fail),
            ("join_tool", JoinArgs, JoinOut, join_tool),
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        action = PlannerAction(
            next_node="parallel",
            args={
                "steps": [
                    {"node": "ok", "args": {"text": "hello"}},
                    {"node": "fail", "args": {"text": "nope"}},
                ],
                "join": {"node": "join_tool", "args": {}},
            },
            thought="branch failure",
        )

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert pause is None
        assert observation is not None
        assert observation["join"]["status"] == "skipped"
        assert observation["join"]["reason"] == "branch_failures"

    @pytest.mark.asyncio
    async def test_join_pause_short_circuits_parallel_plan(self) -> None:
        """Join pause short circuits parallel plan."""

        class EchoArgs(BaseModel):
            text: str

        class JoinArgs(BaseModel):
            pass

        class JoinOut(BaseModel):
            ok: bool = True

        async def echo(args: EchoArgs, _ctx: object) -> dict[str, str]:
            return {"echoed": args.text}

        async def join_pause(
            _args: JoinArgs, ctx: ToolContext
        ) -> dict[str, bool]:
            await ctx.pause("await_input", {"reason": "need confirmation"})
            return {"ok": True}

        planner, _events = _make_planner(
            ("echo", EchoArgs, EchoOut, echo),
            ("join_pause", JoinArgs, JoinOut, join_pause),
        )
        trajectory = Trajectory(query="test")
        tracker = _make_tracker()

        action = PlannerAction(
            next_node="parallel",
            args={
                "steps": [{"node": "echo", "args": {"text": "hello"}}],
                "join": {"node": "join_pause", "args": {}},
            },
            thought="pause",
        )

        observation, pause = await execute_parallel_plan(
            planner, action, trajectory, tracker, action_seq=1
        )

        assert pause is not None
        assert pause.reason == "await_input"
        assert observation is not None
        assert observation["join"]["pause"]["reason"] == "await_input"
