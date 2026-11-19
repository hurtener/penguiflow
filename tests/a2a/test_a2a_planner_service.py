"""Behaviour of ``A2AService`` as a ReactPlanner executor."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from penguiflow import ModelRegistry, Node, build_catalog, tool
from penguiflow.planner import PlannerFinish, ReactPlanner
from penguiflow.state import RemoteBinding
from penguiflow_a2a import A2AAgentToolset, A2AConfig, A2AHttpTransport, A2AService, create_a2a_http_app
from penguiflow_a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    TaskState,
)

from .planner_doubles import ScriptedPlanner, ScriptedRun


def _agent_card(*, streaming: bool = True) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Planner Agent",
        description="Test",
        supported_interfaces=[AgentInterface(url="http://test/a2a", protocol_binding="HTTP+JSON")],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=streaming,
            push_notifications=False,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id="answer", name="Answer", description="Answer", tags=["test"])],
    )


def _request(
    text: str,
    *,
    blocking: bool = True,
    task_id: str | None = None,
    context_id: str | None = None,
) -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id=f"msg-{time.monotonic_ns()}",
            role=Role.USER,
            parts=[Part(text=text)],
            task_id=task_id,
            context_id=context_id,
        ),
        configuration=SendMessageConfiguration(blocking=blocking),
    )


class StubClient:
    """JSON LLM stub that replays scripted planner actions."""

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    async def complete(
        self,
        *,
        messages: list[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del messages, response_format, stream, on_stream_chunk
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


class ToolThenFinishClient:
    """Stateless stub: call ``tool_name`` on the first step of a run, then finish."""

    def __init__(self, tool_name: str, answer: str) -> None:
        self._tool_name = tool_name
        self._answer = answer

    async def complete(
        self,
        *,
        messages: list[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del response_format, stream, on_stream_chunk
        if any(message.get("role") == "assistant" for message in messages):
            action = {"thought": "done", "next_node": None, "args": {"raw_answer": self._answer}}
        else:
            action = {"thought": "work", "next_node": self._tool_name, "args": {"text": "x"}}
        return json.dumps(action), 0.0


class Text(BaseModel):
    text: str


TOOL_CONTEXTS: list[dict[str, Any]] = []
RUN_WINDOWS: list[tuple[str, float, float]] = []


@tool(desc="Record the tool context and echo the text")
async def record_context(args: Text, ctx: Any) -> Text:
    TOOL_CONTEXTS.append(dict(ctx.tool_context))
    return args


@tool(desc="Ask a human to approve before continuing")
async def approval_gate(args: Text, ctx: Any) -> Text:
    await ctx.pause("approval_required", {"prompt": "Approve sending the report?"})
    return args


@tool(desc="Slow work")
async def slow_work(args: Text, ctx: Any) -> Text:
    await asyncio.sleep(5)
    return args


@tool(desc="Timed work")
async def timed_work(args: Text, ctx: Any) -> Text:
    start = time.monotonic()
    await asyncio.sleep(0.2)
    RUN_WINDOWS.append((str(ctx.tool_context["session_id"]), start, time.monotonic()))
    return args


def _catalog(*funcs: Any):
    registry = ModelRegistry()
    nodes = []
    for func in funcs:
        registry.register(func.__name__, Text, Text)
        nodes.append(Node(func, name=func.__name__))
    return build_catalog(nodes, registry)


async def _wait_for_state(service: A2AService, task_id: str, state: TaskState, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = await service.get_task(task_id, history_length=None)
        if task.status.state == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} never reached {state}")


async def _collect(queue: asyncio.Queue[StreamResponse]) -> list[StreamResponse]:
    events: list[StreamResponse] = []
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2)
        events.append(event)
        if event.status_update and event.status_update.final:
            return events


@pytest.mark.asyncio
async def test_real_planner_completes_task_with_answer_and_tool_context() -> None:
    TOOL_CONTEXTS.clear()
    planner = ReactPlanner(
        llm_client=StubClient(
            [
                {"thought": "record", "next_node": "record_context", "args": {"text": "hi"}},
                {"thought": "done", "next_node": None, "args": {"raw_answer": "All done"}},
            ]
        ),
        catalog=_catalog(record_context),
    )
    service = A2AService(
        planner,
        agent_card=_agent_card(),
        tool_context={"api_key_ref": "secret-ref"},
    )
    try:
        response = await service.send_message(_request("do it", context_id="ctx-1"), tenant="acme")
        task = response.task
        assert task is not None
        assert task.status.state == TaskState.COMPLETED
        assert task.artifacts is not None and len(task.artifacts) == 1
        artifact = task.artifacts[0]
        assert artifact.parts[0].text == "All done"
        assert artifact.metadata is not None
        assert artifact.metadata["planner_payload"]["raw_answer"] == "All done"

        tool_context = TOOL_CONTEXTS[-1]
        assert tool_context["tenant"] == "acme"
        assert tool_context["session_id"] == "ctx-1"
        assert tool_context["a2a_context_id"] == "ctx-1"
        assert tool_context["a2a_task_id"] == task.id
        assert tool_context["delivery_channel"] == "a2a"
        assert tool_context["api_key_ref"] == "secret-ref"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_real_planner_pause_maps_to_input_required_and_resumes() -> None:
    planner = ReactPlanner(
        llm_client=StubClient(
            [
                {"thought": "approval", "next_node": "approval_gate", "args": {"text": "report"}},
                {"thought": "done", "next_node": None, "args": {"raw_answer": "Report sent"}},
            ]
        ),
        catalog=_catalog(approval_gate),
        pause_enabled=True,
    )
    service = A2AService(planner, agent_card=_agent_card())
    try:
        first = await service.send_message(_request("send the report"), tenant=None)
        assert first.task is not None
        paused = first.task
        assert paused.status.state == TaskState.INPUT_REQUIRED
        assert paused.status.message is not None
        assert paused.status.message.parts[0].text == "Approve sending the report?"

        second = await service.send_message(
            _request("approved", task_id=paused.id, context_id=paused.context_id),
            tenant=None,
        )
        assert second.task is not None
        assert second.task.id == paused.id
        assert second.task.status.state == TaskState.COMPLETED
        assert second.task.artifacts is not None
        assert second.task.artifacts[0].parts[0].text == "Report sent"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_cancel_task_cancels_running_planner() -> None:
    planner = ReactPlanner(
        llm_client=ToolThenFinishClient("slow_work", "never"),
        catalog=_catalog(slow_work),
    )
    service = A2AService(planner, agent_card=_agent_card())
    try:
        response = await service.send_message(_request("go", blocking=False), tenant=None)
        assert response.task is not None
        task_id = response.task.id
        await _wait_for_state(service, task_id, TaskState.WORKING)
        started = time.monotonic()
        cancelled = await service.cancel_task(task_id)
        assert cancelled.status.state == TaskState.CANCELLED
        assert time.monotonic() - started < 2
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_cancel_paused_task_marks_it_cancelled() -> None:
    async def ask(run: ScriptedRun):
        return run.pause("await_input", prompt="More details?")

    service = A2AService(ScriptedPlanner(ask), agent_card=_agent_card())
    try:
        response = await service.send_message(_request("help"), tenant=None)
        assert response.task is not None
        assert response.task.status.state == TaskState.INPUT_REQUIRED
        cancelled = await service.cancel_task(response.task.id)
        assert cancelled.status.state == TaskState.CANCELLED
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_finish_without_answer_fails_task() -> None:
    async def give_up(run: ScriptedRun) -> PlannerFinish:
        return PlannerFinish(reason="no_path", payload=None, metadata={"error": "no tool fits"})

    service = A2AService(ScriptedPlanner(give_up), agent_card=_agent_card())
    try:
        response = await service.send_message(_request("impossible"), tenant=None)
        assert response.task is not None
        assert response.task.status.state == TaskState.FAILED
        assert response.task.status.message is not None
        assert response.task.status.message.parts[0].text == "no tool fits"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_planner_exception_fails_task() -> None:
    async def boom(run: ScriptedRun) -> str:
        raise RuntimeError("llm unavailable")

    service = A2AService(ScriptedPlanner(boom), agent_card=_agent_card())
    try:
        response = await service.send_message(_request("hi"), tenant=None)
        assert response.task is not None
        assert response.task.status.state == TaskState.FAILED
        assert response.task.status.message is not None
        assert response.task.status.message.parts[0].text == "llm unavailable"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_stream_appends_answer_chunks_and_final_artifact_replaces_them() -> None:
    async def streamer(run: ScriptedRun) -> str:
        run.emit_chunk("draft", action_seq=1)
        run.emit_chunk("Hel", action_seq=2)
        run.emit_chunk("lo", action_seq=2, done=True)
        return "Hello"

    service = A2AService(ScriptedPlanner(streamer), agent_card=_agent_card())
    try:
        task, queue, unsubscribe = await service.stream_message(_request("hi"), tenant=None)
        try:
            events = await _collect(queue)
        finally:
            await unsubscribe()
        updates = [event.artifact_update for event in events if event.artifact_update]
        assert [(u.artifact.parts[0].text, u.append, u.last_chunk) for u in updates] == [
            ("draft", False, False),
            ("Hel", False, False),
            ("lo", True, False),
            ("Hello", False, True),
        ]
        final = await service.get_task(task.id, history_length=None)
        assert final.status.state == TaskState.COMPLETED
        assert final.artifacts is not None
        assert [a.parts[0].text for a in final.artifacts] == ["Hello"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_send_message_does_not_forward_stream_chunks() -> None:
    async def streamer(run: ScriptedRun) -> str:
        run.emit_chunk("partial")
        return "full"

    service = A2AService(ScriptedPlanner(streamer), agent_card=_agent_card())
    try:
        response = await service.send_message(_request("hi"), tenant=None)
        assert response.task is not None
        assert response.task.artifacts is not None
        assert [a.parts[0].text for a in response.task.artifacts] == ["full"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_event_bridge_chains_and_restores_user_callback() -> None:
    seen: list[str] = []

    async def streamer(run: ScriptedRun) -> str:
        run.emit_chunk("x")
        return "done"

    planner = ScriptedPlanner(streamer, event_callback=lambda event: seen.append(event.event_type))
    original = planner._event_callback
    service = A2AService(planner, agent_card=_agent_card())
    await service.send_message(_request("hi"), tenant=None)
    assert seen == ["llm_stream_chunk"]
    assert planner._event_callback is not original
    await service.stop()
    assert planner._event_callback is original


@pytest.mark.asyncio
async def test_contexts_run_in_parallel_and_serialize_within_a_context() -> None:
    RUN_WINDOWS.clear()
    planner = ReactPlanner(
        llm_client=ToolThenFinishClient("timed_work", "ok"),
        catalog=_catalog(timed_work),
    )
    service = A2AService(planner, agent_card=_agent_card())
    try:
        await asyncio.gather(
            service.send_message(_request("a", context_id="ctx-a"), tenant=None),
            service.send_message(_request("b", context_id="ctx-b"), tenant=None),
            service.send_message(_request("a2", context_id="ctx-a"), tenant=None),
        )
        windows = {session: [] for session, _start, _end in RUN_WINDOWS}
        for session, start, end in RUN_WINDOWS:
            windows[session].append((start, end))

        def _overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
            return a[0] < b[1] and b[0] < a[1]

        first_a, second_a = sorted(windows["ctx-a"])
        assert not _overlap(first_a, second_a)
        assert _overlap(windows["ctx-b"][0], first_a) or _overlap(windows["ctx-b"][0], second_a)
    finally:
        await service.stop()


class RecordingBindingStore:
    def __init__(self) -> None:
        self.bindings: list[RemoteBinding] = []

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        self.bindings.append(binding)


@pytest.mark.asyncio
async def test_remote_binding_saved_to_planner_state_store() -> None:
    async def answer(run: ScriptedRun) -> str:
        return "ok"

    store = RecordingBindingStore()
    planner = ScriptedPlanner(answer, state_store=store)
    service = A2AService(planner, agent_card=_agent_card(), config=A2AConfig(agent_url="http://agent"))
    try:
        response = await service.send_message(_request("hi", context_id="ctx-bind"), tenant=None)
        assert response.task is not None
        assert [(b.context_id, b.task_id, b.agent_url) for b in store.bindings] == [
            ("ctx-bind", response.task.id, "http://agent")
        ]
    finally:
        await service.stop()


class FailingBindingStore:
    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        raise RuntimeError("store down")


@pytest.mark.asyncio
async def test_failing_binding_store_does_not_strand_task() -> None:
    async def answer(run: ScriptedRun) -> str:
        return "ok"

    planner = ScriptedPlanner(answer, state_store=FailingBindingStore())
    service = A2AService(planner, agent_card=_agent_card(), config=A2AConfig(agent_url="http://agent"))
    try:
        response = await service.send_message(_request("hi"), tenant=None)
        assert response.task is not None
        assert response.task.status.state == TaskState.COMPLETED
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_planner_manager_calls_planner_specialist_over_http() -> None:
    specialist = ReactPlanner(
        llm_client=StubClient([{"thought": "answer", "next_node": None, "args": {"raw_answer": "42"}}]),
        catalog=[],
    )
    service = A2AService(specialist, agent_card=_agent_card(streaming=False))
    app = create_a2a_http_app(service, include_docs=False)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        toolset = A2AAgentToolset(agent_url="http://test", transport=A2AHttpTransport(client=client))
        spec = toolset.tool(
            name="ask_specialist",
            skill="answer",
            args_model=Text,
            out_model=Text,
            desc="Ask the specialist",
            payload_builder=lambda args, _ctx: args.text,
        )
        manager = ReactPlanner(
            llm_client=StubClient(
                [
                    {"thought": "delegate", "next_node": "ask_specialist", "args": {"text": "meaning of life?"}},
                    {"thought": "done", "next_node": None, "args": {"raw_answer": "The specialist says 42"}},
                ]
            ),
            catalog=[spec],
            max_iters=4,
        )
        result = await manager.run("ask", tool_context={"tenant": "t"})

    assert result.reason == "answer_complete"
    steps = result.metadata["steps"]
    assert steps[0]["action"]["next_node"] == "ask_specialist"
    assert steps[0]["observation"] == {"text": "42"}
    await service.stop()
