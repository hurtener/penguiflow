"""Tests for LLM error recovery functionality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.planner.compress import (
    _estimate_trajectory_size,
    _is_large_observation,
    _summarise_single_observation,
    compress_trajectory,
)
from penguiflow.planner.error_recovery import (
    ErrorRecoveryConfig,
    _create_graceful_failure_action,
    _emit_recovery_event,
    _extract_user_friendly_error,
    step_with_recovery,
)
from penguiflow.planner.llm import LLMErrorType, classify_llm_error
from penguiflow.planner.models import PlannerAction
from penguiflow.planner.trajectory import Trajectory, TrajectoryStep

# --- ErrorRecoveryConfig tests ---


def test_error_recovery_config_defaults():
    """Test ErrorRecoveryConfig has expected defaults."""
    config = ErrorRecoveryConfig()
    assert config.enabled is True
    assert config.max_compress_retries == 1
    assert config.compression_threshold_chars == 2000
    assert config.summarize_on_compress is True


def test_error_recovery_config_custom_values():
    """Test ErrorRecoveryConfig accepts custom values."""
    config = ErrorRecoveryConfig(
        enabled=False,
        max_compress_retries=3,
        compression_threshold_chars=5000,
        summarize_on_compress=False,
    )
    assert config.enabled is False
    assert config.max_compress_retries == 3
    assert config.compression_threshold_chars == 5000
    assert config.summarize_on_compress is False


# --- classify_llm_error tests ---


def test_classify_llm_error_context_length():
    """Test classifying context length errors."""
    test_cases = [
        "input is too long for this model",
        "context length exceeded",
        "maximum context reached",
        "token limit exceeded",
        "context_length_exceeded error",
    ]
    for msg in test_cases:
        exc = Exception(msg)
        assert classify_llm_error(exc) == LLMErrorType.CONTEXT_LENGTH_EXCEEDED, f"Failed for: {msg}"


def test_classify_llm_error_rate_limit():
    """Test classifying rate limit errors."""

    class RateLimitError(Exception):
        pass

    exc = RateLimitError("too many requests")
    assert classify_llm_error(exc) == LLMErrorType.RATE_LIMIT


def test_classify_llm_error_service_unavailable():
    """Test classifying service unavailable errors."""

    class ServiceUnavailableError(Exception):
        pass

    exc = ServiceUnavailableError("service down")
    assert classify_llm_error(exc) == LLMErrorType.SERVICE_UNAVAILABLE


def test_classify_llm_error_bad_request():
    """Test classifying bad request errors."""

    class BadRequestError(Exception):
        pass

    exc = BadRequestError("invalid parameters")
    assert classify_llm_error(exc) == LLMErrorType.BAD_REQUEST_OTHER


def test_classify_llm_error_unknown():
    """Test classifying unknown errors."""
    exc = Exception("something completely different")
    assert classify_llm_error(exc) == LLMErrorType.UNKNOWN


# --- _extract_user_friendly_error tests ---


def test_extract_user_friendly_error_nested_json():
    """Test extracting message from nested JSON error."""
    exc = Exception(
        'litellm.BadRequestError: DatabricksException - '
        '{"error_code":"BAD_REQUEST","message":"{\\"message\\":\\"Input is too long.\\"}"}'
    )
    msg = _extract_user_friendly_error(exc)
    assert "Input is too long" in msg


def test_extract_user_friendly_error_simple_json():
    """Test extracting message from simple JSON error."""
    exc = Exception(
        'litellm.BadRequestError: DatabricksException - '
        '{"error_code":"BAD_REQUEST","message":"Invalid request format"}'
    )
    msg = _extract_user_friendly_error(exc)
    assert "Invalid request format" in msg


def test_extract_user_friendly_error_plain_text():
    """Test extracting message from plain text error."""
    exc = Exception("Something went wrong")
    msg = _extract_user_friendly_error(exc)
    assert msg == "Something went wrong"


def test_extract_user_friendly_error_truncates_long_messages():
    """Test that long error messages are truncated."""
    long_msg = "x" * 1000
    exc = Exception(long_msg)
    msg = _extract_user_friendly_error(exc)
    assert len(msg) <= 500


# --- _create_graceful_failure_action tests ---


def test_create_graceful_failure_action():
    """Test creating graceful failure action."""
    exc = Exception("Test error message")
    action = _create_graceful_failure_action(exc, LLMErrorType.BAD_REQUEST_OTHER)

    assert action.next_node == "final_response"  # finish action
    assert "raw_answer" in action.args
    assert "answer" in action.args
    assert "sorry" in action.args["raw_answer"].lower()
    assert "_recovery" in action.args
    assert action.args["_recovery"]["error_type"] == "bad_request_other"


def test_create_graceful_failure_action_context_length():
    """Test creating graceful failure action for context length error."""
    exc = Exception("Context too long")
    action = _create_graceful_failure_action(exc, LLMErrorType.CONTEXT_LENGTH_EXCEEDED)

    assert action.next_node == "final_response"
    assert action.args["_recovery"]["error_type"] == "context_length_exceeded"


# --- _emit_recovery_event tests ---


def test_emit_recovery_event_with_callback():
    """Test that recovery events are emitted when callback is set."""
    planner = MagicMock()
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.456)

    trajectory = Trajectory(query="test")

    _emit_recovery_event(
        planner,
        trajectory,
        "test_event",
        key1="value1",
    )

    planner._emit_event.assert_called_once()
    event = planner._emit_event.call_args[0][0]
    assert event.event_type == "test_event"
    assert event.extra["key1"] == "value1"


def test_emit_recovery_event_no_callback():
    """Test that no event is emitted when callback is None."""
    planner = MagicMock()
    planner._event_callback = None

    trajectory = Trajectory(query="test")

    _emit_recovery_event(planner, trajectory, "test_event")

    planner._emit_event.assert_not_called()


# --- Compression utilities tests ---


def test_is_large_observation_small():
    """Test that small observations are not flagged."""
    assert _is_large_observation("small") is False
    assert _is_large_observation({"key": "value"}) is False


def test_is_large_observation_large():
    """Test that large observations are flagged."""
    large_data = {"data": "x" * 3000}
    assert _is_large_observation(large_data, threshold=2000) is True


def test_is_large_observation_none():
    """Test that None observations are not flagged."""
    assert _is_large_observation(None) is False


def test_estimate_trajectory_size():
    """Test trajectory size estimation."""
    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"data": "x" * 100},
        )
    )

    size = _estimate_trajectory_size(trajectory)
    assert size > 100  # Should include observation


def test_estimate_trajectory_size_empty():
    """Test trajectory size estimation for empty trajectory."""
    trajectory = Trajectory(query="test")
    size = _estimate_trajectory_size(trajectory)
    assert size == 0


# --- _summarise_single_observation tests ---


@pytest.mark.asyncio
async def test_summarise_single_observation():
    """Test observation summarization."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Summarized content", 0.01)

    result = await _summarise_single_observation(
        mock_client,
        "test_tool",
        {"large": "data" * 100},
    )

    assert result == "Summarized content"
    mock_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_summarise_single_observation_dict_response():
    """Test observation summarization with dict response."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = ({"content": "Dict summary"}, 0.01)

    result = await _summarise_single_observation(
        mock_client,
        "test_tool",
        {"data": "value"},
    )

    assert result == "Dict summary"


@pytest.mark.asyncio
async def test_summarise_single_observation_error_fallback():
    """Test observation summarization fallback on error."""
    mock_client = AsyncMock()
    mock_client.complete.side_effect = Exception("API Error")

    result = await _summarise_single_observation(
        mock_client,
        "test_tool",
        {"data": "value"},
    )

    assert "[Summary unavailable]" in result


# --- compress_trajectory tests ---


@pytest.mark.asyncio
async def test_compress_trajectory_no_large_observations():
    """Test compression when no observations are large."""
    planner = MagicMock()
    planner._summarizer_client = None
    planner._client = AsyncMock()

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"small": "data"},
        )
    )

    compressed, result = await compress_trajectory(planner, trajectory)

    assert result.compressed is False
    assert result.steps_compressed == 0


@pytest.mark.asyncio
async def test_compress_trajectory_with_large_observations():
    """Test compression when observations are large."""
    planner = MagicMock()
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Compressed summary", 0.01)
    planner._summarizer_client = mock_client
    planner._client = None

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"large": "x" * 3000},
        )
    )

    compressed, result = await compress_trajectory(planner, trajectory, threshold=100)

    assert result.compressed is True
    assert result.steps_compressed == 1
    assert compressed.steps[0].llm_observation["_compressed"] is True
    assert "summary" in compressed.steps[0].llm_observation


@pytest.mark.asyncio
async def test_compress_trajectory_no_client():
    """Test compression raises error when no client available."""
    planner = MagicMock()
    planner._summarizer_client = None
    planner._client = None

    trajectory = Trajectory(query="test")

    with pytest.raises(ValueError, match="No LLM client available"):
        await compress_trajectory(planner, trajectory)


# --- step_with_recovery tests ---


@pytest.mark.asyncio
async def test_step_with_recovery_success():
    """Test step_with_recovery passes through on success."""
    planner = MagicMock()
    expected_action = PlannerAction(thought="success", next_node="final_response")
    planner.step = AsyncMock(return_value=expected_action)

    trajectory = Trajectory(query="test")

    result = await step_with_recovery(planner, trajectory)

    assert result == expected_action
    planner.step.assert_called_once()


@pytest.mark.asyncio
async def test_step_with_recovery_disabled():
    """Test step_with_recovery passes through when disabled."""
    planner = MagicMock()
    expected_action = PlannerAction(thought="success", next_node="final_response")
    planner.step = AsyncMock(return_value=expected_action)

    trajectory = Trajectory(query="test")
    config = ErrorRecoveryConfig(enabled=False)

    result = await step_with_recovery(planner, trajectory, config=config)

    assert result == expected_action


@pytest.mark.asyncio
async def test_step_with_recovery_bad_request_graceful_failure():
    """Test step_with_recovery handles bad request with graceful failure."""

    class BadRequestError(Exception):
        pass

    planner = MagicMock()
    planner.step = AsyncMock(side_effect=BadRequestError("Invalid request"))
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    trajectory = Trajectory(query="test")

    result = await step_with_recovery(planner, trajectory)

    assert result.next_node == "final_response"  # finish action
    assert "sorry" in result.args["raw_answer"].lower()


@pytest.mark.asyncio
async def test_step_with_recovery_unknown_error_reraises():
    """Test step_with_recovery re-raises unknown errors."""
    planner = MagicMock()
    planner.step = AsyncMock(side_effect=ValueError("Unknown error"))
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    trajectory = Trajectory(query="test")

    with pytest.raises(ValueError, match="Unknown error"):
        await step_with_recovery(planner, trajectory)


@pytest.mark.asyncio
async def test_step_with_recovery_rate_limit_reraises():
    """Test step_with_recovery re-raises rate limit errors for backoff handling."""

    class RateLimitError(Exception):
        pass

    planner = MagicMock()
    planner.step = AsyncMock(side_effect=RateLimitError("Rate limited"))
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    trajectory = Trajectory(query="test")

    with pytest.raises(RateLimitError):
        await step_with_recovery(planner, trajectory)


@pytest.mark.asyncio
async def test_step_with_recovery_context_length_compresses_and_retries():
    """Test step_with_recovery compresses and retries on context length error."""
    planner = MagicMock()

    # First call raises context length error, second succeeds
    expected_action = PlannerAction(thought="success after compression", next_node="final_response")
    planner.step = AsyncMock(
        side_effect=[
            Exception("input is too long for this model"),
            expected_action,
        ]
    )
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    # Mock summarizer client for compression
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Compressed", 0.01)
    planner._summarizer_client = mock_client

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"large": "x" * 3000},
        )
    )

    config = ErrorRecoveryConfig(compression_threshold_chars=100)

    result = await step_with_recovery(planner, trajectory, config=config)

    assert result == expected_action
    assert planner.step.call_count == 2


@pytest.mark.asyncio
async def test_step_with_recovery_context_length_max_retries_exceeded():
    """Test step_with_recovery gives up after max compression retries."""
    planner = MagicMock()

    # Always raises context length error
    planner.step = AsyncMock(side_effect=Exception("input is too long for this model"))
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    # Mock summarizer client
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Compressed", 0.01)
    planner._summarizer_client = mock_client

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"large": "x" * 3000},
        )
    )

    config = ErrorRecoveryConfig(max_compress_retries=1, compression_threshold_chars=100)

    result = await step_with_recovery(planner, trajectory, config=config)

    # Should return graceful failure after max retries
    assert result.next_node == "final_response"
    assert "_recovery" in result.args


@pytest.mark.asyncio
async def test_step_with_recovery_compression_failure():
    """Test step_with_recovery handles compression failure gracefully."""
    planner = MagicMock()
    planner.step = AsyncMock(side_effect=Exception("input is too long for this model"))
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    # Mock summarizer client that fails
    mock_client = AsyncMock()
    mock_client.complete.side_effect = Exception("Summarization failed")
    planner._summarizer_client = mock_client

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"large": "x" * 3000},
        )
    )

    config = ErrorRecoveryConfig(compression_threshold_chars=100)

    result = await step_with_recovery(planner, trajectory, config=config)

    # Should return graceful failure when compression fails
    assert result.next_node == "final_response"
    assert "_recovery" in result.args


@pytest.mark.asyncio
async def test_step_with_recovery_compression_ineffective():
    """Test step_with_recovery when compression doesn't help (nothing to compress)."""
    planner = MagicMock()
    planner.step = AsyncMock(side_effect=Exception("input is too long for this model"))
    planner._event_callback = MagicMock()
    planner._time_source = MagicMock(return_value=123.0)

    # Mock summarizer client
    mock_client = AsyncMock()
    planner._summarizer_client = mock_client

    # Trajectory with small observations (nothing to compress)
    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"small": "data"},
        )
    )

    config = ErrorRecoveryConfig(compression_threshold_chars=10000)  # High threshold

    result = await step_with_recovery(planner, trajectory, config=config)

    # Should return graceful failure when nothing to compress
    assert result.next_node == "final_response"
    assert "_recovery" in result.args


