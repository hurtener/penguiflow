"""Tests for penguiflow/planner/models.py edge cases."""

from penguiflow.planner.models import (
    RESERVED_NEXT_NODES,
    SPECIAL_NODE_TYPES,
    ActionFormat,
    ActionWithReasoning,
    JoinInjection,
    ObservationGuardrailConfig,
    PlannerAction,
    PlannerEvent,
)

# ─── RFC_UNIFIED_ACTION_SCHEMA: PlannerAction computed properties ─────────────


def test_planner_action_is_terminal():
    """is_terminal should return True only for final_response."""
    assert PlannerAction(next_node="final_response", args={}).is_terminal() is True
    assert PlannerAction(next_node="some_tool", args={}).is_terminal() is False
    assert PlannerAction(next_node="parallel", args={}).is_terminal() is False


def test_planner_action_is_parallel():
    """is_parallel should return True only for parallel actions."""
    assert PlannerAction(next_node="parallel", args={}).is_parallel() is True
    assert PlannerAction(next_node="some_tool", args={}).is_parallel() is False
    assert PlannerAction(next_node="final_response", args={}).is_parallel() is False


def test_planner_action_is_background_task():
    """is_background_task should return True for task.subagent and task.tool."""
    assert PlannerAction(next_node="task.subagent", args={}).is_background_task() is True
    assert PlannerAction(next_node="task.tool", args={}).is_background_task() is True
    assert PlannerAction(next_node="some_tool", args={}).is_background_task() is False
    assert PlannerAction(next_node="parallel", args={}).is_background_task() is False


def test_planner_action_is_tool_call():
    """is_tool_call should return True for regular tool names, False for special nodes."""
    assert PlannerAction(next_node="search_documents", args={}).is_tool_call() is True
    assert PlannerAction(next_node="my_custom_tool", args={}).is_tool_call() is True
    assert PlannerAction(next_node="final_response", args={}).is_tool_call() is False
    assert PlannerAction(next_node="parallel", args={}).is_tool_call() is False
    assert PlannerAction(next_node="task.subagent", args={}).is_tool_call() is False
    assert PlannerAction(next_node="task.tool", args={}).is_tool_call() is False


def test_planner_action_get_answer():
    """get_answer should extract answer from final_response args."""
    action = PlannerAction(
        next_node="final_response",
        args={"answer": "The answer is 42."},
    )
    assert action.get_answer() == "The answer is 42."

    # Non-terminal actions return None
    tool_action = PlannerAction(next_node="some_tool", args={"query": "test"})
    assert tool_action.get_answer() is None

    # Missing answer field returns None
    empty_final = PlannerAction(next_node="final_response", args={})
    assert empty_final.get_answer() is None


def test_planner_action_get_plan_steps():
    """get_plan_steps should extract steps from parallel args."""
    action = PlannerAction(
        next_node="parallel",
        args={
            "steps": [
                {"node": "tool_a", "args": {"x": 1}},
                {"node": "tool_b", "args": {"y": 2}},
            ]
        },
    )
    steps = action.get_plan_steps()
    assert len(steps) == 2
    assert steps[0]["node"] == "tool_a"
    assert steps[1]["node"] == "tool_b"

    # Non-parallel actions return None
    tool_action = PlannerAction(next_node="some_tool", args={})
    assert tool_action.get_plan_steps() is None


def test_planner_action_get_plan_join():
    """get_plan_join should extract join config from parallel args."""
    action = PlannerAction(
        next_node="parallel",
        args={
            "steps": [{"node": "tool_a", "args": {}}],
            "join": {"node": "aggregator", "args": {}, "inject": {"results": "$results"}},
        },
    )
    join = action.get_plan_join()
    assert join is not None
    assert join["node"] == "aggregator"
    assert join["inject"]["results"] == "$results"

    # Parallel without join returns None
    no_join = PlannerAction(next_node="parallel", args={"steps": []})
    assert no_join.get_plan_join() is None


# ─── RFC_UNIFIED_ACTION_SCHEMA: SPECIAL_NODE_TYPES constant ───────────────────


def test_special_node_types_contains_expected_values():
    """SPECIAL_NODE_TYPES should contain all reserved opcodes."""
    assert "parallel" in SPECIAL_NODE_TYPES
    assert "task.subagent" in SPECIAL_NODE_TYPES
    assert "task.tool" in SPECIAL_NODE_TYPES
    assert "final_response" in SPECIAL_NODE_TYPES


def test_reserved_next_nodes_is_alias():
    """RESERVED_NEXT_NODES should be an alias for SPECIAL_NODE_TYPES."""
    assert RESERVED_NEXT_NODES is SPECIAL_NODE_TYPES


# ─── RFC_UNIFIED_ACTION_SCHEMA: ActionFormat config ───────────────────────────


def test_action_format_values():
    """ActionFormat should have expected string values."""
    assert ActionFormat.UNIFIED == "unified"
    assert ActionFormat.LEGACY == "legacy"
    assert ActionFormat.AUTO == "auto"


# ─── RFC_UNIFIED_ACTION_SCHEMA: ActionWithReasoning dataclass ─────────────────


def test_action_with_reasoning_basic():
    """ActionWithReasoning should wrap action with optional reasoning."""
    action = PlannerAction(next_node="some_tool", args={"query": "test"})
    wrapped = ActionWithReasoning(action=action, reasoning="Let me think...")
    assert wrapped.action is action
    assert wrapped.reasoning == "Let me think..."
    assert wrapped.reasoning_tokens is None


