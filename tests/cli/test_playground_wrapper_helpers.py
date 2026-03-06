"""Tests for playground_wrapper helper functions."""

from __future__ import annotations

from typing import Any

import pytest

from penguiflow.cli.playground_wrapper import (
    OrchestratorAgentWrapper,
    PlannerAgentWrapper,
    _combine_callbacks,
    _EventRecorder,
    _extract_from_dict,
    _get_attr,
    _normalise_answer,
    _normalise_metadata,
    _planner_trace_id,
)
from penguiflow.planner import PlannerEvent, PlannerFinish, PlannerPause


class DummyPlanner:
    def __init__(self, *, run_result: Any, resume_result: Any | None = None) -> None:
        self._event_callback = None
        self._run_result = run_result
        self._resume_result = resume_result if resume_result is not None else run_result

    async def run(self, **_: Any) -> Any:
        return self._run_result

    async def resume(self, *_: Any, **__: Any) -> Any:
        return self._resume_result


class DummyOrchestrator:
    def __init__(self, *, response: Any, resume_response: Any | None = None) -> None:
        self._response = response
        self._resume_response = resume_response if resume_response is not None else response
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        self._initialized = True

    async def execute(self, **_: Any) -> Any:
        return self._response

    async def resume(self, *_: Any, **__: Any) -> Any:
        return self._resume_response

    async def stop(self) -> None:
        self._initialized = False


class CapturingOrchestrator:
    def __init__(self, *, response: Any) -> None:
        self._response = response
        self.last_tool_context: dict[str, Any] | None = None

    async def execute(
        self,
        query: str,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        tool_context: dict[str, Any] | None = None,
    ) -> Any:
        _ = (query, tenant_id, user_id, session_id)
        self.last_tool_context = dict(tool_context or {})
        return self._response

    async def resume(
        self,
        resume_token: str,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_input: str | None = None,
        tool_context: dict[str, Any] | None = None,
    ) -> Any:
        _ = (resume_token, tenant_id, user_id, session_id, user_input)
        self.last_tool_context = dict(tool_context or {})
        return self._response


def make_event(event_type: str = "step") -> PlannerEvent:
    return PlannerEvent(event_type=event_type, ts=1.0, trajectory_step=1)


class DummyPayload:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer


def test_event_recorder_callback_none_when_unused() -> None:
    recorder = _EventRecorder()
    assert recorder.callback() is None
    # Positive path: returns a callback when event_consumer is provided
    assert _EventRecorder().callback(event_consumer=lambda *_: None) is not None


def test_event_recorder_forwards_to_consumer() -> None:
    seen: list[tuple[PlannerEvent, str | None]] = []
    recorder = _EventRecorder()
    callback = recorder.callback(
        trace_id_supplier=lambda: "trace-1",
        event_consumer=lambda event, trace_id: seen.append((event, trace_id)),
    )
    assert callback is not None

    event = make_event()
    callback(event)
    assert seen == [(event, "trace-1")]


def test_combine_callbacks() -> None:
    events: list[str] = []

    def first(event: PlannerEvent) -> None:
        events.append(f"first:{event.event_type}")

    def second(event: PlannerEvent) -> None:
        events.append(f"second:{event.event_type}")

    assert _combine_callbacks(None, second) is second
    assert _combine_callbacks(first, None) is first

    combined = _combine_callbacks(first, second)
    assert combined is not None
    combined(make_event("step_complete"))
    assert events == ["first:step_complete", "second:step_complete"]


def test_normalise_metadata() -> None:
    assert _normalise_metadata(None) is None
    assert _normalise_metadata({"a": 1}) == {"a": 1}
    assert _normalise_metadata("value") == {"value": "value"}


def test_extract_from_dict() -> None:
    assert _extract_from_dict({"answer": "hi"}) == "hi"
    assert _extract_from_dict({"content": None}) is None
    assert _extract_from_dict({}) is None


def test_normalise_answer() -> None:
    assert _normalise_answer(None) is None
    assert _normalise_answer("ready") == "ready"
    assert _normalise_answer({"raw_answer": "ok"}) == "ok"
    assert _normalise_answer({"branches": [{"observation": {"answer": "branch"}}]}) == "branch"
    assert _normalise_answer({"branches": [{"observation": 123}]}) == "123"
    assert _normalise_answer(DummyPayload("attr")) == "attr"
    assert _normalise_answer(42) == "42"


