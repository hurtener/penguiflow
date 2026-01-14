"""Step execution for the React planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, MutableMapping
from typing import Any

from pydantic import ValidationError

from . import prompts
from .llm import _coerce_llm_response, _LiteLLMJSONClient
from .migration import normalize_action_with_debug
from .models import PlannerAction, PlannerEvent
from .streaming import _StreamingArgsExtractor, _StreamingThoughtExtractor
from .trajectory import Trajectory
from .validation_repair import _salvage_action_payload

logger = logging.getLogger("penguiflow.planner")


async def step(planner: Any, trajectory: Trajectory) -> PlannerAction:
    had_pending_steering = bool(trajectory.steering_inputs)

    def _consume_steering_inputs() -> None:
        if had_pending_steering:
            trajectory.steering_inputs.clear()

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

    # DEBUG_REMOVE: Log message structure being sent to LLM
    logger.info(
        "DEBUG_step_messages",
        extra={
            "message_count": len(messages),
            "message_roles": [m.get("role") for m in messages],
            "last_message_preview": messages[-1].get("content", "")[:500] if messages else "none",
            "has_arg_repair": arg_repair_message is not None,
        },
    )

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

        # Native LLM adapter supports the same callback-based streaming contract
        # as the LiteLLM client. Keep the gating here so templates can enable/disable
        # streaming consistently via `stream_final_response`.
        from penguiflow.llm.protocol import NativeLLMAdapter

        stream_allowed = (
            planner._stream_final_response
            and isinstance(planner._client, (_LiteLLMJSONClient, NativeLLMAdapter))
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

        def _emit_llm_reasoning_chunk(text: str, done: bool, *, _action_seq: int = current_action_seq) -> None:
            if planner._event_callback is None:
                return
            if not text and not done:
                return
            planner._emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=planner._time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": text,
                        "done": done,
                        "phase": "thinking",
                        "channel": "thinking",
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
            if (
                isinstance(planner._client, (_LiteLLMJSONClient, NativeLLMAdapter))
                and getattr(planner, "_use_native_reasoning", True)
            ):
                llm_result = await planner._client.complete(
                    messages=messages,
                    response_format=response_format,
                    stream=stream_allowed,
                    on_stream_chunk=_emit_llm_chunk if stream_allowed else None,
                    on_reasoning_chunk=_emit_llm_reasoning_chunk,
                )
            else:
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

        # DEBUG_REMOVE: Log raw LLM response for troubleshooting (INFO level for debugging)
        logger.info(
            "DEBUG_llm_raw_response",
            extra={
                "attempt": attempt,
                "response_len": len(raw),
                "response_preview": raw[:1000] if len(raw) > 1000 else raw,
            },
        )

        try:
            action, parse_debug = normalize_action_with_debug(raw)
        except ValueError as exc:
            # Unparseable JSON (e.g. weak model emitted non-JSON). Treat as repairable.
            last_error = str(exc)
            continue
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
                _consume_steering_inputs()
                return salvaged
            last_error = json.dumps(exc.errors(), ensure_ascii=False)
            continue

        # Attach raw LLM response for debugging (excluded from serialization)
        action.raw_llm_response = raw
        if parse_debug is not None:
            other_actions = parse_debug.get("other_actions")
            if isinstance(other_actions, list) and other_actions:
                # Attach candidates to the action for runtime fallbacks/batched execution.
                action.alternate_actions = [item for item in other_actions if isinstance(item, dict)]
                # Track repeated multi-action emission across runs to inject guidance.
                if hasattr(planner, "_multi_action_history_count"):
                    planner._multi_action_history_count += 1
            # Persist the parse diagnostics (truncated previews only) so operators can
            # inspect what text/JSON was ignored when models emit mixed outputs.
            if isinstance(trajectory.metadata, MutableMapping):
                history = trajectory.metadata.get("llm_parse_extras")
                if not isinstance(history, list):
                    history = []
                    trajectory.metadata["llm_parse_extras"] = history
                # Avoid bloating trajectory storage: keep only small candidate fields.
                entry = {
                    "attempt": attempt,
                    **{k: v for k, v in parse_debug.items() if k != "other_actions"},
                }
                if isinstance(other_actions, list):
                    entry["other_actions"] = other_actions
                history.append(entry)
                # Cap history to avoid unbounded growth in long runs.
                if len(history) > 10:
                    del history[: len(history) - 10]
            # Emit a structured event for UIs/logs (only when there is something to report).
            ignored_len = parse_debug.get("ignored_text_len")
            other_count = parse_debug.get("other_json_count")
            if (
                planner._event_callback is not None
                and (
                    (isinstance(ignored_len, int) and ignored_len > 0)
                    or (isinstance(other_count, int) and other_count > 0)
                )
            ):
                # Keep SSE payload small: exclude raw candidate args.
                event_debug = {k: v for k, v in parse_debug.items() if k != "other_actions"}
                planner._emit_event(
                    PlannerEvent(
                        event_type="llm_parse_extras",
                        ts=planner._time_source(),
                        trajectory_step=len(trajectory.steps),
                        extra={
                            **event_debug,
                            "attempt": attempt,
                        },
                    )
                )

        # Log successful parse with args info for finish actions
        if action.next_node == "final_response":
            has_answer = "answer" in action.args or "raw_answer" in action.args
            # Use INFO level if answer is missing (helps debug repair triggers)
            # Also log full raw LLM output so we can see exactly what the model returned
            log_level = logging.INFO if not has_answer else logging.DEBUG
            extra: dict[str, Any] = {
                "args_keys": list(action.args.keys()),
                "raw_answer_present": "raw_answer" in action.args,
                "answer_present": "answer" in action.args,
            }
            if not has_answer:
                # Log full raw response (truncated at 2000 chars) to help debug
                extra["raw_llm_output"] = raw[:2000] if len(raw) > 2000 else raw
            logger.log(log_level, "finish_action_parsed", extra=extra)
        _consume_steering_inputs()
        return action

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
                _consume_steering_inputs()
                return PlannerAction(
                    next_node="final_response",
                    args={"answer": extracted_answer, "raw_answer": extracted_answer},
                    thought="Extracted answer from malformed JSON",
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
        _consume_steering_inputs()
        return PlannerAction(
            next_node="final_response",
            args={"answer": last_raw, "raw_answer": last_raw},
            thought=error,
        )

    # Should not reach here, but return a fail-safe action
    _consume_steering_inputs()
    return PlannerAction(
        next_node="final_response",
        args={"answer": "LLM response parsing failed", "raw_answer": "LLM response parsing failed"},
        thought="Failed to parse LLM response",
    )