def test_action_with_reasoning_with_tokens():
    """ActionWithReasoning should track reasoning token count."""
    action = PlannerAction(next_node="final_response", args={"answer": "Done"})
    wrapped = ActionWithReasoning(action=action, reasoning="Analysis...", reasoning_tokens=150)
    assert wrapped.reasoning_tokens == 150


def test_action_with_reasoning_from_llm_response():
    """from_llm_response should extract reasoning from LiteLLM response structure."""

    # Mock LiteLLM response with reasoning_content
    class MockMessage:
        content = '{"next_node": "final_response", "args": {"answer": "42"}}'
        reasoning_content = "Let me calculate..."

    class MockChoice:
        message = MockMessage()

    class MockResponse:
        choices = [MockChoice()]

    action = PlannerAction(next_node="final_response", args={"answer": "42"})
    wrapped = ActionWithReasoning.from_llm_response(MockResponse(), action)

    assert wrapped.action is action
    assert wrapped.reasoning == "Let me calculate..."

# ─── PlannerEvent tests ──────────────────────────────────────────────────────


def test_planner_event_to_payload_with_token_estimate():
    """to_payload should include token_estimate when set."""
    event = PlannerEvent(
        event_type="plan_step",
        ts=1234567890.0,
        trajectory_step=1,
        token_estimate=500,
    )
    result = event.to_payload()
    assert result["token_estimate"] == 500


def test_planner_event_to_payload_with_error():
    """to_payload should include error when set."""
    event = PlannerEvent(
        event_type="plan_error",
        ts=1234567890.0,
        trajectory_step=1,
        error="Something failed",
    )
    result = event.to_payload()
    assert result["error"] == "Something failed"


def test_planner_event_to_payload_basic():
    """to_payload should include basic fields."""
    event = PlannerEvent(
        event_type="plan_start",
        ts=1234567890.0,
        trajectory_step=0,
        thought="Planning action",
        node_name="tool_node",
        latency_ms=100.5,
    )
    result = event.to_payload()

    assert result["event"] == "plan_start"
    assert result["ts"] == 1234567890.0
    assert result["thought"] == "Planning action"
    assert result["node_name"] == "tool_node"
    assert result["latency_ms"] == 100.5


def test_planner_event_to_payload_with_extra():
    """to_payload should include extra fields."""
    event = PlannerEvent(
        event_type="plan_step",
        ts=1234567890.0,
        trajectory_step=1,
        extra={"custom_field": "custom_value"},
    )
    result = event.to_payload()
    assert result["custom_field"] == "custom_value"


def test_planner_event_to_payload_filters_reserved_keys():
    """to_payload should filter reserved log keys from extra."""
    event = PlannerEvent(
        event_type="plan_step",
        ts=1234567890.0,
        trajectory_step=1,
        extra={"message": "should_be_filtered", "allowed": "should_appear"},
    )
    result = event.to_payload()
    assert "message" not in result  # Reserved key filtered
    assert result["allowed"] == "should_appear"


# ─── JoinInjection tests ─────────────────────────────────────────────────────


def test_join_injection_direct_mapping():
    """JoinInjection should accept direct mapping dict."""
    injection = JoinInjection(mapping={"field1": "$results", "field2": "$branches"})
    assert injection.mapping["field1"] == "$results"
    assert injection.mapping["field2"] == "$branches"


def test_join_injection_shorthand():
    """JoinInjection should allow shorthand without mapping wrapper."""
    injection = JoinInjection.model_validate({"field1": "$results"})
    assert injection.mapping["field1"] == "$results"


def test_join_injection_with_mapping_key():
    """JoinInjection should accept explicit mapping key."""
    injection = JoinInjection.model_validate(
        {"mapping": {"field1": "$results", "field2": "$expect"}}
    )
    assert injection.mapping["field1"] == "$results"
    assert injection.mapping["field2"] == "$expect"


def test_join_injection_empty():
    """JoinInjection should default to empty mapping."""
    injection = JoinInjection()
    assert injection.mapping == {}


# ─── ObservationGuardrailConfig tests ─────────────────────────────────────────


def test_observation_guardrail_config_defaults():
    """ObservationGuardrailConfig should have reasonable defaults."""
    config = ObservationGuardrailConfig()

    assert config.max_observation_chars == 50_000
    assert config.max_field_chars == 10_000
    assert config.preserve_structure is True
    assert config.auto_artifact_threshold == 20_000
    assert config.preview_length == 500
    assert "{truncated_chars}" in config.truncation_suffix


def test_observation_guardrail_config_custom_values():
    """ObservationGuardrailConfig should accept custom values."""
    config = ObservationGuardrailConfig(
        max_observation_chars=100_000,
        max_field_chars=20_000,
        preserve_structure=False,
        auto_artifact_threshold=50_000,
        preview_length=1000,
        truncation_suffix="... [TRUNCATED]",
    )

    assert config.max_observation_chars == 100_000
    assert config.max_field_chars == 20_000
    assert config.preserve_structure is False
    assert config.auto_artifact_threshold == 50_000
    assert config.preview_length == 1000
    assert config.truncation_suffix == "... [TRUNCATED]"


def test_observation_guardrail_config_min_values():
    """ObservationGuardrailConfig should enforce minimum values."""
    import pytest

    # max_observation_chars minimum is 1000
    with pytest.raises(ValueError):
        ObservationGuardrailConfig(max_observation_chars=500)

    # max_field_chars minimum is 100
    with pytest.raises(ValueError):
        ObservationGuardrailConfig(max_field_chars=50)


def test_observation_guardrail_config_disable_artifact_fallback():
    """ObservationGuardrailConfig should allow disabling artifact fallback."""
    config = ObservationGuardrailConfig(auto_artifact_threshold=0)
    assert config.auto_artifact_threshold == 0
