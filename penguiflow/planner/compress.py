"""Trajectory compression utilities for error recovery.

This module provides functions to compress trajectory observations when the
context becomes too large for the model's context window. Instead of failing,
we summarize individual tool outputs and retry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from penguiflow.planner.trajectory import Trajectory

logger = logging.getLogger(__name__)

# Default threshold in characters for compression candidates
DEFAULT_COMPRESSION_THRESHOLD = 2000

_OBSERVATION_SUMMARIZER_SYSTEM_PROMPT = """\
You are a concise summarizer. Given a tool observation (output from a function/tool call),
produce a brief summary that preserves the essential information needed for reasoning.

Focus on:
- Key data points and values
- Important results or findings
- Any errors or warnings
- Structure of the response (if relevant)

Be extremely concise. The summary should be much shorter than the original while
retaining the critical information needed to continue reasoning about the task."""

_OBSERVATION_SUMMARIZER_USER_TEMPLATE = """\
Tool: {tool_name}
Observation to summarize:
{observation}

Provide a concise summary (max 500 chars) that preserves the essential information."""


@dataclass
class CompressionResult:
    """Result of trajectory compression."""

    compressed: bool
    steps_compressed: int
    original_size_chars: int
    compressed_size_chars: int


def _is_large_observation(obs: Any, threshold: int = DEFAULT_COMPRESSION_THRESHOLD) -> bool:
    """Check if an observation exceeds the compression threshold.

    Args:
        obs: The observation to check (can be any JSON-serializable value)
        threshold: Character count threshold

    Returns:
        True if the observation is large enough to warrant compression
    """
    if obs is None:
        return False
    try:
        serialized = json.dumps(obs, default=str)
        return len(serialized) > threshold
    except (TypeError, ValueError):
        # If we can't serialize it, check string representation
        return len(str(obs)) > threshold


def _estimate_trajectory_size(trajectory: Trajectory) -> int:
    """Estimate the character size of a trajectory's LLM-visible content."""
    total = 0
    for step in trajectory.steps:
        if step.llm_observation is not None:
            try:
                total += len(json.dumps(step.llm_observation, default=str))
            except (TypeError, ValueError):
                total += len(str(step.llm_observation))
        if step.action:
            try:
                total += len(step.action.model_dump_json())
            except Exception:
                total += len(str(step.action))
    return total


async def _summarise_single_observation(
    client: Any,
    tool_name: str,
    observation: Any,
) -> str:
    """Summarize a single tool observation using an LLM.

    Args:
        client: LLM client with a complete() method
        tool_name: Name of the tool that produced this observation
        observation: The observation to summarize

    Returns:
        A concise summary string
    """
    # Serialize the observation for the prompt
    try:
        obs_str = json.dumps(observation, indent=2, default=str)
    except (TypeError, ValueError):
        obs_str = str(observation)

    # Truncate very large observations to avoid overwhelming the summarizer
    max_input_chars = 8000
    if len(obs_str) > max_input_chars:
        obs_str = obs_str[:max_input_chars] + "\n... [truncated for summarization]"

    messages = [
        {"role": "system", "content": _OBSERVATION_SUMMARIZER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _OBSERVATION_SUMMARIZER_USER_TEMPLATE.format(
                tool_name=tool_name,
                observation=obs_str,
            ),
        },
    ]

    try:
        result = await client.complete(messages=messages)
        # Handle both tuple (response, cost) and direct response formats
        if isinstance(result, tuple):
            response = result[0]
        else:
            response = result

        # Extract content from response
        if hasattr(response, "content"):
            return str(response.content).strip()
        if isinstance(response, dict) and "content" in response:
            return str(response["content"]).strip()
        return str(response).strip()
    except Exception as exc:
        logger.warning(
            "observation_summarization_failed",
            extra={"tool": tool_name, "error": str(exc)},
        )
        # Fallback: return a truncated version
        fallback = obs_str[:400] + "..." if len(obs_str) > 400 else obs_str
        return f"[Summary unavailable] {fallback}"


async def compress_trajectory(
    planner: Any,
    trajectory: Trajectory,
    threshold: int = DEFAULT_COMPRESSION_THRESHOLD,
) -> tuple[Trajectory, CompressionResult]:
    """Compress a trajectory by summarizing large observations.

    This creates a deep copy of the trajectory and replaces large `llm_observation`
    fields with compressed summaries. The original `observation` field is preserved
    for record-keeping.

    Args:
        planner: The ReactPlanner instance (used to access summarizer client)
        trajectory: The trajectory to compress
        threshold: Character threshold for compression candidates

    Returns:
        A tuple of (compressed_trajectory, compression_result)
    """
    # Use summarizer client if available, otherwise fall back to main client
    client = getattr(planner, "_summarizer_client", None) or getattr(planner, "_client", None)
    if client is None:
        raise ValueError("No LLM client available for compression")

    # Deep copy the trajectory using serialization round-trip
    compressed = Trajectory.from_serialised(trajectory.serialise())
    original_size = _estimate_trajectory_size(trajectory)

    steps_compressed = 0
    for step in compressed.steps:
        if step.llm_observation is not None and _is_large_observation(
            step.llm_observation, threshold
        ):
            tool_name = (step.action.next_node if step.action else None) or "unknown_tool"
            summary = await _summarise_single_observation(client, tool_name, step.llm_observation)

            # Replace with compressed marker
            step.llm_observation = {"_compressed": True, "summary": summary}
            steps_compressed += 1

            logger.debug(
                "observation_compressed",
                extra={"tool": tool_name, "step": compressed.steps.index(step)},
            )

    compressed_size = _estimate_trajectory_size(compressed)

    result = CompressionResult(
        compressed=steps_compressed > 0,
        steps_compressed=steps_compressed,
        original_size_chars=original_size,
        compressed_size_chars=compressed_size,
    )

    logger.info(
        "trajectory_compression_complete",
        extra={
            "steps_compressed": steps_compressed,
            "original_size": original_size,
            "compressed_size": compressed_size,
            "reduction_pct": round((1 - compressed_size / original_size) * 100, 1)
            if original_size > 0
            else 0,
        },
    )

    return compressed, result


__all__ = [
    "compress_trajectory",
    "CompressionResult",
    "DEFAULT_COMPRESSION_THRESHOLD",
]
