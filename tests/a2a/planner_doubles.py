"""Planner doubles for A2A server tests.

``A2AService`` drives a planner through ``run``/``resume``, a per-task
``SteeringInbox`` and the planner's ``event_callback`` (for answer streaming).
``ScriptedPlanner`` implements exactly that surface so protocol tests can
script agent behaviour without an LLM. Behaviour against a real
``ReactPlanner`` is covered in ``test_a2a_planner_service``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from penguiflow.planner import PlannerEvent, PlannerFinish, PlannerPause
from penguiflow_a2a import A2APlanner

if TYPE_CHECKING:
    from penguiflow.planner import ToolVisibilityPolicy
    from penguiflow.state import StateStore
    from penguiflow.steering import SteeringInbox


class ScriptedRun:
    """What a scripted handler sees for one ``run``/``resume`` call."""

    def __init__(
        self,
        planner: ScriptedPlanner,
        *,
        query: str,
        tool_context: Mapping[str, Any],
        steering: SteeringInbox | None,
        resume_token: str | None,
    ) -> None:
        """Capture one call's planner, query, context and steering."""
        self.planner = planner
        self.query = query
        self.tool_context = dict(tool_context)
        self.steering = steering
        self.resume_token = resume_token

    def emit_chunk(
        self, text: str, *, done: bool = False, action_seq: int = 0
    ) -> None:
        """Emit an answer chunk like ``stream_final_response`` does."""
        self.planner.emit(
            PlannerEvent(
                event_type="llm_stream_chunk",
                ts=0.0,
                trajectory_step=0,
                extra={
                    "text": text,
                    "done": done,
                    "phase": "answer",
                    "channel": "answer",
                    "action_seq": action_seq,
                },
            )
        )

    def pause(
        self, reason: str = "await_input", **payload: str
    ) -> PlannerPause:
        """Pause the run with ``reason`` and ``payload``."""
        token = f"resume-{len(self.planner.calls)}"
        return PlannerPause(reason=reason, payload=payload, resume_token=token)


Handler = Callable[[ScriptedRun], Awaitable[Any]]


class ScriptedPlanner(A2APlanner):
    """Planner double exposing the surface ``A2AService`` uses.

    The handler returns a ``PlannerFinish``/``PlannerPause``, or a plain value
    which is wrapped as ``PlannerFinish(reason="answer_complete",
    payload={"raw_answer": value})``.
    """

    def __init__(
        self,
        handler: Handler,
        *,
        event_callback: Callable[[PlannerEvent], None] | None = None,
        state_store: StateStore | None = None,
    ) -> None:
        """Store ``handler``, ``event_callback`` and ``state_store``."""
        self._handler = handler
        self.event_callback = event_callback
        self.state_store = state_store
        self.calls: list[dict[str, Any]] = []

    def emit(self, event: PlannerEvent) -> None:
        """Send ``event`` to the event callback, if any."""
        if self.event_callback is not None:
            self.event_callback(event)

    @override
    async def run(
        self,
        query: str,
        *,
        tool_context: Mapping[str, Any] | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
    ) -> PlannerFinish | PlannerPause:
        self.calls.append(
            {
                "kind": "run",
                "query": query,
                "tool_context": dict(tool_context or {}),
                "tool_visibility": tool_visibility,
            }
        )
        return await self._dispatch(
            query, tool_context or {}, steering, resume_token=None
        )

    @override
    async def resume(
        self,
        token: str,
        user_input: str | None = None,
        *,
        tool_context: Mapping[str, Any] | None = None,
        steering: SteeringInbox | None = None,
        tool_visibility: ToolVisibilityPolicy | None = None,
    ) -> PlannerFinish | PlannerPause:
        self.calls.append(
            {
                "kind": "resume",
                "token": token,
                "user_input": user_input,
                "tool_context": dict(tool_context or {}),
                "tool_visibility": tool_visibility,
            }
        )
        return await self._dispatch(
            user_input or "", tool_context or {}, steering, resume_token=token
        )

    async def _dispatch(
        self,
        query: str,
        tool_context: Mapping[str, Any],
        steering: SteeringInbox | None,
        *,
        resume_token: str | None,
    ) -> PlannerFinish | PlannerPause:
        run = ScriptedRun(
            self,
            query=query,
            tool_context=tool_context,
            steering=steering,
            resume_token=resume_token,
        )
        result = await self._handler(run)
        if isinstance(result, PlannerFinish | PlannerPause):
            return result
        return PlannerFinish(
            reason="answer_complete", payload={"raw_answer": result}
        )


def echo_planner(prefix: str = "echo:") -> ScriptedPlanner:
    """Build a planner that echoes the query."""

    async def handler(run: ScriptedRun) -> str:
        return f"{prefix}{run.query}"

    return ScriptedPlanner(handler)


def slow_planner(delay: float = 0.5, prefix: str = "slow:") -> ScriptedPlanner:
    """Build a planner that echoes the query after a delay."""

    async def handler(run: ScriptedRun) -> str:
        await asyncio.sleep(delay)
        return f"{prefix}{run.query}"

    return ScriptedPlanner(handler)


def streaming_planner(delay: float = 0.0) -> ScriptedPlanner:
    """Stream each query word as an answer chunk, then the full text."""

    async def handler(run: ScriptedRun) -> str:
        if delay:
            await asyncio.sleep(delay)
        words = run.query.split()
        for idx, word in enumerate(words):
            run.emit_chunk(
                word if idx == 0 else f" {word}", done=idx == len(words) - 1
            )
            await asyncio.sleep(0)
        return " ".join(words)

    return ScriptedPlanner(handler)


__all__ = [
    "ScriptedPlanner",
    "ScriptedRun",
    "echo_planner",
    "slow_planner",
    "streaming_planner",
]
