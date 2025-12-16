"""Tests for penguiflow/planner/prompts.py edge cases."""


from penguiflow.planner import prompts


def test_render_observation_empty():
    """render_observation with empty data should default observation to None."""
    result = prompts.render_observation(observation=None, error=None, failure=None)
    assert '"observation": null' in result


def test_render_observation_with_observation():
    """render_observation should include observation when provided."""
    result = prompts.render_observation(observation={"data": "test"}, error=None, failure=None)
    assert '"observation"' in result
    assert '"data"' in result


def test_render_observation_with_error():
    """render_observation should include error when provided."""
    result = prompts.render_observation(observation=None, error="Something failed", failure=None)
    assert '"error"' in result
    assert "Something failed" in result


def test_render_observation_with_failure():
    """render_observation should include failure when provided."""
    result = prompts.render_observation(
        observation=None,
        error=None,
        failure={"traceback": "stack trace"},
    )
    assert '"failure"' in result


def test_render_validation_error():
    """render_validation_error should format validation message."""
    result = prompts.render_validation_error("test_node", "field 'x' is required")
    assert "test_node" in result
    assert "did not validate" in result
    assert "field 'x' is required" in result
    assert "corrected JSON" in result


def test_render_output_validation_error():
    """render_output_validation_error should format output validation message."""
    result = prompts.render_output_validation_error("test_node", "missing field")
    assert "test_node" in result
    assert "returned data that did not validate" in result


def test_render_invalid_node():
    """render_invalid_node should list available options."""
    result = prompts.render_invalid_node("unknown_tool", ["tool_a", "tool_b", "tool_c"])
    assert "unknown_tool" in result
    assert "not in the catalog" in result
    assert "tool_a" in result
    assert "tool_b" in result


def test_render_invalid_join_injection_source():
    """render_invalid_join_injection_source should show available sources."""
    result = prompts.render_invalid_join_injection_source(
        "$unknown",
        ["$results", "$expect", "$branches"],
    )
    assert "$unknown" in result
    assert "unknown source" in result
    assert "$results" in result


def test_render_join_validation_error_without_suggest():
    """render_join_validation_error without suggestion."""
    result = prompts.render_join_validation_error(
        "join_node",
        "field missing",
        suggest_inject=False,
    )
    assert "join_node" in result
    assert "did not validate" in result
    assert "inject" not in result.lower()


def test_render_join_validation_error_with_suggest():
    """render_join_validation_error with injection suggestion."""
    result = prompts.render_join_validation_error(
        "join_node",
        "field missing",
        suggest_inject=True,
    )
    assert "join_node" in result
    assert "join.inject" in result


def test_render_hop_budget_violation():
    """render_hop_budget_violation should include limit."""
    result = prompts.render_hop_budget_violation(10)
    assert "budget exhausted" in result.lower()
    assert "limit=10" in result


def test_render_deadline_exhausted():
    """render_deadline_exhausted should indicate deadline reached."""
    result = prompts.render_deadline_exhausted()
    assert "deadline" in result.lower()


def test_render_repair_message():
    """render_repair_message should include error details."""
    result = prompts.render_repair_message("invalid syntax")
    assert "invalid JSON" in result
    assert "invalid syntax" in result
    assert "corrected JSON" in result


def test_render_empty_parallel_plan():
    """render_empty_parallel_plan should indicate empty plan error."""
    result = prompts.render_empty_parallel_plan()
    assert "parallel" in result.lower()
    assert "empty" in result.lower() or "plan" in result.lower()


def test_render_parallel_with_next_node():
    """render_parallel_with_next_node should indicate conflict."""
    result = prompts.render_parallel_with_next_node("conflicting_node")
    assert "conflicting_node" in result
    assert "parallel" in result.lower()


def test_render_parallel_setup_error():
    """render_parallel_setup_error should list all errors."""
    errors = ["Error 1", "Error 2"]
    result = prompts.render_parallel_setup_error(errors)
    assert "Error 1" in result
    assert "Error 2" in result


def test_render_parallel_unknown_failure():
    """render_parallel_unknown_failure should indicate unknown failure."""
    result = prompts.render_parallel_unknown_failure("branch_node")
    assert "branch_node" in result
    assert "failed" in result.lower() or "error" in result.lower()