def test_extract_user_friendly_error_with_inner_json_string():
    """Test extracting message when inner message is a JSON string starting with {."""
    exc = Exception(
        'DatabricksException - {"error_code":"BAD_REQUEST","message":"{\\"message\\":\\"Nested error\\"}"}'
    )
    msg = _extract_user_friendly_error(exc)
    assert "Nested error" in msg


def test_extract_user_friendly_error_json_decode_error():
    """Test extracting message when JSON is malformed."""
    exc = Exception('DatabricksException - {invalid json}')
    msg = _extract_user_friendly_error(exc)
    # Should fall back to regex or plain text
    assert len(msg) > 0


def test_extract_user_friendly_error_regex_fallback():
    """Test extracting message using regex fallback."""
    # Error with message pattern but not DatabricksException format
    exc = Exception('Some error with "message": "The actual error"')
    msg = _extract_user_friendly_error(exc)
    assert "actual error" in msg.lower() or "message" in msg.lower()


def test_extract_user_friendly_error_double_nested_regex():
    """Test extracting double-nested message via regex."""
    exc = Exception('"message": "{\\"message\\": \\"Deep nested\\"}"')
    msg = _extract_user_friendly_error(exc)
    # Should extract the innermost message
    assert len(msg) > 0


# --- Additional compress.py coverage tests ---


