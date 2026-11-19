"""Step execution for the React planner."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from . import prompts
from .llm import _coerce_llm_response, supports_callback_streaming
from .migration import normalize_action_with_debug
from .models import PlannerAction, PlannerEvent
from .react_utils import _serialize_validation_errors
from .streaming import _StreamingArgsExtractor, _StreamingThoughtExtractor
from .validation_repair import _salvage_action_payload

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .guardrails.models import GuardrailDecision, RetrySpec
    from .react import ReactPlanner
    from .trajectory import Trajectory

logger = logging.getLogger("penguiflow.planner")


def _prepend_system_message(
    messages: list[dict[str, str]], content: str
) -> list[dict[str, str]]:
    """Insert a system message before the first non-system message."""
    patched: list[dict[str, str]] = []
    inserted = False
    for msg in messages:
        if not inserted and msg.get("role") != "system":
            patched.append({"role": "system", "content": content})
            inserted = True
        patched.append(msg)
    if not inserted:
        patched.append({"role": "system", "content": content})
    return patched


@dataclass(slots=True)
class _StepState:
    """Messages and errors carried across a step's repair attempts."""

    base_messages: list[dict[str, str]]
    messages: list[dict[str, str]]
    had_pending_steering: bool
    last_error: str | None = None
    last_raw: str | None = None

    def consume_steering_inputs(self, trajectory: Trajectory) -> None:
        """Clear the steering inputs rendered into this step's messages."""
        if self.had_pending_steering:
            trajectory.steering_inputs.clear()


class _ActionStream:
    """Streams one LLM attempt's thought, answer and reasoning chunks."""

    def __init__(
        self, planner: ReactPlanner, trajectory: Trajectory, action_seq: int
    ) -> None:
        """Stream chunks of ``trajectory``'s step ``action_seq``."""
        self._planner = planner
        self._trajectory = trajectory
        self._action_seq = action_seq
        # Detect finish actions and stream their args content.
        self._args = _StreamingArgsExtractor()
        self._thought = _StreamingThoughtExtractor()

    def _enqueue(self, extra: dict[str, Any]) -> None:
        self._planner.enqueue_llm_stream_chunk(
            self._trajectory,
            {
                "ts": self._planner.time_source(),
                "trajectory_step": len(self._trajectory.steps),
                "extra": extra,
            },
        )

    def start(self) -> None:
        """Open the step's thinking stream (when anyone is listening)."""
        if self._planner.event_callback is None:
            return
        self._enqueue(
            {
                "text": "",
                "done": False,
                "phase": "action",
                "channel": "thinking",
                "action_seq": self._action_seq,
            }
        )

    def emit_chunk(self, text: str, done: bool) -> None:
        """Route one streamed output chunk to the thinking/answer channels."""
        thought_chars = self._thought.feed(text)
        if thought_chars:
            self._enqueue(
                {
                    "text": "".join(thought_chars),
                    "done": False,
                    "phase": "observation",
                    "channel": "thinking",
                }
            )
        # Emit args content as "answer" phase for real-time display, batching
        # small chars into reasonable chunks for efficiency.
        args_chars = self._args.feed(text)
        if args_chars:
            self._enqueue(
                {
                    "text": "".join(args_chars),
                    "done": False,
                    "phase": "answer",
                    "channel": "answer",
                    "action_seq": self._action_seq,
                }
            )
        # Emit done signal when LLM finishes and it was a finish action
        if done and self._args.is_finish_action:
            self._enqueue(
                {
                    "text": "",
                    "done": True,
                    "phase": "answer",
                    "channel": "answer",
                    "action_seq": self._action_seq,
                }
            )

    def emit_reasoning_chunk(self, text: str, done: bool) -> None:
        """Stream one reasoning chunk on the thinking channel."""
        if not text and not done:
            return
        self._enqueue(
            {
                "text": text,
                "done": done,
                "phase": "thinking",
                "channel": "thinking",
                "action_seq": self._action_seq,
            }
        )


async def step(planner: ReactPlanner, trajectory: Trajectory) -> PlannerAction:
    """Ask the LLM for the next action, repairing unparseable responses."""
    if getattr(planner, "tool_call_mode", "prompted") == "native":
        from .react_step_native import (
            emit_downgrade_once,
            resolve_native_eligibility,
            step_native,
        )

        ineligible_reason = resolve_native_eligibility(planner)
        if ineligible_reason is None:
            return await step_native(planner, trajectory)
        emit_downgrade_once(planner, trajectory, ineligible_reason)
        # Fall through to the prompted wire format below.

    state = await _init_step_state(planner, trajectory)
    for attempt in range(1, planner.repair_attempts + 1):
        action = await _attempt_step(planner, trajectory, state, attempt)
        if action is not None:
            return action
    state.consume_steering_inputs(trajectory)
    return _unparsed_response_action(planner, state.last_raw)


