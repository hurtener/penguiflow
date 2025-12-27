"""Tests for penguiflow/planner/models.py edge cases."""


from penguiflow.planner.models import JoinInjection, ObservationGuardrailConfig, PlannerEvent

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
