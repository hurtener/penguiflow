"""LLM error recovery system for ReactPlanner.

This module provides automatic recovery strategies for LLM-related errors,
including context length compression and graceful failure handling.

Recovery strategies by error type:
- CONTEXT_LENGTH_EXCEEDED: Auto-compress trajectory, retry
- RATE_LIMIT / SERVICE_UNAVAILABLE: Handled by existing exponential backoff
- BAD_REQUEST_OTHER: Inject error as observation, let LLM apologize
- UNKNOWN: Re-raise original error
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from penguiflow.planner.compress import compress_trajectory
from penguiflow.planner.llm import LLMErrorType, classify_llm_error
from penguiflow.planner.models import PlannerAction, PlannerEvent

if TYPE_CHECKING:
    from penguiflow.planner.trajectory import Trajectory

logger = logging.getLogger(__name__)


@dataclass
class ErrorRecoveryConfig:
    """Configuration for LLM error recovery."""

    enabled: bool = True
    max_compress_retries: int = 1
    compression_threshold_chars: int = 2000
    summarize_on_compress: bool = True


def _extract_user_friendly_error(exc: Exception) -> str:
    """Extract a clean, user-readable error message from a nested exception.

    Args:
        exc: The exception to extract a message from.

    Returns:
        A clean error message suitable for user display.
    """
    import json as json_module

    error_str = str(exc)

    # Try to extract from litellm format first
    litellm_match = re.search(r"DatabricksException - (.+)", error_str)
    if litellm_match:
        json_payload = litellm_match.group(1)
        try:
            data = json_module.loads(json_payload)
            if isinstance(data, dict) and "message" in data:
                inner_msg = data["message"]
                # Handle double-nested JSON
                if isinstance(inner_msg, str) and inner_msg.startswith("{"):
                    try:
                        inner_data = json_module.loads(inner_msg)
                        if isinstance(inner_data, dict) and "message" in inner_data:
                            return str(inner_data["message"])
                    except json_module.JSONDecodeError:
                        pass
                return str(inner_msg)
        except json_module.JSONDecodeError:
            pass

    # Try to extract nested JSON message from Databricks-style errors
    # e.g., {"error_code":"BAD_REQUEST","message":"{\"message\":\"Input is too long.\"}"}
    json_match = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', error_str)
    if json_match:
        inner_msg = json_match.group(1)
        # Unescape the string
        inner_msg = inner_msg.replace('\\"', '"').replace("\\\\", "\\")
        # Check for double-nested JSON message
        inner_json = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', inner_msg)
        if inner_json:
            return inner_json.group(1).replace('\\"', '"').replace("\\\\", "\\")
        return inner_msg

    # Fall back to cleaned exception string
    # Remove class name prefixes like "litellm.BadRequestError:"
    cleaned = re.sub(r"^\w+\.\w+:\s*", "", error_str)
    return cleaned[:500] if len(cleaned) > 500 else cleaned


async def step_with_recovery(
    planner: Any,
    trajectory: Trajectory,
    *,
    config: ErrorRecoveryConfig | None = None,
) -> PlannerAction:
    """Execute a planner step with automatic error recovery.

    This wrapper catches LLM errors and applies appropriate recovery strategies:
    - For context length errors: compress trajectory and retry
    - For other bad request errors: return a graceful failure action

    Args:
        planner: The ReactPlanner instance.
        trajectory: The current trajectory.
        config: Recovery configuration (uses defaults if not provided).

    Returns:
        The next PlannerAction (either from successful LLM call or recovery).

    Raises:
        Exception: Re-raised if error is unrecoverable.
    """
    if config is None:
        config = ErrorRecoveryConfig()

    if not config.enabled:
        return await planner.step(trajectory)

    compress_attempts = 0

    while True:
        try:
            return await planner.step(trajectory)

        except Exception as exc:
            error_type = classify_llm_error(exc)

            _emit_recovery_event(
                planner,
                trajectory,
                "error_recovery_attempt",
                error_type=error_type.value,
                error_message=str(exc)[:500],
                compress_attempt=compress_attempts,
            )

            if error_type == LLMErrorType.CONTEXT_LENGTH_EXCEEDED:
                if compress_attempts >= config.max_compress_retries:
                    logger.warning(
                        "error_recovery_exhausted",
                        extra={
                            "error_type": error_type.value,
                            "compress_attempts": compress_attempts,
                        },
                    )
                    _emit_recovery_event(
                        planner,
                        trajectory,
                        "error_recovery_failed",
                        error_type=error_type.value,
                        reason="max_compress_retries_exceeded",
                    )
                    # Fall through to graceful failure
                    return _create_graceful_failure_action(exc, error_type)

                # Compress trajectory and retry
                compress_attempts += 1
                logger.info(
                    "error_recovery_compressing",
                    extra={
                        "attempt": compress_attempts,
                        "trajectory_steps": len(trajectory.steps),
                    },
                )

                try:
                    compressed_trajectory, result = await compress_trajectory(
                        planner,
                        trajectory,
                        threshold=config.compression_threshold_chars,
                    )
                except Exception as compress_exc:
                    logger.warning(
                        "compression_failed",
                        extra={"error": str(compress_exc)[:200]},
                    )
                    _emit_recovery_event(
                        planner,
                        trajectory,
                        "error_recovery_failed",
                        error_type=error_type.value,
                        reason="compression_failed",
                        compression_error=str(compress_exc)[:200],
                    )
                    return _create_graceful_failure_action(exc, error_type)

                if result.compressed:
                    _emit_recovery_event(
                        planner,
                        trajectory,
                        "trajectory_compressed",
                        attempt=compress_attempts,
                        reason="context_length",
                        steps_compressed=result.steps_compressed,
                        original_size_chars=result.original_size_chars,
                        compressed_size_chars=result.compressed_size_chars,
                    )

                    # Update trajectory steps in-place with compressed data
                    for i, step in enumerate(compressed_trajectory.steps):
                        if i < len(trajectory.steps):
                            trajectory.steps[i].llm_observation = step.llm_observation

                    # Retry with compressed trajectory
                    continue

                # Compression didn't help (nothing to compress)
                logger.warning(
                    "compression_ineffective",
                    extra={"steps_compressed": 0},
                )
                return _create_graceful_failure_action(exc, error_type)

            elif error_type == LLMErrorType.BAD_REQUEST_OTHER:
                logger.info(
                    "error_recovery_graceful_failure",
                    extra={"error_type": error_type.value},
                )
                _emit_recovery_event(
                    planner,
                    trajectory,
                    "error_recovery_success",
                    error_type=error_type.value,
                    strategy="graceful_failure",
                )
                return _create_graceful_failure_action(exc, error_type)

            elif error_type in (
                LLMErrorType.RATE_LIMIT,
                LLMErrorType.SERVICE_UNAVAILABLE,
            ):
                # These should be handled by existing retry logic in the client
                # Re-raise so the client's backoff mechanisms can work
                logger.debug(
                    "error_recovery_passthrough",
                    extra={"error_type": error_type.value},
                )
                raise

            else:  # UNKNOWN
                logger.warning(
                    "error_recovery_unknown_error",
                    extra={
                        "error_type": error_type.value,
                        "exception_class": exc.__class__.__name__,
                    },
                )
                raise


def _create_graceful_failure_action(
    exc: Exception,
    error_type: LLMErrorType,
) -> PlannerAction:
    """Create a graceful failure action that lets the LLM apologize.

    Args:
        exc: The original exception.
        error_type: The classified error type.

    Returns:
        A PlannerAction with finish signal and error context.
    """
    user_msg = _extract_user_friendly_error(exc)

    thought = (
        f"I encountered an error while processing your request: {user_msg}. "
        "I apologize for the inconvenience."
    )

    return PlannerAction(
        thought=thought,
        next_node=None,
        args={
            "raw_answer": (
                f"I'm sorry, but I encountered an issue while processing your request: "
                f"{user_msg}. Please try again or rephrase your question."
            ),
            "_recovery": {
                "error_type": error_type.value,
                "original_error": str(exc)[:1000],
            },
        },
    )


def _emit_recovery_event(
    planner: Any,
    trajectory: Trajectory,
    event_type: str,
    **extra: Any,
) -> None:
    """Emit a recovery-related event for observability.

    Args:
        planner: The ReactPlanner instance.
        trajectory: The current trajectory.
        event_type: The type of recovery event.
        **extra: Additional event data.
    """
    if planner._event_callback is None:
        return

    planner._emit_event(
        PlannerEvent(
            event_type=event_type,
            ts=planner._time_source(),
            trajectory_step=len(trajectory.steps),
            extra=extra,
        )
    )


__all__ = [
    "ErrorRecoveryConfig",
    "step_with_recovery",
]
