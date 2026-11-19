"""Payload shaping and observation guardrails for the React planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from .models import FinalPayload, ObservationGuardrailConfig, PlannerEvent
from .trajectory import Trajectory

if TYPE_CHECKING:
    from ..artifacts import ArtifactScope, ArtifactStore
    from ..catalog import NodeSpec
    from .artifact_registry import ArtifactRegistry

logger = logging.getLogger("penguiflow.planner")


_NO_ANSWER = "No answer produced."
_ANSWER_KEYS = (
    "raw_answer",
    "answer",
    "text",
    "result",
    "output",
    "response",
    "message",
    "content",
    "greeting",
    "joke",
    "reply",
    "summary",
    "explanation",
    "description",
    "body",
)
_THINKING_PHRASES = (
    "i need to",
    "i should",
    "i will",
    "let me",
    "i'll",
    "first,",
    "now i",
    "the user",
    "based on",
    "looking at",
    "i can see",
    "i notice",
    "according to",
)


def _mapping_answer(observation: Mapping[str, Any]) -> str | None:
    """Extract an answer string from a mapping observation, if any."""
    # First pass: check for answer-like keys (prioritized order)
    for key in _ANSWER_KEYS:
        if key not in observation:
            continue
        value = observation[key]
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            # Recursively extract from nested dict
            return _fallback_answer(value)
        if value is not None:
            return str(value)

    # Second pass: check if there's a nested 'args' dict with answer-like
    # keys
    if "args" in observation and isinstance(observation["args"], Mapping):
        nested = _fallback_answer(observation["args"])
        if nested != _NO_ANSWER:
            return nested

    # Third pass: if observation has exactly one string value > 10 chars, use
    # it (excluding 'thought' and 'next_node' which are planner metadata)
    excluded_keys = {"thought", "next_node", "plan", "join"}
    string_values = [
        v
        for k, v in observation.items()
        if k not in excluded_keys and isinstance(v, str) and len(v) > 10
    ]
    if len(string_values) == 1:
        return string_values[0]

    # Fourth pass: use 'thought' as last resort if it looks like an answer
    return _thought_answer(observation.get("thought", ""))


def _thought_answer(thought: object) -> str | None:
    """Return a long ``thought`` that does not read like thinking."""
    if not isinstance(thought, str) or len(thought) <= 20:
        return None
    thought_lower = thought.lower().strip()
    if any(thought_lower.startswith(p) for p in _THINKING_PHRASES):
        return None
    return thought


def _fallback_answer(last_observation: object) -> str:
    """Provide a safe fallback answer when planner args are missing.

    This function extracts a human-readable answer string from various payload
    formats. Per RFC_STRUCTURED_PLANNER_OUTPUT, the result must be a plain
    string (not JSON).
    """
    if isinstance(last_observation, Mapping):
        answer = _mapping_answer(last_observation)
        if answer is not None:
            return answer
    if isinstance(last_observation, str):
        return last_observation
    if last_observation is not None:
        try:
            return json.dumps(last_observation, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(last_observation)
    return _NO_ANSWER


def _build_failure_payload(
    spec: NodeSpec, args: BaseModel, exc: Exception
) -> dict[str, Any]:
    suggestion = getattr(exc, "suggestion", None)
    if suggestion is None:
        suggestion = getattr(exc, "remedy", None)
    payload: dict[str, Any] = {
        "node": spec.name,
        "args": args.model_dump(mode="json"),
        "error_code": exc.__class__.__name__,
        "message": str(exc),
    }
    if suggestion:
        payload["suggestion"] = str(suggestion)
    return payload


async def _clamp_observation(
    *,
    observation: dict[str, Any],
    spec_name: str,
    trajectory_step: int,
    config: ObservationGuardrailConfig,
    artifact_store: ArtifactStore,
    artifact_registry: ArtifactRegistry,
    active_trajectory: Trajectory | None,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
    scope: ArtifactScope | None = None,
) -> tuple[dict[str, Any], bool]:
    """Apply observation size guardrails to prevent context overflow.

    This is the final safety net after ToolNode's artifact extraction. It
    ensures no single observation exceeds the configured limits.
    """
    # Serialize to check total size
    try:
        serialized = json.dumps(observation, ensure_ascii=False)
    except (TypeError, ValueError):
        # Already JSON-serializable from model_dump, but defensive
        serialized = str(observation)

    original_size = len(serialized)

    # Fast path: observation is within limits
    if original_size <= config.max_observation_chars:
        return observation, False

    # Store as artifact if above threshold and artifact store supports it
    if (
        config.auto_artifact_threshold > 0
        and original_size >= config.auto_artifact_threshold
    ):
        try:
            ref = await artifact_store.put_text(
                serialized,
                namespace=f"observation.{spec_name}",
                scope=scope,
            )
            artifact_registry.register_binary_artifact(
                ref,
                source_tool=f"observation.{spec_name}",
                step_index=trajectory_step,
            )
            if isinstance(active_trajectory, Trajectory) and isinstance(
                active_trajectory.metadata, MutableMapping
            ):
                artifact_registry.write_snapshot(active_trajectory.metadata)
            preview = (
                serialized[: config.preview_length] + "..."
                if len(serialized) > config.preview_length
                else serialized
            )
            clamped = {
                "artifact": ref.model_dump(),
                "summary": (
                    "Large observation stored as artifact "
                    f"({original_size} chars). "
                    f"Artifact ID: {ref.id}"
                ),
                "preview": preview,
            }
            _emit_observation_clamped_event(
                emit_event=emit_event,
                time_source=time_source,
                node_name=spec_name,
                trajectory_step=trajectory_step,
                original_size=original_size,
                clamped_size=len(json.dumps(clamped)),
                method="artifact",
            )
        except Exception as exc:
            logger.debug(f"Failed to store observation as artifact: {exc}")
            # Fall through to truncation
        else:
            return clamped, True

    # Truncate approach
    if config.preserve_structure:
        clamped = _truncate_observation_preserving_structure(
            observation,
            config.max_observation_chars,
            config.max_field_chars,
            config=config,
        )
    else:
        # Simple truncation of serialized form
        suffix_template = config.truncation_suffix
        suffix_len = len(suffix_template.format(truncated_chars=0))
        truncated_chars = (
            original_size - config.max_observation_chars + suffix_len
        )
        truncated_text = serialized[
            : config.max_observation_chars - suffix_len
        ]
        clamped = {
            "truncated_observation": truncated_text,
            "truncation_note": suffix_template.format(
                truncated_chars=truncated_chars
            ),
        }

    clamped_size = len(json.dumps(clamped, ensure_ascii=False))
    _emit_observation_clamped_event(
        emit_event=emit_event,
        time_source=time_source,
        node_name=spec_name,
        trajectory_step=trajectory_step,
        original_size=original_size,
        clamped_size=clamped_size,
        method="truncate",
    )
    return clamped, True


def _truncate_observation_preserving_structure(
    observation: dict[str, Any],
    max_total_chars: int,
    max_field_chars: int,
    *,
    config: ObservationGuardrailConfig,
) -> dict[str, Any]:
    """Truncate observation while preserving dict structure.

    Truncates individual string values rather than the entire serialized form.
    """
    result: dict[str, Any] = {}

    for key, value in observation.items():
        if isinstance(value, str) and len(value) > max_field_chars:
            truncated_chars = len(value) - max_field_chars
            result[key] = value[
                :max_field_chars
            ] + config.truncation_suffix.format(
                truncated_chars=truncated_chars
            )
        elif isinstance(value, dict):
            # Recursively handle nested dicts
            result[key] = _truncate_observation_preserving_structure(
                value, max_total_chars, max_field_chars, config=config
            )
        elif isinstance(value, list):
            # Truncate list if too many items
            if len(value) > 20:
                result[key] = [
                    *value[:20],
                    f"... [{len(value) - 20} more items]",
                ]
            else:
                result[key] = value
        else:
            result[key] = value

    # Check if still too large after field truncation
    serialized = json.dumps(result, ensure_ascii=False)
    if len(serialized) > max_total_chars:
        # Further truncate the largest string fields
        str_fields = [
            (k, len(json.dumps(v)))
            for k, v in result.items()
            if isinstance(v, str)
        ]
        str_fields.sort(key=lambda x: x[1], reverse=True)

        for field_name, _ in str_fields:
            if len(serialized) <= max_total_chars:
                break
            current_val = result[field_name]
            if isinstance(current_val, str) and len(current_val) > 100:
                # Truncate to preview length
                result[field_name] = current_val[
                    : config.preview_length
                ] + config.truncation_suffix.format(
                    truncated_chars=len(current_val) - config.preview_length
                )
                serialized = json.dumps(result, ensure_ascii=False)

    return result


def _emit_observation_clamped_event(
    *,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
    node_name: str,
    trajectory_step: int,
    original_size: int,
    clamped_size: int,
    method: str,
) -> None:
    """Emit event when observation is clamped."""
    emit_event(
        PlannerEvent(
            event_type="observation_clamped",
            ts=time_source(),
            trajectory_step=trajectory_step,
            node_name=node_name,
            extra={
                "original_size": original_size,
                "clamped_size": clamped_size,
                "method": method,
                "reduction_pct": round(
                    (1 - clamped_size / original_size) * 100, 1
                )
                if original_size > 0
                else 0,
            },
        )
    )
    logger.info(
        "observation_clamped",
        extra={
            "node_name": node_name,
            "original_size": original_size,
            "clamped_size": clamped_size,
            "method": method,
        },
    )


def _first_fallback_answer(*sources: object) -> str | None:
    """Return the first real fallback answer among ``sources``."""
    for source in sources:
        if source is None:
            continue
        extracted = _fallback_answer(source)
        logger.debug(
            "fallback_answer_extraction",
            extra={
                "source_type": type(source).__name__,
                "extracted": extracted[:200] if extracted else None,
            },
        )
        if extracted and extracted != _NO_ANSWER:
            return extracted
    return None


def _log_no_answer(args: object, last_observation: object) -> None:
    """Log why no answer could be extracted from the final payload."""
    args_keys = list(args.keys()) if isinstance(args, dict) else None
    logger.warning(
        "no_answer_extracted",
        extra={
            "input_args": str(args)[:500] if args else None,
            "input_args_type": type(args).__name__ if args else None,
            "input_args_keys": args_keys,
            "last_observation": str(last_observation)[:200]
            if last_observation
            else None,
            "expected_keys": [
                "raw_answer",
                "answer",
                "text",
                "result",
                "output",
                "response",
                "message",
                "content",
            ],
        },
    )


def _initial_payload(args: object) -> dict[str, Any]:
    """Seed the final payload from the finish action's args."""
    if isinstance(args, BaseModel):
        return args.model_dump(mode="json")
    if isinstance(args, Mapping):
        return dict(args)
    if args is not None:
        return {"raw_answer": _fallback_answer(args)}
    return {}


