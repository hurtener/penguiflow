"""Tests for parallel execution edge cases (planner/parallel.py)."""

from __future__ import annotations

from pydantic import BaseModel

from penguiflow.planner.models import JoinInjection, ParallelCall, ParallelJoin, PlannerAction
from penguiflow.planner.parallel import _BranchExecutionResult


class EchoOut(BaseModel):
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
            inject=JoinInjection(mapping={"results": "$results", "expect": "$expect"}),
        )
        assert join.inject is not None
        assert join.inject.mapping["results"] == "$results"

    def test_join_injection_shorthand(self) -> None:
        """Test JoinInjection accepts shorthand without 'mapping' wrapper."""
        # Shorthand: {"field": "$source"}
        injection = JoinInjection.model_validate({"results": "$results"})
        assert injection.mapping["results"] == "$results"

    def test_join_injection_full_form(self) -> None:
        """Test JoinInjection with full 'mapping' wrapper."""
        injection = JoinInjection.model_validate({"mapping": {"results": "$results"}})
        assert injection.mapping["results"] == "$results"


class TestPlannerActionParallel:
    """Tests for PlannerAction with parallel plans."""

    def test_action_with_parallel_plan(self) -> None:
        """Test PlannerAction with parallel plan."""
        action = PlannerAction(
            thought="Fetching data in parallel",
            plan=[
                ParallelCall(node="fetch1", args={"id": 1}),
                ParallelCall(node="fetch2", args={"id": 2}),
            ],
        )
        assert action.plan is not None
        assert len(action.plan) == 2
        assert action.next_node is None

    def test_action_with_plan_and_join(self) -> None:
        """Test PlannerAction with plan and join."""
        action = PlannerAction(
            thought="Parallel with join",
            plan=[
                ParallelCall(node="fetch", args={"id": 1}),
            ],
            join=ParallelJoin(
                node="merge",
                args={},
                inject=JoinInjection(mapping={"results": "$results"}),
            ),
        )
        assert action.plan is not None
        assert action.join is not None
        assert action.join.node == "merge"

    def test_action_with_next_node_and_plan_invalid(self) -> None:
        """Test that action can technically have both, but this is invalid at runtime."""
        # The model allows this, but execute_parallel_plan will reject it
        action = PlannerAction(
            thought="Invalid combo",
            plan=[ParallelCall(node="fetch", args={})],
            next_node="other_node",  # This is invalid with plan
        )
        # Model validates, but runtime will error
        assert action.plan is not None
        assert action.next_node is not None