def test_is_large_observation_non_serializable():
    """Test _is_large_observation with non-serializable object falls back to str."""
    class NonSerializable:
        def __str__(self):
            return "x" * 3000

    obj = NonSerializable()
    # Should use str() fallback and detect as large
    assert _is_large_observation(obj, threshold=100) is True


def test_estimate_trajectory_size_non_serializable_observation():
    """Test _estimate_trajectory_size with non-serializable observation."""
    class NonSerializable:
        def __str__(self):
            return "fallback string"

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation=NonSerializable(),
        )
    )

    size = _estimate_trajectory_size(trajectory)
    assert size > 0  # Should use str() fallback


@pytest.mark.asyncio
async def test_summarise_single_observation_object_with_content_attr():
    """Test summarization when response has content attribute."""
    mock_client = AsyncMock()

    class ResponseWithContent:
        content = "Content from attribute"

    mock_client.complete.return_value = (ResponseWithContent(), 0.01)

    result = await _summarise_single_observation(
        mock_client,
        "test_tool",
        {"data": "value"},
    )

    assert result == "Content from attribute"


@pytest.mark.asyncio
async def test_summarise_single_observation_direct_response():
    """Test summarization when response is returned directly (not tuple)."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = "Direct string response"

    result = await _summarise_single_observation(
        mock_client,
        "test_tool",
        {"data": "value"},
    )

    assert result == "Direct string response"


@pytest.mark.asyncio
async def test_summarise_single_observation_truncates_large_input():
    """Test that very large observations are truncated before summarization."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Summary", 0.01)

    # Very large observation
    large_obs = {"data": "x" * 10000}

    await _summarise_single_observation(
        mock_client,
        "test_tool",
        large_obs,
    )

    # Check that the message was truncated
    call_args = mock_client.complete.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
    user_msg = messages[1]["content"]
    assert "truncated" in user_msg


