"""Validation, salvage, and repair helpers for the React planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ValidationError

from ..catalog import NodeSpec
from . import prompts
from .llm import _coerce_llm_response
from .migration import try_normalize_action
from .models import PlannerAction, PlannerEvent
from .trajectory import Trajectory

logger = logging.getLogger("penguiflow.planner")

AUTO_STR_SENTINEL = "<auto>"


def _validate_llm_context(
    llm_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Ensure llm_context is JSON-serialisable."""

    if llm_context is None:
        return None
    if not isinstance(llm_context, Mapping):
        raise TypeError("llm_context must be a mapping of JSON-serializable data")
    try:
        json.dumps(llm_context, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"llm_context must be JSON-serializable: {exc}") from exc
    return dict(llm_context)


def _coerce_tool_context(
    tool_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalise tool_context to a mutable dict."""

    if tool_context is None:
        return {}
    if not isinstance(tool_context, Mapping):
        raise TypeError("tool_context must be a mapping")
    return dict(tool_context)


def _salvage_action_payload(raw: str) -> PlannerAction | None:
    """Attempt to coerce loosely-structured JSON into a PlannerAction.

    This is a best-effort salvage path used when strict parsing fails. It accepts
    legacy, unified, and hybrid shapes and normalizes them into the current
    PlannerAction model.
    """

    return try_normalize_action(raw)


def _summarize_validation_error(exc: ValidationError, *, limit: int = 240) -> str:
    """Build a compact, human-readable validation summary."""
    summary = str(exc)
    try:
        errors = exc.errors()
    except Exception:
        errors = []
    if errors:
        first = errors[0]
        loc = ".".join(str(part) for part in first.get("loc", []))
        msg = str(first.get("msg") or "validation error")
        summary = f"{loc}: {msg}" if loc else msg
    if len(summary) > limit:
        summary = summary[: limit - 3] + "..."
    return summary


def _default_for_annotation(annotation: Any) -> Any:
    """Generate a lightweight placeholder for a required field."""

    origin = get_origin(annotation)
    if origin is Literal:
        values = get_args(annotation)
        if values:
            return values[0]

    if origin is None:
        if annotation is str:
            return AUTO_STR_SENTINEL
        if annotation is bool:
            return False
        if annotation is int:
            return 0
        if annotation is float:
            return 0.0
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return {}
    else:
        if origin in (list, set, tuple, Sequence):
            return []
        if origin in (dict, Mapping):
            return {}
        if origin is type(None):
            return None

        for arg in get_args(annotation):
            if arg is type(None):
                continue
            candidate = _default_for_annotation(arg)
            if candidate is not None:
                return candidate

    return "<auto>"


def _autofill_missing_args(
    spec: NodeSpec,
    args: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]] | None:
    """Fill required args with safe defaults to avoid repeated validation loops."""

    provided: dict[str, Any] = dict(args or {})
    filled: dict[str, Any] = {}

    for field_name, field_info in spec.args_model.model_fields.items():
        if field_name in provided and provided[field_name] is not None:
            continue
        if not field_info.is_required():
            continue

        placeholder = _default_for_annotation(field_info.annotation)
        provided[field_name] = placeholder
        filled[field_name] = placeholder

    if not filled:
        return None

    return provided, tuple(filled.keys())


def _scan_placeholder_paths(
    value: Any,
    placeholders: Sequence[str],
    path: str = "",
) -> list[str]:
    matches: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_str = str(key)
            child_path = f"{path}.{key_str}" if path else key_str
            matches.extend(_scan_placeholder_paths(item, placeholders, child_path))
        return matches
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for idx, item in enumerate(value):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            matches.extend(_scan_placeholder_paths(item, placeholders, child_path))
        return matches
    if isinstance(value, str) and value in placeholders:
        matches.append(path or "<root>")
    return matches


def _extract_field_descriptions(spec: NodeSpec) -> dict[str, str]:
    """Extract field descriptions from the tool's args schema.

    This now includes comprehensive hints: description, enum values, examples,
    type constraints, and patterns - giving the model actionable context.
    """
    schema = spec.args_model.model_json_schema()
    properties = schema.get("properties", {})
    # Also check $defs for referenced schemas (enums, nested models)
    defs = schema.get("$defs", {})

    descriptions: dict[str, str] = {}
    for field_name, field_info in properties.items():
        if not isinstance(field_info, dict):
            continue

        hints: list[str] = []

        # Start with description if available
        desc = field_info.get("description")
        if desc:
            hints.append(desc)

        # Handle $ref to definitions (common for enums)
        ref = field_info.get("$ref")
        if ref and ref.startswith("#/$defs/"):
            ref_name = ref.split("/")[-1]
            ref_schema = defs.get(ref_name, {})
            # Check for enum in referenced schema
            if "enum" in ref_schema:
                enum_values = ref_schema["enum"]
                hints.append(f"Valid options: {enum_values}")
            # Check for description in referenced schema
            if not desc and "description" in ref_schema:
                hints.insert(0, ref_schema["description"])

        # Handle anyOf (common for Optional fields with enums)
        any_of = field_info.get("anyOf", [])
        for option in any_of:
            if isinstance(option, dict):
                if "$ref" in option:
                    ref_name = option["$ref"].split("/")[-1]
                    ref_schema = defs.get(ref_name, {})
                    if "enum" in ref_schema:
                        hints.append(f"Valid options: {ref_schema['enum']}")
                elif "enum" in option:
                    hints.append(f"Valid options: {option['enum']}")

        # Direct enum values
        if "enum" in field_info:
            hints.append(f"Valid options: {field_info['enum']}")

        # Examples
        examples = field_info.get("examples")
        if examples:
            hints.append(f"Examples: {examples}")

        # Type hint (useful for complex types)
        field_type = field_info.get("type")
        if field_type and field_type not in {"string", "integer", "number", "boolean"}:
            hints.append(f"Type: {field_type}")

        # Pattern (regex constraint)
        pattern = field_info.get("pattern")
        if pattern:
            hints.append(f"Must match pattern: {pattern}")

        # Default value (if not null)
        default = field_info.get("default")
        if default is not None:
            hints.append(f"Default: {default}")

        if hints:
            descriptions[field_name] = " | ".join(hints)

    return descriptions


def _is_arg_fill_eligible(
    spec: NodeSpec,
    missing_fields: Sequence[str],
    trajectory: Trajectory,
    *,
    arg_fill_enabled: bool,
) -> bool:
    """
    Check if arg-fill should be attempted for this tool call.

    Arg-fill is eligible when:
    1. arg_fill_enabled is True
    2. Missing fields are simple types (string, number, boolean)
    3. Arg-fill hasn't already been attempted for this action
    4. Tool exists in catalog (already validated by caller)
    """
    if not arg_fill_enabled:
        return False

    # Check if already attempted for THIS tool (per-tool tracking prevents
    # one tool's failed arg-fill from blocking other tools)
    if trajectory.metadata.get(f"arg_fill_attempted_{spec.name}"):
        return False

    # Get schema to check field types
    schema = spec.args_model.model_json_schema()
    properties = schema.get("properties", {})

    # Only allow simple types (string, number, boolean, integer)
    allowed_types = {"string", "number", "integer", "boolean"}
    for field in missing_fields:
        field_info = properties.get(field, {})
        if not isinstance(field_info, dict):
            return False
        field_type = field_info.get("type")
        # If type is not specified or not simple, skip arg-fill
        if field_type not in allowed_types:
            # Check if it's a union/anyOf with allowed types
            any_of = field_info.get("anyOf", [])
            if not any_of or not all(
                isinstance(t, dict) and t.get("type") in allowed_types | {"null"}
                for t in any_of
            ):
                logger.debug(
                    "arg_fill_ineligible_complex_type",
                    extra={"field": field, "type": field_type, "any_of": any_of},
                )
                return False

    return True


def _parse_arg_fill_response(
    raw: str,
    expected_fields: Sequence[str],
) -> dict[str, Any] | None:
    """
    Parse an arg-fill response, trying JSON first then tagged format.

    Returns:
        Parsed field values dict, or None if parsing failed.
    """
    # Strip any markdown fences
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines if they're fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try JSON parsing first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # Validate that all values are non-placeholder
            result: dict[str, Any] = {}
            for field in expected_fields:
                if field in parsed:
                    value = parsed[field]
                    # Reject placeholder values
                    if isinstance(value, str):
                        lower = value.lower().strip()
                        if lower in {"<auto>", "unknown", "n/a", "", "<fill_value>", "your value here"}:
                            logger.debug(
                                "arg_fill_placeholder_detected",
                                extra={"field": field, "value": value},
                            )
                            return None
                    result[field] = value
            # Check we got at least one valid field
            if result:
                return result
    except json.JSONDecodeError:
        pass

    # Try tagged format as fallback: <field>value</field>
    import re

    result = {}
    for field in expected_fields:
        pattern = rf"<{re.escape(field)}>(.*?)</{re.escape(field)}>"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            value = match.group(1).strip()
            # Reject placeholder values
            lower = value.lower()
            if lower in {"<auto>", "unknown", "n/a", "", "<fill_value>", "your value here"}:
                logger.debug(
                    "arg_fill_placeholder_detected_tagged",
                    extra={"field": field, "value": value},
                )
                return None
            result[field] = value

    if result:
        return result

    return None


async def _attempt_arg_fill(
    *,
    trajectory: Trajectory,
    spec: NodeSpec,
    action: PlannerAction,
    missing_fields: list[str],
    build_messages: Callable[[Trajectory], Awaitable[list[dict[str, str]]]],
    client: Any,
    cost_tracker: Any,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
) -> dict[str, Any] | None:
    """
    Attempt to fill missing args with a simplified LLM call.

    This uses a minimal prompt asking only for the missing field values,
    which is easier for small models than re-emitting the full action JSON.
    """
    # Mark that we're attempting arg-fill for this specific tool
    trajectory.metadata[f"arg_fill_attempted_{spec.name}"] = True

    # Get field descriptions for context
    field_descriptions = _extract_field_descriptions(spec)

    # Get user query for context
    user_query = trajectory.resume_user_input or trajectory.query

    # Build the arg-fill prompt
    fill_prompt = prompts.render_arg_fill_prompt(
        tool_name=spec.name,
        missing_fields=missing_fields,
        field_descriptions=field_descriptions,
        user_query=user_query,
    )

    # Build messages: use existing conversation context + arg-fill prompt
    # Use "user" role so it's a follow-up request within the conversation
    # (the system prompt with full instructions is already in base_messages)
    base_messages = await build_messages(trajectory)
    messages = list(base_messages) + [
        {"role": "user", "content": fill_prompt},
    ]

    # Emit event for observability
    emit_event(
        PlannerEvent(
            event_type="arg_fill_attempt",
            ts=time_source(),
            trajectory_step=len(trajectory.steps),
            node_name=spec.name,
            extra={
                "missing_fields": missing_fields,
                "field_count": len(missing_fields),
            },
        )
    )

    start_time = time_source()

    try:
        # Make the LLM call with a simple JSON response format
        # Use a minimal schema for just the expected fields
        llm_result = await client.complete(
            messages=messages,
            response_format={"type": "json_object"},
            stream=False,
            on_stream_chunk=None,
        )
        raw, cost = _coerce_llm_response(llm_result)
        cost_tracker.record_main_call(cost)

        latency_ms = (time_source() - start_time) * 1000

        # Parse the response
        filled = _parse_arg_fill_response(raw, missing_fields)

        if filled is not None:
            # Success! Record metrics
            arg_fill_success_count = int(trajectory.metadata.get("arg_fill_success_count", 0))
            trajectory.metadata["arg_fill_success_count"] = arg_fill_success_count + 1

            emit_event(
                PlannerEvent(
                    event_type="arg_fill_success",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    node_name=spec.name,
                    latency_ms=latency_ms,
                    extra={
                        "filled_fields": list(filled.keys()),
                        "missing_fields": missing_fields,
                    },
                )
            )

            logger.info(
                "arg_fill_success",
                extra={
                    "tool": spec.name,
                    "missing_fields": missing_fields,
                    "filled_fields": list(filled.keys()),
                    "latency_ms": latency_ms,
                },
            )

            return filled

        # Parsing failed
        arg_fill_failure_count = int(trajectory.metadata.get("arg_fill_failure_count", 0))
        trajectory.metadata["arg_fill_failure_count"] = arg_fill_failure_count + 1

        emit_event(
            PlannerEvent(
                event_type="arg_fill_failure",
                ts=time_source(),
                trajectory_step=len(trajectory.steps),
                node_name=spec.name,
                latency_ms=latency_ms,
                error="parse_failed",
                extra={
                    "missing_fields": missing_fields,
                    "raw_response_len": len(raw),
                },
            )
        )

        logger.warning(
            "arg_fill_parse_failed",
            extra={
                "tool": spec.name,
                "missing_fields": missing_fields,
                "raw_preview": raw[:200] if raw else "",
            },
        )

        return None

    except Exception as exc:
        latency_ms = (time_source() - start_time) * 1000

        arg_fill_failure_count = int(trajectory.metadata.get("arg_fill_failure_count", 0))
        trajectory.metadata["arg_fill_failure_count"] = arg_fill_failure_count + 1

        emit_event(
            PlannerEvent(
                event_type="arg_fill_failure",
                ts=time_source(),
                trajectory_step=len(trajectory.steps),
                node_name=spec.name,
                latency_ms=latency_ms,
                error=f"{exc.__class__.__name__}: {exc}",
                extra={"missing_fields": missing_fields},
            )
        )

        logger.warning(
            "arg_fill_exception",
            extra={
                "tool": spec.name,
                "missing_fields": missing_fields,
                "error": str(exc),
            },
        )

        return None


def _parse_finish_repair_response(raw: str) -> str | None:
    """
    Parse a finish repair response to extract raw_answer.

    Returns:
        The raw_answer string if found and valid, None otherwise.
    """
    # Strip any markdown fences
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try JSON parsing
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            # Look for raw_answer or common answer keys at top level
            for key in ("raw_answer", "answer", "text", "response", "content"):
                if key in parsed:
                    value = parsed[key]
                    if isinstance(value, str) and value.strip():
                        # Reject placeholder values
                        lower = value.lower().strip()
                        if lower not in {"<auto>", "unknown", "n/a", "", "<fill_value>"}:
                            return value
                        else:
                            logger.info(
                                "finish_repair_parse_placeholder",
                                extra={"key": key, "value": value[:100] if value else None},
                            )

            # Check if LLM returned full action schema with args.raw_answer
            # This handles cases where model returns {"thought": ..., "next_node": ..., "args": {"raw_answer": ...}}
            if "args" in parsed and isinstance(parsed["args"], dict):
                args_dict = parsed["args"]
                for key in ("raw_answer", "answer", "text", "response", "content"):
                    if key in args_dict:
                        value = args_dict[key]
                        if isinstance(value, str) and value.strip():
                            lower = value.lower().strip()
                            if lower not in {"<auto>", "unknown", "n/a", "", "<fill_value>"}:
                                logger.info(
                                    "finish_repair_parse_from_args",
                                    extra={"key": key, "answer_len": len(value)},
                                )
                                return value

            # JSON parsed but no answer key found
            logger.info(
                "finish_repair_parse_no_answer_key",
                extra={"keys_found": list(parsed.keys())[:10], "response_preview": text[:300]},
            )
    except json.JSONDecodeError as e:
        logger.info(
            "finish_repair_parse_json_error",
            extra={"error": str(e), "response_preview": text[:300]},
        )

    # If the response is just plain text (not JSON), use it directly
    # This handles cases where the model ignores the JSON instruction
    if text and not text.startswith("{"):
        # Clean up common prefixes
        for prefix in ("raw_answer:", "answer:", "response:"):
            if text.lower().startswith(prefix):
                text = text[len(prefix) :].strip()
        if text and text.lower() not in {"<auto>", "unknown", "n/a", "<fill_value>"}:
            return text
        else:
            logger.info(
                "finish_repair_parse_plaintext_placeholder",
                extra={"text_preview": text[:100] if text else None},
            )

    return None


async def _attempt_finish_repair(
    *,
    trajectory: Trajectory,
    action: PlannerAction,
    build_messages: Callable[[Trajectory], Awaitable[list[dict[str, str]]]],
    client: Any,
    cost_tracker: Any,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
    system_prompt_extra: str | None,
    action_seq: int,
) -> str | None:
    """
    Attempt to get the raw_answer when the model finishes without providing one.

    This uses a simplified prompt asking only for the answer text,
    which is easier for small models than re-emitting the full action JSON.
    """
    # Mark that we're attempting finish repair
    trajectory.metadata["finish_repair_attempted"] = True

    # Get user query for context
    user_query = trajectory.resume_user_input or trajectory.query

    # Build the finish repair prompt with voice context
    repair_prompt = prompts.render_finish_repair_prompt(
        thought=action.thought,
        user_query=user_query,
        voice_context=system_prompt_extra,
    )

    # Build messages: use existing conversation context + repair prompt
    # Use "user" role so it's a follow-up request within the conversation
    # (the system prompt with full instructions is already in base_messages)
    base_messages = await build_messages(trajectory)
    messages = list(base_messages) + [
        {"role": "user", "content": repair_prompt},
    ]

    # Emit event for observability
    emit_event(
        PlannerEvent(
            event_type="finish_repair_attempt",
            ts=time_source(),
            trajectory_step=len(trajectory.steps),
            thought=action.thought,
        )
    )

    start_time = time_source()

    try:
        # Make the LLM call
        llm_result = await client.complete(
            messages=messages,
            response_format={"type": "json_object"},
            stream=False,
            on_stream_chunk=None,
        )
        raw, cost = _coerce_llm_response(llm_result)
        cost_tracker.record_main_call(cost)

        latency_ms = (time_source() - start_time) * 1000

        logger.debug(
            "finish_repair_raw_response",
            extra={
                "response_len": len(raw),
                "response_preview": raw[:500] if len(raw) > 500 else raw,
            },
        )

        # Parse the response - look for raw_answer
        raw_answer = _parse_finish_repair_response(raw)

        if raw_answer is not None:
            # Success! Record metrics
            finish_repair_success_count = int(trajectory.metadata.get("finish_repair_success_count", 0))
            trajectory.metadata["finish_repair_success_count"] = finish_repair_success_count + 1

            # Stream the repaired answer to frontend as llm_stream_chunk events
            # This ensures the answer shows up in the UI just like normal streamed responses
            emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": raw_answer,
                        "done": False,
                        "phase": "args",
                        "channel": "answer",
                        "action_seq": action_seq,
                    },
                )
            )
            emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": "",
                        "done": True,
                        "phase": "args",
                        "channel": "answer",
                        "action_seq": action_seq,
                    },
                )
            )

            emit_event(
                PlannerEvent(
                    event_type="finish_repair_success",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    latency_ms=latency_ms,
                    extra={"answer_len": len(raw_answer)},
                )
            )

            return raw_answer

        # Parsing failed - log what we received for debugging
        logger.info(
            "finish_repair_parse_failed",
            extra={
                "raw_len": len(raw),
                "raw_preview": raw[:500] if len(raw) > 500 else raw,
            },
        )

        finish_repair_failure_count = int(trajectory.metadata.get("finish_repair_failure_count", 0))
        trajectory.metadata["finish_repair_failure_count"] = finish_repair_failure_count + 1

        emit_event(
            PlannerEvent(
                event_type="finish_repair_failure",
                ts=time_source(),
                trajectory_step=len(trajectory.steps),
                latency_ms=latency_ms,
                error="parse_failed",
                extra={"raw_len": len(raw)},
            )
        )

        return None

    except Exception as exc:
        latency_ms = (time_source() - start_time) * 1000

        finish_repair_failure_count = int(trajectory.metadata.get("finish_repair_failure_count", 0))
        trajectory.metadata["finish_repair_failure_count"] = finish_repair_failure_count + 1

        emit_event(
            PlannerEvent(
                event_type="finish_repair_failure",
                ts=time_source(),
                trajectory_step=len(trajectory.steps),
                latency_ms=latency_ms,
                error=f"{exc.__class__.__name__}: {exc}",
            )
        )

        logger.warning(
            "finish_repair_exception",
            extra={"error": str(exc)},
        )

        return None


async def _attempt_graceful_failure(
    *,
    trajectory: Trajectory,
    build_messages: Callable[[Trajectory], Awaitable[list[dict[str, str]]]],
    client: Any,
    cost_tracker: Any,
    emit_event: Callable[[PlannerEvent], None],
    time_source: Callable[[], float],
    system_prompt_extra: str | None,
    action_seq: int,
) -> str | None:
    """
    Attempt to get a user-friendly message when the planner hits a failure threshold.

    Instead of returning a technical error, this prompts the model to generate
    a graceful, non-technical response explaining it couldn't complete the action.
    """
    # Get user query for context
    user_query = trajectory.resume_user_input or trajectory.query

    # Build the graceful failure prompt
    graceful_prompt = prompts.render_graceful_failure_prompt(
        user_query=user_query,
        voice_context=system_prompt_extra,
    )

    # Build messages with the graceful failure prompt
    base_messages = await build_messages(trajectory)
    messages = list(base_messages) + [
        {"role": "user", "content": graceful_prompt},
    ]

    emit_event(
        PlannerEvent(
            event_type="graceful_failure_attempt",
            ts=time_source(),
            trajectory_step=len(trajectory.steps),
        )
    )

    start_time = time_source()

    try:
        llm_result = await client.complete(
            messages=messages,
            response_format={"type": "json_object"},
            stream=False,
            on_stream_chunk=None,
        )
        raw, cost = _coerce_llm_response(llm_result)
        cost_tracker.record_main_call(cost)

        latency_ms = (time_source() - start_time) * 1000

        # Parse the response - look for raw_answer
        raw_answer = _parse_finish_repair_response(raw)

        if raw_answer is not None:
            # Stream the graceful response to frontend
            emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": raw_answer,
                        "done": False,
                        "phase": "args",
                        "channel": "answer",
                        "action_seq": action_seq,
                    },
                )
            )
            emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": "",
                        "done": True,
                        "phase": "args",
                        "channel": "answer",
                        "action_seq": action_seq,
                    },
                )
            )

            emit_event(
                PlannerEvent(
                    event_type="graceful_failure_success",
                    ts=time_source(),
                    trajectory_step=len(trajectory.steps),
                    latency_ms=latency_ms,
                    extra={"answer_len": len(raw_answer)},
                )
            )

            logger.info(
                "graceful_failure_success",
                extra={"answer_len": len(raw_answer), "latency_ms": latency_ms},
            )

            return raw_answer

        logger.warning(
            "graceful_failure_parse_failed",
            extra={"raw_len": len(raw), "raw_preview": raw[:300]},
        )
        return None

    except Exception as exc:
        logger.warning(
            "graceful_failure_exception",
            extra={"error": str(exc)},
        )
        return None


def _record_invalid_response(
    *,
    trajectory: Trajectory,
    attempt: int,
    raw: str,
    error: ValidationError,
    salvage_action: PlannerAction | None,
    will_retry: bool,
    time_source: Callable[[], float],
    emit_event: Callable[[PlannerEvent], None],
) -> None:
    stripped = raw.lstrip()
    had_non_json_prefix = bool(stripped) and stripped[0] not in "{["
    had_code_fence = "```" in raw
    response_len = len(raw)
    error_type = error.__class__.__name__
    error_summary = _summarize_validation_error(error)
    next_node_detected = salvage_action.next_node if salvage_action is not None else None

    metadata = trajectory.metadata
    invalid_responses = metadata.get("invalid_responses")
    if not isinstance(invalid_responses, list):
        invalid_responses = []
        metadata["invalid_responses"] = invalid_responses

    entry = {
        "step": len(trajectory.steps),
        "attempt": attempt,
        "error_type": error_type,
        "error_summary": error_summary,
        "next_node_detected": next_node_detected,
        "response_len": response_len,
        "had_code_fence": had_code_fence,
        "had_non_json_prefix": had_non_json_prefix,
        "ts": time_source(),
    }
    invalid_responses.append(entry)

    metadata["validation_failures_count"] = int(metadata.get("validation_failures_count", 0)) + 1
    if will_retry:
        metadata["repair_attempts"] = int(metadata.get("repair_attempts", 0)) + 1
    if salvage_action is not None:
        metadata["salvage_used"] = True

    emit_event(
        PlannerEvent(
            event_type="planner_repair_attempt",
            ts=time_source(),
            trajectory_step=len(trajectory.steps),
            extra={
                "attempt": attempt,
                "error_type": error_type,
                "error_summary": error_summary,
                "next_node_detected": next_node_detected,
                "response_len": response_len,
                "had_code_fence": had_code_fence,
                "had_non_json_prefix": had_non_json_prefix,
            },
        )
    )


def _apply_arg_validation(
    *,
    trajectory: Trajectory,
    spec: NodeSpec,
    action: PlannerAction,
    parsed_args: BaseModel,
    autofilled_fields: Sequence[str],
    record_arg_event: Callable[..., None],
) -> str | None:
    extra = spec.extra or {}
    raw_validation = extra.get("arg_validation")
    validator = extra.get("arg_validator")

    # Parse validation config (if provided)
    validation: dict[str, Any] = {}
    if isinstance(raw_validation, Mapping):
        validation = dict(raw_validation)
    elif raw_validation is True:
        validation = {"reject_placeholders": True}

    # Always include <auto> in placeholder list - this is critical for detecting
    # autofilled values that the model didn't properly fill
    placeholders = list(validation.get("placeholders") or [])
    if AUTO_STR_SENTINEL not in placeholders:
        placeholders.append(AUTO_STR_SENTINEL)

    # ALWAYS scan for placeholders - this is independent of arg_validation config
    # The autofill mechanism inserts <auto> placeholders, and we need to detect them
    # regardless of whether custom validation rules are configured
    placeholder_paths: list[str] = []
    if placeholders:
        placeholder_paths = _scan_placeholder_paths(action.args or {}, placeholders)

    # Determine validation behavior:
    # - emit_suspect: default True (always emit suspect events for observability)
    # - reject_placeholders: default True when autofilled_fields exist (catch autofill placeholders)
    #                        otherwise use config value (default False for backwards compat)
    # - reject_autofill: use config value (default False)
    emit_suspect = validation.get("emit_suspect", True)

    # Key change: reject placeholders by default when they exist in autofilled fields
    # This catches the <auto> sentinel that _autofill_missing_args inserts
    has_autofilled_placeholders = bool(
        autofilled_fields and placeholder_paths and
        any(path.split(".")[0].split("[")[0] in autofilled_fields for path in placeholder_paths)
    )
    reject_placeholders = validation.get("reject_placeholders", has_autofilled_placeholders)
    reject_autofill = validation.get("reject_autofill", False)

    if emit_suspect and (placeholder_paths or autofilled_fields):
        record_arg_event(
            trajectory,
            event_type="planner_args_suspect",
            spec=spec,
            error_summary=None,
            placeholders=placeholders,
            placeholder_paths=placeholder_paths,
            autofilled_fields=autofilled_fields,
            source="placeholder",
        )

    if reject_placeholders and placeholder_paths:
        error_summary = "placeholder values detected in tool args"
        record_arg_event(
            trajectory,
            event_type="planner_args_invalid",
            spec=spec,
            error_summary=error_summary,
            placeholders=placeholders,
            placeholder_paths=placeholder_paths,
            autofilled_fields=autofilled_fields,
            source="placeholder",
        )
        return error_summary

    if reject_autofill and autofilled_fields:
        error_summary = "required tool args were autofilled"
        record_arg_event(
            trajectory,
            event_type="planner_args_invalid",
            spec=spec,
            error_summary=error_summary,
            placeholders=placeholders,
            placeholder_paths=placeholder_paths,
            autofilled_fields=autofilled_fields,
            source="autofill",
        )
        return error_summary

    # Custom validator (only runs if configured)
    if callable(validator):
        try:
            result = validator(parsed_args, action)
        except Exception as exc:
            error_summary = f"arg_validator raised {exc.__class__.__name__}: {exc}"
            record_arg_event(
                trajectory,
                event_type="planner_args_invalid",
                spec=spec,
                error_summary=error_summary,
                placeholders=placeholders,
                placeholder_paths=placeholder_paths,
                autofilled_fields=autofilled_fields,
                source="validator_error",
            )
            return error_summary

        if result is None or result is True:
            return None

        if isinstance(result, str):
            error_summary = result
        elif isinstance(result, Mapping):
            error_summary = str(result.get("error") or result)
        else:
            error_summary = "arg_validator rejected args"

        record_arg_event(
            trajectory,
            event_type="planner_args_invalid",
            spec=spec,
            error_summary=error_summary,
            placeholders=placeholders,
            placeholder_paths=placeholder_paths,
            autofilled_fields=autofilled_fields,
            source="validator",
        )
        return error_summary

    return None
