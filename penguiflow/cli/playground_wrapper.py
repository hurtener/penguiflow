"""Agent wrappers used by the playground backend."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from penguiflow.planner import (
    PlannerEvent,
    PlannerEventCallback,
    PlannerFinish,
    PlannerPause,
    ReactPlanner,
    Trajectory,
)

from .playground_state import PlaygroundStateStore


@dataclass
class ChatResult:
    """Normalized chat response returned by agent wrappers."""

    answer: str | None
    trace_id: str
    session_id: str
    metadata: dict[str, Any] | None = None
    pause: dict[str, Any] | None = None


class AgentWrapper(Protocol):
    """Common interface exposed to the FastAPI layer."""

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
        event_consumer: Callable[[PlannerEvent, str | None], None] | None = None,
        trace_id_hint: str | None = None,
    ) -> ChatResult: ...

    async def shutdown(self) -> None: ...


class _EventRecorder:
    """Buffers planner events and persists them once the trace is known."""

    def __init__(self, state_store: PlaygroundStateStore | None) -> None:
        self._state_store = state_store
        self._buffer: list[PlannerEvent] = []

    def callback(
        self,
        *,
        trace_id_supplier: Callable[[], str | None] | None = None,
        event_consumer: Callable[[PlannerEvent, str | None], None] | None = None,
    ) -> PlannerEventCallback | None:
        if self._state_store is None and event_consumer is None:
            return None

        def _record(event: PlannerEvent) -> None:
            self._buffer.append(event)
            trace_id = trace_id_supplier() if trace_id_supplier else None
            if event_consumer:
                event_consumer(event, trace_id)

        return _record

    async def persist(self, trace_id: str) -> None:
        if self._state_store is None:
            self._buffer.clear()
            return
        if not self._buffer:
            return
        events = list(self._buffer)
        self._buffer.clear()
        for event in events:
            await self._state_store.save_event(trace_id, event)


def _combine_callbacks(
    existing: PlannerEventCallback | None, new: PlannerEventCallback | None
) -> PlannerEventCallback | None:
    if existing is None:
        return new
    if new is None:
        return existing

    def _combined(event: PlannerEvent) -> None:
        existing(event)
        new(event)

    return _combined


def _normalise_metadata(metadata: Any) -> dict[str, Any] | None:
    if metadata is None:
        return None
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return {"value": metadata}


def _extract_from_dict(data: Mapping[str, Any]) -> str | None:
    """Extract answer from a dict using common answer keys."""
    for key in ("raw_answer", "answer", "text", "content", "message", "greeting", "response", "result"):
        if key in data:
            value = data[key]
            return str(value) if value is not None else None
    return None


def _normalise_answer(payload: Any) -> str | None:
    """Extract the human-readable answer from a planner payload.

    Per RFC_STRUCTURED_PLANNER_OUTPUT, FinalPayload.raw_answer is guaranteed
    to be a string by the ReactPlanner. This function handles various payload
    formats for backward compatibility.
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        # Check for parallel execution result with branches
        if "branches" in payload and isinstance(payload["branches"], list):
            for branch in payload["branches"]:
                if isinstance(branch, Mapping) and "observation" in branch:
                    obs = branch["observation"]
                    if isinstance(obs, Mapping):
                        result = _extract_from_dict(obs)
                        if result is not None:
                            return result
                    elif obs is not None:
                        return str(obs)
        # Standard path: extract from common answer keys (raw_answer first)
        result = _extract_from_dict(payload)
        if result is not None:
            return result
    for attr in ("raw_answer", "answer", "text", "content", "message", "greeting", "response", "result"):
        if hasattr(payload, attr):
            value = getattr(payload, attr)
            return str(value) if value is not None else None
    return str(payload)


def _build_trajectory(
    query: str,
    session_id: str,
    trace_id: str,
    metadata: Mapping[str, Any] | None,
    llm_context: Mapping[str, Any] | None,
    tool_context: Mapping[str, Any] | None = None,
) -> Trajectory | None:
    if metadata is None:
        return None
    steps = metadata.get("steps")
    if not isinstance(steps, list):
        return None
    payload: dict[str, Any] = {
        "query": query,
        "llm_context": dict(llm_context or {}),
        "tool_context": {
            **(dict(tool_context or {})),
            "session_id": session_id,
            "trace_id": trace_id,
        },
        "steps": steps,
        "hint_state": {},
    }
    if "artifacts" in metadata:
        payload["artifacts"] = metadata["artifacts"]
    if "sources" in metadata:
        payload["sources"] = metadata["sources"]
    if "summary" in metadata and metadata["summary"] is not None:
        payload["summary"] = metadata["summary"]
    return Trajectory.from_serialised(payload)


