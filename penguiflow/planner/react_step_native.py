"""Native tool-calling step for the React planner.

Provider-native tool calls replace the prompted ``{next_node, args}`` JSON
envelope as the WIRE FORMAT only — the result is the same ``PlannerAction``
the runtime already executes (decision-shape invariance, transport analysis
Decision 9 / Phase 5).

Mapping (Phase 5 design decisions D5.1/D5.5):
- zero tool calls   → ``final_response`` with the content as the answer
- one tool call     → direct tool action (args parsed from provider-validated
  JSON)
- N tool calls      → ``parallel`` plan with one step per call (no join in v1)
- content alongside an intermediate tool call → ``action.thought`` (preamble)

Structured finishing (``final_response_model``): the one deliberate exception
to "no text alongside tool calls". When finishing, the model writes the answer
as plain text AND calls the synthetic ``final_response`` tool carrying only the
``structured`` object, in the same turn. We keep the streamed text as the
answer and read ``structured`` from the call — so the answer still streams
while the payload validates first-pass. See ``build_native_tools`` /
``FINISH_TOOL_NAME``.

Streaming (D5.3 revised): content deltas stream token-by-token on the
    ``answer``
channel (parity with prompted mode). An *intermediate* turn that streams a
disobedient preamble before a tool call closes the answer stream with
``superseded: True`` and re-emits the text on ``thinking``; a structured finish
turn does not (its text is the real answer).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..llm.protocol import NativeToolCallResult
from ..llm.types import ToolCallPart, ToolSpec
from .llm import _coerce_llm_response
from .models import NativeToolsLLMClient, PlannerAction, PlannerEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .react import ReactPlanner
    from .trajectory import Trajectory

logger = logging.getLogger("penguiflow.planner")

_DOWNGRADE_FLAG = "native_tool_call_downgraded"

# Reserved native tool name used to deliver a schema-validated final answer
# when the planner is configured with ``final_response_model``. The model
# "calls" this synthetic tool to finish, so the structured payload arrives as
# provider-validated function-call args (no extra corrective turn needed). See
# ``docs/planner/structured-final-response.md``.
FINISH_TOOL_NAME = "final_response"


def resolve_native_eligibility(planner: ReactPlanner) -> str | None:
    """Return None when native mode can run, else a human-readable reason."""
    client = planner.client
    if not isinstance(client, NativeToolsLLMClient):
        return (
            f"client {type(client).__name__} does not expose "
            "complete_with_tools"
        )

    model = getattr(client, "_model", None)
    if isinstance(model, str) and model:
        from ..llm.profiles import get_profile

        profile = get_profile(model)
        if not getattr(profile, "supports_tools", True):
            return f"model '{model}' does not support tools"
        if not getattr(profile, "supports_native_tool_calls", True):
            return f"model '{model}' route does not support native tool calls"
    return None


def emit_downgrade_once(
    planner: ReactPlanner, trajectory: Trajectory, reason: str
) -> None:
    """Emit the downgrade event once per run (the condition is run-stable)."""
    if trajectory.metadata.get(_DOWNGRADE_FLAG):
        return
    trajectory.metadata[_DOWNGRADE_FLAG] = reason
    logger.warning("tool_call_mode_downgraded", extra={"reason": reason})
    planner.emit_event(
        PlannerEvent(
            event_type="tool_call_mode_downgraded",
            ts=planner.time_source(),
            trajectory_step=len(trajectory.steps),
            extra={"from": "native", "to": "prompted", "reason": reason},
        )
    )


_TOOL_NAME_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")


def build_native_tools(
    planner: ReactPlanner,
) -> tuple[list[ToolSpec], dict[str, str]]:
    """Declare the currently visible catalog as native tool specs.

    Catalog names may contain characters that provider function-name patterns
    reject (e.g. MCP tools like ``youtube.download_video`` vs the
    ``^[a-zA-Z0-9_-]{1,128}$`` rule — found live on Databricks). Each tool is
    declared under a wire-safe alias; the returned mapping translates the
    provider's calls back to real catalog names.
    """
    tools: list[ToolSpec] = []
    alias_to_name: dict[str, str] = {}
    used: set[str] = set()
    # When structured final answers are enabled, reserve the finish-tool name
    # so a real catalog tool can never collide with it.
    structured_schema = getattr(planner, "final_response_schema", None)
    if structured_schema is not None:
        used.add(FINISH_TOOL_NAME)
    for spec in planner.specs:
        alias = _TOOL_NAME_UNSAFE_RE.sub("_", spec.name)[:128] or "tool"
        if alias in used:
            base = alias[:120]
            suffix = 2
            while f"{base}_{suffix}" in used:
                suffix += 1
            alias = f"{base}_{suffix}"
        used.add(alias)
        alias_to_name[alias] = spec.name
        tools.append(
            ToolSpec(
                name=alias,
                description=spec.desc,
                json_schema=spec.args_model.model_json_schema(),
            )
        )
    if structured_schema is not None:
        tools.append(
            ToolSpec(
                name=FINISH_TOOL_NAME,
                description=(
                    "FINISH the run. Write your complete "
                    "human-readable answer as "
                    "plain text in the same turn (it streams to the user), "
                    "and call "
                    "this with 'structured' set to the matching schema "
                    "object. Use "
                    "the optional 'answer' field only if you did not write "
                    "the answer "
                    "as plain text. Do not call this alongside other tools."
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "structured": structured_schema,
                        "answer": {
                            "type": "string",
                            "description": (
                                "Fallback answer; normally write the "
                                "answer as plain "
                                "text instead so it streams."
                            ),
                        },
                    },
                    "required": ["structured"],
                },
            )
        )
    return tools, alias_to_name


def _parse_tool_args(arguments_json: str, *, tool_name: str) -> dict[str, Any]:
    """Parse provider-validated args defensively (D5.5).

    A malformed payload degrades to ``{}`` so the runtime's existing
    arg-validation repair handles it instead of crashing the step.
    """
    if not arguments_json:
        return {}
    try:
        parsed = json.loads(arguments_json)
    except (TypeError, ValueError):
        logger.warning(
            "native_tool_args_unparseable",
            extra={"tool": tool_name, "preview": arguments_json[:200]},
        )
        return {}
    if isinstance(parsed, dict):
        return parsed
    logger.warning(
        "native_tool_args_not_object",
        extra={"tool": tool_name, "type": type(parsed).__name__},
    )
    return {}


class _NativeStream:
    """Streams one native-mode turn on the answer and thinking channels.

    D5.3 (revised): the native-mode prompt forbids text alongside tool calls,
    so content deltas stream OPTIMISTICALLY to the answer channel,
    token-by-token, exactly like prompted mode. If the model disobeys and the
    turn turns out to be a tool call, the answer stream is closed with
    ``superseded: True`` and the text is re-emitted on the thinking channel
    so traces stay coherent (consumers that ignore the marker degrade to
    briefly showing preamble text — never broken state).
    """

    def __init__(
        self, planner: ReactPlanner, trajectory: Trajectory, action_seq: int
    ) -> None:
        """Stream chunks of ``trajectory``'s step ``action_seq``."""
        self._planner = planner
        self._trajectory = trajectory
        # The runtime increments the action seq and emits step_start BEFORE
        # the step runs; chunks must carry the SAME seq or the UI answer gate
        # drops them.
        self.action_seq = action_seq
        self.answer_chunks_emitted = 0

    def enqueue(self, extra: dict[str, Any]) -> None:
        """Queue a chunk (guardrail redaction applies, as in prompted mode)."""
        self._planner.enqueue_llm_stream_chunk(
            self._trajectory,
            {
                "ts": self._planner.time_source(),
                "trajectory_step": len(self._trajectory.steps),
                "extra": {"action_seq": self.action_seq, **extra},
            },
        )

    def emit_chunk(self, text: str, _done: bool) -> None:
        """Stream a content delta on the answer channel.

        The provider's done flag is not forwarded: the answer stream is
        closed explicitly once the turn's outcome is known.
        """
        if not text:
            return
        self.answer_chunks_emitted += 1
        self.enqueue(
            {
                "text": text,
                "done": False,
                "phase": "answer",
                "channel": "answer",
            }
        )

    def emit_reasoning_chunk(self, text: str, done: bool) -> None:
        """Stream reasoning on the thinking channel (prompted-mode parity)."""
        if not text and not done:
            return
        self.enqueue(
            {
                "text": text,
                "done": done,
                "phase": "thinking",
                "channel": "thinking",
            }
        )

    def emit_text(self, text: str, *, channel: str) -> None:
        """Emit a whole text on the answer or thinking channel."""
        self.enqueue(
            {
                "text": text,
                "done": False,
                "phase": channel,
                "channel": channel,
            }
        )

    def finish_answer(self, answer: str) -> None:
        """Emit the answer unless it already streamed, then close it.

        Non-streaming providers hand back the whole answer at once; emitting
        it keeps the answer channel carrying the text.
        """
        if answer and self.answer_chunks_emitted == 0:
            self.emit_text(answer, channel="answer")
        self.close_answer(superseded=False)

    def close_answer(self, *, superseded: bool) -> None:
        """Close the answer stream, marking retracted text as superseded."""
        self.enqueue(
            {
                "text": "",
                "done": True,
                "phase": "answer",
                "channel": "answer",
                **({"superseded": True} if superseded else {}),
            }
        )


