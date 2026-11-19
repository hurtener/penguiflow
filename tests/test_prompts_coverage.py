"""Tests for penguiflow/planner/prompts.py edge cases."""

from __future__ import annotations

from penguiflow.planner import prompts


def test_render_observation_empty() -> None:
    """render_observation defaults the observation to None."""
    result = prompts.render_observation(
        observation=None, error=None, failure=None
    )
    assert '"observation": null' in result


def test_render_observation_with_observation() -> None:
    """render_observation should include observation when provided."""
    result = prompts.render_observation(
        observation={"data": "test"}, error=None, failure=None
    )
    assert '"observation"' in result
    assert '"data"' in result


def test_render_observation_with_error() -> None:
    """render_observation should include error when provided."""
    result = prompts.render_observation(
        observation=None, error="Something failed", failure=None
    )
    assert '"error"' in result
    assert "Something failed" in result


def test_render_observation_with_failure() -> None:
    """render_observation should include failure when provided."""
    result = prompts.render_observation(
        observation=None,
        error=None,
        failure={"traceback": "stack trace"},
    )
    assert '"failure"' in result


def test_render_validation_error() -> None:
    """render_validation_error should format validation message."""
    result = prompts.render_validation_error(
        "test_node", "field 'x' is required"
    )
    assert "test_node" in result
    assert "did not validate" in result
    assert "field 'x' is required" in result
    assert "corrected JSON" in result


def test_render_output_validation_error() -> None:
    """render_output_validation_error formats the validation message."""
    result = prompts.render_output_validation_error(
        "test_node", "missing field"
    )
    assert "test_node" in result
    assert "returned data that did not validate" in result


def test_render_invalid_node() -> None:
    """render_invalid_node should list available options."""
    result = prompts.render_invalid_node(
        "unknown_tool", ["tool_a", "tool_b", "tool_c"]
    )
    assert "unknown_tool" in result
    assert "not in the catalog" in result
    assert "tool_a" in result
    assert "tool_b" in result


def test_render_invalid_join_injection_source() -> None:
    """render_invalid_join_injection_source should show available sources."""
    result = prompts.render_invalid_join_injection_source(
        "$unknown",
        ["$results", "$expect", "$branches"],
    )
    assert "$unknown" in result
    assert "unknown source" in result
    assert "$results" in result


def test_render_join_validation_error_without_suggest() -> None:
    """render_join_validation_error without suggestion."""
    result = prompts.render_join_validation_error(
        "join_node",
        "field missing",
        suggest_inject=False,
    )
    assert "join_node" in result
    assert "did not validate" in result
    assert "inject" not in result.lower()


def test_render_join_validation_error_with_suggest() -> None:
    """render_join_validation_error with injection suggestion."""
    result = prompts.render_join_validation_error(
        "join_node",
        "field missing",
        suggest_inject=True,
    )
    assert "join_node" in result
    assert "join.inject" in result


def test_render_hop_budget_violation() -> None:
    """render_hop_budget_violation should include limit."""
    result = prompts.render_hop_budget_violation(10)
    assert "budget exhausted" in result.lower()
    assert "limit=10" in result


def test_render_deadline_exhausted() -> None:
    """render_deadline_exhausted should indicate deadline reached."""
    result = prompts.render_deadline_exhausted()
    assert "deadline" in result.lower()


def test_render_repair_message() -> None:
    """render_repair_message should include error details."""
    result = prompts.render_repair_message("invalid syntax")
    assert "invalid JSON" in result
    assert "invalid syntax" in result
    assert "corrected JSON" in result


def test_render_empty_parallel_plan() -> None:
    """render_empty_parallel_plan should indicate empty plan error."""
    result = prompts.render_empty_parallel_plan()
    assert "parallel" in result.lower()
    assert "args.steps" in result


def test_render_parallel_with_next_node() -> None:
    """render_parallel_with_next_node should indicate conflict."""
    result = prompts.render_parallel_with_next_node("conflicting_node")
    assert "conflicting_node" in result
    assert "parallel" in result.lower()


def test_render_parallel_setup_error() -> None:
    """render_parallel_setup_error should list all errors."""
    errors = ["Error 1", "Error 2"]
    result = prompts.render_parallel_setup_error(errors)
    assert "Error 1" in result
    assert "Error 2" in result


def test_render_parallel_unknown_failure() -> None:
    """render_parallel_unknown_failure should indicate unknown failure."""
    result = prompts.render_parallel_unknown_failure("branch_node")
    assert "branch_node" in result
    assert "failed" in result.lower() or "error" in result.lower()