@pytest.mark.asyncio
async def test_planner_agent_wrapper_pause_and_finish() -> None:
    pause = PlannerPause(reason="await_input", payload={"field": "value"}, resume_token="resume-1")
    wrapper = PlannerAgentWrapper(DummyPlanner(run_result=pause))
    result = await wrapper.chat("hi", session_id="session-1", trace_id_hint="trace-1")
    assert result.pause is not None
    assert result.pause["resume_token"] == "resume-1"

    finish = PlannerFinish(
        reason="answer_complete",
        payload=None,
        metadata={
            "steps": [{"action": {"thought": "plan", "next_node": "finish"}}],
            "thought": "fallback",
        },
    )
    wrapper = PlannerAgentWrapper(DummyPlanner(run_result=finish))
    result = await wrapper.chat("hi", session_id="session-1", trace_id_hint="trace-2")
    assert result.answer == "fallback"

    finish_no_steps = PlannerFinish(reason="answer_complete", payload="ok", metadata={"foo": "bar"})
    wrapper = PlannerAgentWrapper(DummyPlanner(run_result=finish_no_steps))
    result = await wrapper.chat("hi", session_id="session-1")
    assert result.answer == "ok"

    wrapper = PlannerAgentWrapper(DummyPlanner(run_result=object()))
    with pytest.raises(RuntimeError):
        await wrapper.chat("hi", session_id="session-1")


@pytest.mark.asyncio
async def test_planner_agent_wrapper_resume_finish() -> None:
    finish = PlannerFinish(reason="answer_complete", payload="done", metadata={})
    wrapper = PlannerAgentWrapper(DummyPlanner(run_result=finish, resume_result=finish))
    result = await wrapper.resume("resume-1", session_id="session-1", trace_id_hint="trace-3")
    assert result.answer == "done"
    assert result.trace_id == "trace-3"

    pause = PlannerPause(reason="await_input", payload={}, resume_token="resume-2")
    wrapper = PlannerAgentWrapper(DummyPlanner(run_result=pause, resume_result=pause))
    paused = await wrapper.resume("resume-2", session_id="session-1")
    assert paused.pause is not None


def test_get_attr_and_trace_id_helpers() -> None:
    assert _get_attr(None, "answer") is None
    assert _get_attr({"answer": "ok"}, "answer") == "ok"
    assert _get_attr(DummyPayload("attr"), "answer") == "attr"

    class DummyTrajectory:
        def __init__(self) -> None:
            self.tool_context = {"trace_id": "trace-9"}

    class DummyPlannerWithTrajectory:
        _active_trajectory = DummyTrajectory()

    assert _planner_trace_id(DummyPlannerWithTrajectory()) == "trace-9"

    class DummyPlannerNoTrace:
        class DummyTrajectory:
            tool_context = ["not", "mapping"]

        _active_trajectory = DummyTrajectory()

    assert _planner_trace_id(DummyPlannerNoTrace()) is None


@pytest.mark.asyncio
async def test_orchestrator_agent_wrapper_initialize_and_chat_pause() -> None:
    response = {"pause_token": "pause-1", "metadata": {"reason": "await_input", "payload": {"x": 1}}}
    orchestrator = DummyOrchestrator(response=response)
    wrapper = OrchestratorAgentWrapper(orchestrator)

    await wrapper.initialize()
    assert orchestrator._initialized is True

    result = await wrapper.chat("hi", session_id="session-1", trace_id_hint="trace-4")
    assert result.pause is not None
    assert result.pause["resume_token"] == "pause-1"

    resume_response = {"pause_token": "pause-2", "metadata": {"reason": "await_input", "payload": {}}}
    orchestrator = DummyOrchestrator(response=response, resume_response=resume_response)
    wrapper = OrchestratorAgentWrapper(orchestrator)
    paused = await wrapper.resume("pause-2", session_id="session-1", trace_id_hint="trace-5")
    assert paused.pause is not None

    orchestrator._initialized = True
    await wrapper.shutdown()
    assert orchestrator._initialized is False


@pytest.mark.asyncio
async def test_orchestrator_wrapper_forwards_tool_context_when_supported() -> None:
    orchestrator = CapturingOrchestrator(response={"answer": "ok", "trace_id": "t1", "metadata": {}})
    wrapper = OrchestratorAgentWrapper(
        orchestrator,
        tool_context_defaults={"task_service": "svc-1"},
        tenant_id="tenant-default",
        user_id="user-default",
    )

    await wrapper.chat(
        "hi",
        session_id="session-1",
        tool_context={"tenant_id": "tenant-override"},
    )

    assert orchestrator.last_tool_context is not None
    assert orchestrator.last_tool_context["task_service"] == "svc-1"
    assert orchestrator.last_tool_context["tenant_id"] == "tenant-override"

    await wrapper.resume(
        "pause-1",
        session_id="session-1",
        tool_context={"tenant_id": "tenant-2"},
    )
    assert orchestrator.last_tool_context is not None
    assert orchestrator.last_tool_context["task_service"] == "svc-1"
    assert orchestrator.last_tool_context["tenant_id"] == "tenant-2"