async def _native_messages(
    planner: ReactPlanner, trajectory: Trajectory
) -> list[dict[str, Any]]:
    """Build the step's messages, including any arg repair guidance.

    Arg-validation repair guidance is delivered via trajectory metadata by
    the runtime (same contract as the prompted step): consume it or it is
    lost.
    """
    base_messages = list(await planner.build_messages(trajectory))
    arg_repair_message = trajectory.metadata.pop("arg_repair_message", None)
    if arg_repair_message:
        base_messages.insert(
            0, {"role": "system", "content": arg_repair_message}
        )
    return base_messages


async def _complete_native(
    planner: ReactPlanner,
    messages: list[dict[str, Any]],
    tools: list[ToolSpec],
    stream: _NativeStream,
) -> tuple[str, list[ToolCallPart]]:
    """Run the native completion; return its content and tool calls."""
    client = planner.client
    if not isinstance(client, NativeToolsLLMClient):
        msg = (
            f"client {type(client).__name__} does not expose "
            "complete_with_tools"
        )
        raise TypeError(msg)
    stream_allowed = bool(planner.stream_final_response)
    use_reasoning = getattr(planner, "use_native_reasoning", True)
    # Typed as object: a JSONLLMClient-shaped return is handled below.
    result: object = await client.complete_with_tools(
        messages=messages,
        tools=tools,
        stream=stream_allowed,
        on_stream_chunk=stream.emit_chunk if stream_allowed else None,
        on_reasoning_chunk=stream.emit_reasoning_chunk
        if (stream_allowed and use_reasoning)
        else None,
    )
    tool_calls: list[ToolCallPart]
    if isinstance(result, NativeToolCallResult):
        content = result.content
        cost = result.cost
        tool_calls = list(result.tool_calls)
    else:
        # Defensive: a JSONLLMClient-shaped return slipped through.
        content, cost = _coerce_llm_response(result)
        tool_calls = []
    planner.cost_tracker.record_main_call(cost or 0.0)
    return (content or "").strip(), tool_calls