def _move_unknown_fields_to_extra(payload_data: dict[str, Any]) -> None:
    """Nest the keys FinalPayload does not declare under ``extra``."""
    known_fields = set(FinalPayload.model_fields)
    extra_payload: dict[str, Any] = {}
    existing_extra = payload_data.get("extra")
    if isinstance(existing_extra, Mapping):
        extra_payload.update(existing_extra)
    for key in list(payload_data.keys()):
        if key not in known_fields:
            extra_payload[key] = payload_data.pop(key)
    if extra_payload:
        payload_data["extra"] = extra_payload


def _build_final_payload(
    *,
    args: object,
    last_observation: object,
    artifacts: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]] | None,
) -> FinalPayload:
    logger.debug(
        "build_final_payload_start",
        extra={
            "args_type": type(args).__name__,
            "args_value": str(args)[:500] if args else None,
            "last_observation_type": type(last_observation).__name__
            if last_observation
            else None,
            "last_observation_value": str(last_observation)[:500]
            if last_observation
            else None,
        },
    )

    payload_data = _initial_payload(args)
    if not payload_data.get("raw_answer"):
        # Try args first (the LLM's answer), then last_observation
        raw_answer = _first_fallback_answer(args, last_observation)
        if raw_answer is None:
            _log_no_answer(args, last_observation)
        payload_data["raw_answer"] = raw_answer or _NO_ANSWER

    payload_data["artifacts"] = dict(artifacts)
    if sources is not None:
        payload_data["sources"] = list(sources)
    _move_unknown_fields_to_extra(payload_data)

    try:
        return FinalPayload.model_validate(payload_data)
    except ValidationError as exc:
        logger.warning(
            "final_payload_validation_failed",
            extra={"error": str(exc)},
        )
        # Try args first, then last_observation (consistent with above)
        return FinalPayload(
            raw_answer=_first_fallback_answer(args, last_observation)
            or _NO_ANSWER,
            artifacts=dict(artifacts),
        )
