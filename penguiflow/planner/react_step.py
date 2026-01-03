"""Step execution for the React planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from pydantic import ValidationError

from . import prompts
from .llm import _coerce_llm_response, _LiteLLMJSONClient
from .models import PlannerAction, PlannerEvent
from .streaming import _StreamingArgsExtractor, _StreamingThoughtExtractor
from .trajectory import Trajectory
from .validation_repair import _salvage_action_payload

logger = logging.getLogger("penguiflow.planner")


async def step(planner: Any, trajectory: Trajectory) -> PlannerAction:
    base_messages = await planner._build_messages(trajectory)
    arg_repair_message: str | None = None
    if isinstance(trajectory.metadata, MutableMapping):
        arg_repair_message = trajectory.metadata.pop("arg_repair_message", None)
    if arg_repair_message:
        patched: list[dict[str, str]] = []
        inserted = False
        for msg in base_messages:
            if not inserted and msg.get("role") != "system":
                patched.append({"role": "system", "content": arg_repair_message})
                inserted = True
            patched.append(msg)
        if not inserted:
            patched.append({"role": "system", "content": arg_repair_message})
        base_messages = patched
    messages: list[dict[str, str]] = list(base_messages)
    last_error: str | None = None
    last_raw: str | None = None

    for attempt in range(1, planner._repair_attempts + 1):
        if last_error is not None:
            messages = list(base_messages) + [
                {
                    "role": "system",
                    "content": prompts.render_repair_message(last_error),
                }
            ]

        response_format: Mapping[str, Any] | None = planner._response_format
        if response_format is None and getattr(planner._client, "expects_json_schema", False):
            response_format = planner._action_schema

        stream_allowed = (
            planner._stream_final_response
            and isinstance(planner._client, _LiteLLMJSONClient)
            and (
                response_format is None
                or (
                    isinstance(response_format, Mapping)
                    and response_format.get("type") in ("json_object", "json_schema")
                )
            )
        )

        # Create extractor to detect finish actions and stream args content
        args_extractor = _StreamingArgsExtractor()
        thought_extractor = _StreamingThoughtExtractor()

        current_action_seq = planner._action_seq

        def _emit_llm_chunk(
            text: str,
            done: bool,
            *,
            _extractor: _StreamingArgsExtractor = args_extractor,
            _thought_extractor: _StreamingThoughtExtractor = thought_extractor,
            _action_seq: int = current_action_seq,
        ) -> None:
            if planner._event_callback is None:
                return

            # DEBUG: Log incoming chunks
            logger.debug(
                "llm_chunk_received",
                extra={"text": text[:100] if text else "", "done": done, "buffer_len": len(_extractor._buffer)},
            )

            thought_chars = _thought_extractor.feed(text)
            if thought_chars:
                thought_text = "".join(thought_chars)
                planner._emit_event(
                    PlannerEvent(
                        event_type="llm_stream_chunk",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "text": thought_text,
                            "done": False,
                            "phase": "observation",
                            "channel": "thinking",
                        },
                    )
                )

            # Feed chunk to extractor to detect args content
            args_chars = _extractor.feed(text)

            # DEBUG: Log extractor state
            logger.debug(
                "extractor_state",
                extra={
                    "is_finish": _extractor.is_finish_action,
                    "in_args_string": _extractor._in_args_string,
                    "chars_extracted": len(args_chars),
                },
            )

            # Emit args content as "answer" phase for real-time display
            if args_chars:
                # Batch small chars into reasonable chunks for efficiency
                args_text = "".join(args_chars)
                planner._emit_event(
                    PlannerEvent(
                        event_type="llm_stream_chunk",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "text": args_text,
                            "done": False,
                            "phase": "answer",
                            "channel": "answer",
                            "action_seq": _action_seq,
                        },
                    )
                )

            # Emit done signal when LLM finishes and it was a finish action
            if done and _extractor.is_finish_action:
                planner._emit_event(
                    PlannerEvent(
                        event_type="llm_stream_chunk",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            "text": "",
                            "done": True,
                            "phase": "answer",
                            "channel": "answer",
                            "action_seq": _action_seq,
                        },
                    )
                )

        if planner._event_callback is not None:
            planner._emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=planner._time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": "",
                        "done": False,
                        "phase": "action",
                        "channel": "thinking",
                        "action_seq": current_action_seq,
                    },
                )
            )
        try:
            llm_result = await planner._client.complete(
                messages=messages,
                response_format=response_format,
                stream=stream_allowed,
                on_stream_chunk=_emit_llm_chunk if stream_allowed else None,
            )
        finally:
            if planner._event_callback is not None:
                planner._emit_event(
                    PlannerEvent(
                        event_type="llm_stream_chunk",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={"text": "", "done": True, "phase": "action", "channel": "thinking"},
                    )
                )
        raw, cost = _coerce_llm_response(llm_result)
        last_raw = raw
        planner._cost_tracker.record_main_call(cost)

        # Debug log the raw LLM response for troubleshooting
        logger.debug(
            "llm_raw_response",
            extra={
                "attempt": attempt,
                "response_len": len(raw),
                "response_preview": raw[:1000] if len(raw) > 1000 else raw,
            },
        )

        try:
            action = PlannerAction.model_validate_json(raw)
            # Log successful parse with args info for finish actions
            if action.next_node is None:
                logger.debug(
                    "finish_action_parsed",
                    extra={
                        "has_args": action.args is not None,
                        "args_keys": list(action.args.keys()) if isinstance(action.args, dict) else None,
                        "raw_answer_present": "raw_answer" in (action.args or {}),
                    },
                )
            return action
        except ValidationError as exc:
            salvaged = _salvage_action_payload(raw)
            will_retry = salvaged is None and attempt < planner._repair_attempts
            planner._record_invalid_response(
                trajectory,
                attempt=attempt,
                raw=raw,
                error=exc,
                salvage_action=salvaged,
                will_retry=will_retry,
            )
            if salvaged is not None:
                logger.info(
                    "planner_action_salvaged",
                    extra={"errors": json.dumps(exc.errors(), ensure_ascii=False)},
                )
                return salvaged
            last_error = json.dumps(exc.errors(), ensure_ascii=False)
            continue

    if last_raw is not None:
        # Try to extract raw_answer/answer content using regex before naive truncation
        # This handles cases where the JSON is malformed but raw_answer is readable
        extracted_answer: str | None = None
        import re

        # Look for "raw_answer": "..." or "answer": "..." pattern
        answer_match = re.search(
            r'"(?:raw_answer|answer)"\s*:\s*"((?:[^"\\]|\\.)*)',
            last_raw,
            re.DOTALL,
        )
        if answer_match:
            # Unescape the content
            extracted_answer = answer_match.group(1)
            extracted_answer = extracted_answer.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
            if extracted_answer:
                logger.info(
                    "planner_fallback_answer_extracted",
                    extra={"length": len(extracted_answer)},
                )
                return PlannerAction(
                    thought="Extracted answer from malformed JSON",
                    next_node=None,
                    args={"raw_answer": extracted_answer},
                )

        # If no answer extracted, fall back to truncation
        max_chars = 1000
        error = f"LLM response could not be parsed after {planner._repair_attempts} attempts."
        last_raw = last_raw[:max_chars] if len(last_raw) > max_chars else last_raw
        logger.warning(
            "planner_action_parse_failed",
            extra={
                "error": error,
                "raw_preview": last_raw,
            },
        )
        return PlannerAction(
            thought=error,
            next_node=None,
            args={"raw_answer": last_raw},
        )

    # Should not reach here, but return a fail-safe action
    return PlannerAction(
        thought="Failed to parse LLM response",
        next_node=None,
        args={"raw_answer": "LLM response parsing failed"},
    )