def _structured_finish_action(
    tool_calls: list[ToolCallPart],
    content: str,
    stream: _NativeStream | None,
    *,
    action_seq: int,
) -> PlannerAction | None:
    """Map a ``final_response`` tool call to the structured final answer.

    A finishing turn legitimately streams the answer as plain text AND calls
    final_response with the structured object, so the streamed text is KEPT
    as the answer instead of being retracted. The tool carries only the
    machine-readable payload; the runtime's structured-final check validates
    it first try (no repair turn).
    """
    finish_call = next(
        (c for c in tool_calls if c.name == FINISH_TOOL_NAME), None
    )
    if finish_call is None:
        return None
    # Finishing ends the run, so any sibling calls in the same turn are
    # dropped. The prompt tells the model to call final_response alone; log
    # it so the rare violation is observable rather than silent.
    if len(tool_calls) > 1:
        logger.info(
            "native_finish_dropped_sibling_calls",
            extra={
                "dropped": [
                    c.name for c in tool_calls if c is not finish_call
                ],
                "action_seq": action_seq,
            },
        )
    finish_args = _parse_tool_args(
        finish_call.arguments_json, tool_name=finish_call.name
    )
    # Prefer the streamed plain-text answer; fall back to an answer carried
    # inside the tool call if the model put it there instead of as text.
    fallback = finish_args.get("answer")
    answer_text = content or (fallback if isinstance(fallback, str) else "")
    if stream is not None:
        stream.finish_answer(answer_text)
    final_args: dict[str, Any] = {
        "answer": answer_text,
        "raw_answer": answer_text,
    }
    if "structured" in finish_args:
        final_args["structured"] = finish_args["structured"]
    return PlannerAction(
        next_node="final_response",
        args=final_args,
        thought="",
        raw_llm_response=finish_call.arguments_json or None,
    )