@pytest.mark.asyncio
async def test_summarise_single_observation_non_serializable():
    """Test summarization with non-JSON-serializable observation."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Summary", 0.01)

    class CustomObj:
        def __str__(self):
            return "custom object string"

    result = await _summarise_single_observation(
        mock_client,
        "test_tool",
        CustomObj(),
    )

    assert result == "Summary"


@pytest.mark.asyncio
async def test_compress_trajectory_uses_main_client_fallback():
    """Test compression falls back to main client when summarizer not available."""
    planner = MagicMock()
    planner._summarizer_client = None

    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Compressed via main client", 0.01)
    planner._client = mock_client

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="test", next_node="tool"),
            llm_observation={"large": "x" * 3000},
        )
    )

    compressed, result = await compress_trajectory(planner, trajectory, threshold=100)

    assert result.compressed is True
    mock_client.complete.assert_called()


@pytest.mark.asyncio
async def test_compress_trajectory_finish_action():
    """Test compression handles finish actions (next_node=final_response)."""
    planner = MagicMock()
    mock_client = AsyncMock()
    mock_client.complete.return_value = ("Summary", 0.01)
    planner._summarizer_client = mock_client

    trajectory = Trajectory(query="test")
    trajectory.steps.append(
        TrajectoryStep(
            action=PlannerAction(thought="finish", next_node="final_response"),  # Finish action
            llm_observation={"large": "x" * 3000},
        )
    )

    compressed, result = await compress_trajectory(planner, trajectory, threshold=100)

    assert result.compressed is True
    # Should use "unknown_tool" for finish actions
    assert compressed.steps[0].llm_observation["_compressed"] is True