async def _init_step_state(
    planner: ReactPlanner, trajectory: Trajectory
) -> _StepState:
    """Build the step's messages, including any arg repair guidance."""
    had_pending_steering = bool(trajectory.steering_inputs)
    base_messages = list(await planner.build_messages(trajectory))
    arg_repair_message: str | None = trajectory.metadata.pop(
        "arg_repair_message", None
    )
    if arg_repair_message:
        base_messages = _prepend_system_message(
            base_messages, arg_repair_message
        )
    messages = list(base_messages)

    # DEBUG_REMOVE: Log message structure being sent to LLM
    logger.debug(
        "DEBUG_step_messages",
        extra={
            "message_count": len(messages),
            "message_roles": [m.get("role") for m in messages],
            "last_message_preview": messages[-1].get("content", "")[:500]
            if messages
            else "none",
            "has_arg_repair": arg_repair_message is not None,
        },
    )
    return _StepState(
        base_messages=base_messages,
        messages=messages,
        had_pending_steering=had_pending_steering,
    )


def _step_response_format(planner: ReactPlanner) -> Mapping[str, Any] | None:
    """Return the response format to request for the action."""
    response_format: Mapping[str, Any] | None = planner.response_format
    if response_format is None and getattr(
        planner.client, "expects_json_schema", False
    ):
        response_format = planner.action_schema
    return response_format


def _stream_allowed(
    planner: ReactPlanner, response_format: Mapping[str, Any] | None
) -> bool:
    """Whether to stream this attempt's output.

    The native LLM adapter supports the same callback-based streaming contract
    as the LiteLLM client (and the fallback wrappers that delegate to either).
    The gating stays here so callers can enable or disable streaming
    consistently via ``stream_final_response``.
    """
    return bool(
        planner.stream_final_response
        and supports_callback_streaming(planner.client)
        and (
            response_format is None
            or response_format.get("type") in ("json_object", "json_schema")
        )
    )


def _guardrail_stop_action(
    decision: GuardrailDecision, thought: str
) -> PlannerAction:
    """Build the final action that ends the run on a guardrail STOP."""
    stop_message = decision.stop.user_message if decision.stop else None
    return PlannerAction(
        next_node="final_response",
        args={"answer": stop_message or "Unable to complete the request."},
        thought=f"{thought}:{decision.rule_id}",
    )


def _log_guardrail_decision(
    planner: ReactPlanner, decision: GuardrailDecision
) -> None:
    """Log a guardrail decision (and, when not enforced, its shadow)."""
    logger.info(
        "guardrail_decision",
        extra={
            "action": decision.action.value,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "enforced": planner.guardrails_enforced(),
        },
    )
    if not planner.guardrails_enforced():
        logger.info(
            "guardrail_decision_shadow",
            extra={
                "action": decision.action.value,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
            },
        )


def _apply_guardrail_retry(
    planner: ReactPlanner,
    trajectory: Trajectory,
    state: _StepState,
    retry: RetrySpec,
    *,
    rule_id: str,
) -> None:
    """Prepend the guardrail's corrective message while retries remain."""
    attempts = int(trajectory.metadata.get("guardrail_retry_count", 0))
    if attempts >= retry.max_attempts:
        return
    trajectory.metadata["guardrail_retry_count"] = attempts + 1
    if retry.corrective_message:
        state.messages = _prepend_system_message(
            list(state.base_messages), retry.corrective_message
        )
    if planner.event_callback is not None:
        planner.emit_event(
            PlannerEvent(
                event_type="guardrail_retry",
                ts=planner.time_source(),
                trajectory_step=len(trajectory.steps),
                extra={"rule_id": rule_id, "attempt": attempts + 1},
            )
        )