class PlannerAgentWrapper:
    """Adapter for bare planners returned by build_planner()."""

    def __init__(
        self,
        planner: ReactPlanner,
        *,
        state_store: PlaygroundStateStore | None = None,
        tool_context_defaults: Mapping[str, Any] | None = None,
    ) -> None:
        self._planner = planner
        self._state_store = state_store
        self._event_recorder = _EventRecorder(state_store)
        self._tool_context_defaults = dict(tool_context_defaults or {})

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
        event_consumer: Callable[[PlannerEvent, str | None], None] | None = None,
        trace_id_hint: str | None = None,
    ) -> ChatResult:
        llm_context = dict(llm_context or {})
        trace_id = trace_id_hint or secrets.token_hex(8)

        def _trace_id_supplier() -> str:
            return trace_id

        callback = self._event_recorder.callback(
            trace_id_supplier=_trace_id_supplier,
            event_consumer=event_consumer,
        )
        original_callback = getattr(self._planner, "_event_callback", None)
        if callback is not None:
            self._planner._event_callback = _combine_callbacks(original_callback, callback)

        try:
            merged_tool_context = {
                **self._tool_context_defaults,
                **dict(tool_context or {}),
                "session_id": session_id,
                "trace_id": trace_id,
            }
            result = await self._planner.run(
                query=query,
                llm_context=llm_context,
                tool_context=merged_tool_context,
            )
        finally:
            if callback is not None:
                self._planner._event_callback = original_callback

        await self._event_recorder.persist(trace_id)

        if isinstance(result, PlannerPause):
            pause_payload = {
                "reason": result.reason,
                "payload": result.payload,
                "resume_token": result.resume_token,
            }
            return ChatResult(
                answer=None,
                trace_id=trace_id,
                session_id=session_id,
                metadata={"pause": pause_payload},
                pause=pause_payload,
            )
        if not isinstance(result, PlannerFinish):
            raise RuntimeError("Planner did not finish execution")

        metadata = _normalise_metadata(getattr(result, "metadata", None))
        trajectory = _build_trajectory(query, session_id, trace_id, metadata, llm_context, merged_tool_context)
        if trajectory is not None and self._state_store is not None:
            await self._state_store.save_trajectory(trace_id, session_id, trajectory)

        # Extract answer from payload, falling back to thought in metadata
        answer = _normalise_answer(result.payload)
        if answer is None and metadata is not None:
            answer = metadata.get("thought")

        return ChatResult(
            answer=answer,
            trace_id=trace_id,
            session_id=session_id,
            metadata=metadata,
        )

    async def shutdown(self) -> None:
        """Planner wrappers do not own additional resources."""


class OrchestratorAgentWrapper:
    """Adapter for orchestrators exposing an execute coroutine."""

    def __init__(
        self,
        orchestrator: Any,
        *,
        state_store: PlaygroundStateStore | None = None,
        tenant_id: str = "playground-tenant",
        user_id: str = "playground-user",
    ) -> None:
        self._orchestrator = orchestrator
        self._state_store = state_store
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._event_recorder = _EventRecorder(state_store)

    async def chat(
        self,
        query: str,
        *,
        session_id: str,
        llm_context: Mapping[str, Any] | None = None,
        tool_context: Mapping[str, Any] | None = None,
        event_consumer: Callable[[PlannerEvent, str | None], None] | None = None,
        trace_id_hint: str | None = None,
    ) -> ChatResult:
        ctx = dict(llm_context or {})
        tool_ctx = dict(tool_context or {})
        planner = getattr(self._orchestrator, "_planner", None)
        trace_holder: dict[str, str | None] = {"id": trace_id_hint}

        def _trace_id_supplier() -> str | None:
            if trace_holder["id"]:
                return trace_holder["id"]
            return _planner_trace_id(planner)

        callback = self._event_recorder.callback(
            trace_id_supplier=_trace_id_supplier,
            event_consumer=event_consumer,
        )
        original_callback = None
        if planner is not None and callback is not None:
            original_callback = getattr(planner, "_event_callback", None)
            planner._event_callback = _combine_callbacks(original_callback, callback)

        try:
            response = await self._orchestrator.execute(
                query=query,
                tenant_id=tool_ctx.get("tenant_id", self._tenant_id),
                user_id=tool_ctx.get("user_id", self._user_id),
                session_id=session_id,
            )
        finally:
            if planner is not None and callback is not None:
                planner._event_callback = original_callback

        trace_id = _get_attr(response, "trace_id") or _trace_id_supplier() or secrets.token_hex(8)
        trace_holder["id"] = trace_id
        await self._event_recorder.persist(trace_id)

        metadata = _normalise_metadata(_get_attr(response, "metadata"))
        trajectory = _build_trajectory(query, session_id, trace_id, metadata, ctx, tool_ctx)
        if trajectory is not None and self._state_store is not None:
            await self._state_store.save_trajectory(trace_id, session_id, trajectory)

        return ChatResult(
            answer=_normalise_answer(_get_attr(response, "answer")),
            trace_id=trace_id,
            session_id=session_id,
            metadata=metadata,
        )

    async def shutdown(self) -> None:
        stop_fn = getattr(self._orchestrator, "stop", None)
        if stop_fn is not None:
            await stop_fn()


def _get_attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _planner_trace_id(planner: Any) -> str | None:
    trajectory = getattr(planner, "_active_trajectory", None)
    if trajectory is None:
        return None
    tool_ctx = getattr(trajectory, "tool_context", None)
    if isinstance(tool_ctx, Mapping):
        value = tool_ctx.get("trace_id")
        return str(value) if value is not None else None
    return None


__all__ = [
    "AgentWrapper",
    "ChatResult",
    "OrchestratorAgentWrapper",
    "PlannerAgentWrapper",
]