def _retract_streamed_answer(stream: _NativeStream, content: str) -> None:
    """Move preamble text that streamed as an answer to thinking.

    This handles a model that disobeyed the no-preamble instruction on an
    intermediate tool turn.
    """
    if stream.answer_chunks_emitted == 0:
        return
    stream.close_answer(superseded=True)
    if content:
        stream.emit_text(content, channel="thinking")
    logger.info(
        "native_answer_stream_superseded",
        extra={
            "chars_streamed": len(content),
            "action_seq": stream.action_seq,
        },
    )


def _tool_calls_action(
    tool_calls: list[ToolCallPart],
    alias_to_name: Mapping[str, str],
    content: str,
) -> PlannerAction:
    """Map the turn's tool calls to one tool action or a parallel plan."""

    def real_name(call: ToolCallPart) -> str:
        return alias_to_name.get(call.name, call.name)

    if len(tool_calls) == 1:
        call = tool_calls[0]
        return PlannerAction(
            next_node=real_name(call),
            args=_parse_tool_args(call.arguments_json, tool_name=call.name),
            thought=content,
            raw_llm_response=content or None,
        )

    # D5.5: N>1 native tool calls map to the existing parallel plan (no join
    # v1).
    steps = [
        {
            "node": real_name(call),
            "args": _parse_tool_args(call.arguments_json, tool_name=call.name),
        }
        for call in tool_calls
    ]
    return PlannerAction(
        next_node="parallel",
        args={"steps": steps},
        thought=content,
        raw_llm_response=content or None,
    )


async def step_native(
    planner: ReactPlanner, trajectory: Trajectory
) -> PlannerAction:
    """Run one native-mode step: declare tools, map the reply to an action."""
    had_pending_steering = bool(trajectory.steering_inputs)
    messages = await _native_messages(planner, trajectory)
    tools, alias_to_name = build_native_tools(planner)
    stream = _NativeStream(planner, trajectory, planner.action_seq)
    content, tool_calls = await _complete_native(
        planner, messages, tools, stream
    )
    # Steering inputs were rendered into this step's messages; clear them so
    # they are not repeated on every subsequent step (prompted-step parity).
    if had_pending_steering:
        trajectory.steering_inputs.clear()
    streaming = stream if planner.stream_final_response else None

    if not tool_calls:
        # D5.1: content-only turn is the final answer. It already streamed
        # token-by-token on the answer channel; emit the full text only when
        # nothing streamed (non-streaming runs), then close the stream.
        if content and streaming is not None:
            streaming.finish_answer(content)
        return PlannerAction(
            next_node="final_response",
            args={"answer": content, "raw_answer": content},
            thought="",
            raw_llm_response=content,
        )

    # Structured final answer (final_response_model). Detect the synthetic
    # finish tool BEFORE the supersede logic below.
    if getattr(planner, "final_response_schema", None) is not None:
        finish = _structured_finish_action(
            tool_calls, content, streaming, action_seq=stream.action_seq
        )
        if finish is not None:
            return finish

    if streaming is not None:
        _retract_streamed_answer(streaming, content)
    return _tool_calls_action(tool_calls, alias_to_name, content)


__all__ = [
    "FINISH_TOOL_NAME",
    "build_native_tools",
    "emit_downgrade_once",
    "resolve_native_eligibility",
    "step_native",
]