async def _guardrail_before_llm(
    planner: ReactPlanner, trajectory: Trajectory, state: _StepState
) -> PlannerAction | None:
    """Evaluate the ``llm_before`` guardrail; return an action to stop."""
    latest_user = next(
        (
            msg.get("content")
            for msg in reversed(state.messages)
            if msg.get("role") == "user"
        ),
        trajectory.resume_user_input or trajectory.query,
    )
    decision = await planner.evaluate_guardrail(
        planner.build_guardrail_event(
            event_type="llm_before",
            text_content=str(latest_user or ""),
            trajectory=trajectory,
        )
    )
    if decision is None:
        return None
    _log_guardrail_decision(planner, decision)
    if not planner.guardrails_enforced():
        return None
    if decision.action.value == "STOP":
        return _guardrail_stop_action(decision, "guardrail_stop")
    if decision.action.value == "RETRY" and decision.retry is not None:
        _apply_guardrail_retry(
            planner,
            trajectory,
            state,
            decision.retry,
            rule_id=decision.rule_id,
        )
    return None


async def _complete_action(
    planner: ReactPlanner,
    trajectory: Trajectory,
    state: _StepState,
    *,
    response_format: Mapping[str, Any] | None,
    stream: _ActionStream,
) -> str | tuple[str, float]:
    """Request the next action from the LLM, streaming when allowed."""
    stream_allowed = _stream_allowed(planner, response_format)
    on_stream_chunk = stream.emit_chunk if stream_allowed else None
    try:
        client = planner.client
        if supports_callback_streaming(client) and getattr(
            planner, "use_native_reasoning", True
        ):
            return await client.complete(
                messages=state.messages,
                response_format=response_format,
                stream=stream_allowed,
                on_stream_chunk=on_stream_chunk,
                on_reasoning_chunk=stream.emit_reasoning_chunk,
            )
        return await client.complete(
            messages=state.messages,
            response_format=response_format,
            stream=stream_allowed,
            on_stream_chunk=on_stream_chunk,
        )
    finally:
        await planner.finalize_guardrail_stream()
        if planner.event_callback is not None:
            planner.emit_event(
                PlannerEvent(
                    event_type="llm_stream_chunk",
                    ts=planner.time_source(),
                    trajectory_step=len(trajectory.steps),
                    extra={
                        "text": "",
                        "done": True,
                        "phase": "action",
                        "channel": "thinking",
                    },
                )
            )


async def _attempt_step(
    planner: ReactPlanner,
    trajectory: Trajectory,
    state: _StepState,
    attempt: int,
) -> PlannerAction | None:
    """Run one LLM attempt; return ``None`` when it needs a repair retry."""
    planner.clear_guardrail_stream_decision()
    if state.last_error is not None:
        state.messages = _prepend_system_message(
            list(state.base_messages),
            prompts.render_repair_message(state.last_error),
        )
    stream = _ActionStream(planner, trajectory, planner.action_seq)
    stream.start()
    if planner.guardrails_enabled():
        stop = await _guardrail_before_llm(planner, trajectory, state)
        if stop is not None:
            return stop
    llm_result = await _complete_action(
        planner,
        trajectory,
        state,
        response_format=_step_response_format(planner),
        stream=stream,
    )
    raw, cost = _coerce_llm_response(llm_result)
    decision = planner.guardrail_stream_decision
    if (
        decision is not None
        and planner.guardrails_enforced()
        and decision.action.value == "STOP"
    ):
        return _guardrail_stop_action(decision, "guardrail_stream_stop")
    state.last_raw = raw
    planner.cost_tracker.record_main_call(cost)

    # DEBUG_REMOVE: Log raw LLM response for troubleshooting (DEBUG level)
    logger.debug(
        "DEBUG_llm_raw_response",
        extra={
            "attempt": attempt,
            "response_len": len(raw),
            "response_preview": raw[:1000] if len(raw) > 1000 else raw,
        },
    )
    return _parse_action(planner, trajectory, state, raw=raw, attempt=attempt)


def _parse_action(
    planner: ReactPlanner,
    trajectory: Trajectory,
    state: _StepState,
    *,
    raw: str,
    attempt: int,
) -> PlannerAction | None:
    """Parse the LLM output into an action; ``None`` means retry."""
    try:
        action, parse_debug = normalize_action_with_debug(raw)
    except ValidationError as exc:
        # Checked before ValueError, which ValidationError subclasses.
        salvaged = _salvage_action_payload(raw)
        will_retry = salvaged is None and attempt < planner.repair_attempts
        planner.record_invalid_response(
            trajectory,
            attempt=attempt,
            raw=raw,
            error=exc,
            salvage_action=salvaged,
            will_retry=will_retry,
        )
        if salvaged is None:
            state.last_error = _serialize_validation_errors(exc)
            return None
        logger.info(
            "planner_action_salvaged",
            extra={"errors": _serialize_validation_errors(exc)},
        )
        state.consume_steering_inputs(trajectory)
        return salvaged
    except ValueError as exc:
        # Unparseable JSON (e.g. weak model emitted non-JSON). Treat as
        # repairable.
        state.last_error = str(exc)
        return None

    # Attach raw LLM response for debugging (excluded from serialization)
    action.raw_llm_response = raw
    if parse_debug is not None:
        _record_parse_extras(
            planner, trajectory, action, parse_debug, attempt=attempt
        )
    if action.next_node == "final_response":
        _log_finish_action(action, raw)
    state.consume_steering_inputs(trajectory)
    return action


def _record_parse_extras(
    planner: ReactPlanner,
    trajectory: Trajectory,
    action: PlannerAction,
    parse_debug: Mapping[str, Any],
    *,
    attempt: int,
) -> None:
    """Keep the text and JSON the parser ignored in mixed LLM outputs."""
    other_actions = parse_debug.get("other_actions")
    if isinstance(other_actions, list) and other_actions:
        # Attach candidates to the action for runtime fallbacks/batched
        # execution.
        action.alternate_actions = [
            item for item in other_actions if isinstance(item, dict)
        ]
        # Track repeated multi-action emission across runs to inject
        # guidance.
        planner.multi_action_history_count += 1
    # Persist the parse diagnostics (truncated previews only) so operators can
    # inspect what text/JSON was ignored when models emit mixed outputs.
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
    _emit_parse_extras_event(planner, trajectory, parse_debug, attempt=attempt)


def _emit_parse_extras_event(
    planner: ReactPlanner,
    trajectory: Trajectory,
    parse_debug: Mapping[str, Any],
    *,
    attempt: int,
) -> None:
    """Emit ``llm_parse_extras`` when the parser ignored anything."""
    ignored_len = parse_debug.get("ignored_text_len")
    other_count = parse_debug.get("other_json_count")
    if planner.event_callback is None or not (
        (isinstance(ignored_len, int) and ignored_len > 0)
        or (isinstance(other_count, int) and other_count > 0)
    ):
        return
    # Keep SSE payload small: exclude raw candidate args.
    event_debug = {
        k: v for k, v in parse_debug.items() if k != "other_actions"
    }
    planner.emit_event(
        PlannerEvent(
            event_type="llm_parse_extras",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={**event_debug, "attempt": attempt},
        )
    )


def _log_finish_action(action: PlannerAction, raw: str) -> None:
    """Log a parsed finish action, louder when its answer is missing."""
    has_answer = "answer" in action.args or "raw_answer" in action.args
    # Use INFO level if answer is missing (helps debug repair triggers), with
    # the raw LLM output so we can see exactly what the model returned.
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


def _unparsed_response_action(
    planner: ReactPlanner, last_raw: str | None
) -> PlannerAction:
    """Finish with whatever the unparseable responses still contain."""
    if last_raw is None:
        # Should not reach here, but return a fail-safe action
        return PlannerAction(
            next_node="final_response",
            args={
                "answer": "LLM response parsing failed",
                "raw_answer": "LLM response parsing failed",
            },
            thought="Failed to parse LLM response",
        )
    # Try to extract raw_answer/answer content using regex before naive
    # truncation. This handles cases where the JSON is malformed but
    # raw_answer is readable.
    extracted_answer = _extract_answer_text(last_raw)
    if extracted_answer:
        logger.info(
            "planner_fallback_answer_extracted",
            extra={"length": len(extracted_answer)},
        )
        return PlannerAction(
            next_node="final_response",
            args={
                "answer": extracted_answer,
                "raw_answer": extracted_answer,
            },
            thought="Extracted answer from malformed JSON",
        )

    # If no answer extracted, fall back to truncation
    max_chars = 1000
    error = (
        "LLM response could not be parsed after "
        f"{planner.repair_attempts} attempts."
    )
    last_raw = last_raw[:max_chars] if len(last_raw) > max_chars else last_raw
    logger.warning(
        "planner_action_parse_failed",
        extra={
            "error": error,
            "raw_preview": last_raw,
        },
    )
    return PlannerAction(
        next_node="final_response",
        args={"answer": last_raw, "raw_answer": last_raw},
        thought=error,
    )


def _extract_answer_text(raw: str) -> str | None:
    """Return the unescaped ``raw_answer``/``answer`` string in ``raw``."""
    # Look for "raw_answer": "..." or "answer": "..." pattern
    answer_match = re.search(
        r'"(?:raw_answer|answer)"\s*:\s*"((?:[^"\\]|\\.)*)',
        raw,
        re.DOTALL,
    )
    if not answer_match:
        return None
    # Unescape the content
    return (
        answer_match.group(1)
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )
