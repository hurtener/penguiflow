"""Tests for A2A planner tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Never

import httpx
import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.artifacts import NoOpArtifactStore
from penguiflow.planner import PlannerFinish, ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow.remote import (
    RemoteCallRequest,
    RemoteCallResult,
    RemoteStreamEvent,
    RemoteTaskAuthRequired,
    RemoteTaskEvent,
    RemoteTaskSnapshot,
    RemoteTaskState,
    RemoteTaskStatus,
    RemoteTransport,
)
from penguiflow.state import InMemoryStateStore, RemoteBinding
from penguiflow_a2a import (
    A2AAgentToolset,
    A2AConfig,
    A2AService,
    create_a2a_http_app,
)
from penguiflow_a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from ..tool_context_doubles import FakeToolContext
from .planner_doubles import ScriptedPlanner, ScriptedRun

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence

    from penguiflow.planner.context import PlannerPauseReason, ToolContext
    from penguiflow_a2a.transport import A2AHttpTransport


class StubClient(JSONLLMClient):
    """LLM client that replays scripted responses."""

    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        """Replay ``responses`` in order."""
        self._responses = [json.dumps(item) for item in responses]

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        if not self._responses:
            msg = "No stub responses left"
            raise AssertionError(msg)
        return self._responses.pop(0), 0.0


def _agent_card(*, streaming: bool) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Planner Tools Test Agent",
        description="Test",
        supported_interfaces=[
            AgentInterface(url="http://test/a2a", protocol_binding="HTTP+JSON")
        ],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=streaming,
            push_notifications=False,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="echo", name="Echo", description="Echo", tags=["test"]
            )
        ],
    )


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoResult(BaseModel):
    """Output of the echo tool."""

    echo: str


class ContextEchoResult(EchoResult):
    """Output of a tool that echoes its A2A context."""

    context_id: str


@pytest.mark.asyncio
async def test_a2a_agent_toolset_unary_executes_via_planner() -> None:
    """A2A agent toolset unary executes via planner."""

    async def echo(run: ScriptedRun) -> PlannerFinish:
        payload = json.loads(run.query)
        return PlannerFinish(
            reason="answer_complete",
            payload={"echo": str(payload.get("text"))},
        )

    service = A2AService(
        ScriptedPlanner(echo),
        agent_card=_agent_card(streaming=False),
        config=A2AConfig(),
    )
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        toolset = A2AAgentToolset(
            agent_url="http://test",
            transport=httpx_to_a2a_transport(client),
        )
        spec = toolset.tool(
            name="a2a_echo",
            skill="echo",
            args_model=EchoArgs,
            out_model=EchoResult,
            desc="Echo via A2A",
            streaming=False,
        )

        planner = ReactPlanner(
            llm_client=StubClient(
                [
                    {
                        "thought": "call",
                        "next_node": "a2a_echo",
                        "args": {"text": "hello"},
                    },
                    {
                        "thought": "done",
                        "next_node": "final_response",
                        "args": {"answer": "ok"},
                    },
                ]
            ),
            catalog=[spec],
            max_iters=4,
        )
        result = await planner.run("hi", tool_context={"tenant": "t"})
        assert result.reason == "answer_complete"
        steps = result.metadata.get("steps")
        assert isinstance(steps, list)
        assert steps
        assert steps[0]["action"]["next_node"] == "a2a_echo"
        assert steps[0]["observation"] == {"echo": "hello"}
    await service.stop()


@pytest.mark.asyncio
async def test_toolset_reuses_remote_context_in_session() -> None:
    """A2a agent toolset reuses remote context within router session."""

    async def echo_context(run: ScriptedRun) -> PlannerFinish:
        payload = json.loads(run.query)
        context_id = run.tool_context.get("a2a_context_id")
        assert isinstance(context_id, str), "Expected A2A context id"
        assert run.tool_context["session_id"] == context_id
        return PlannerFinish(
            reason="answer_complete",
            payload={
                "echo": str(payload.get("text")),
                "context_id": context_id,
            },
        )

    service = A2AService(
        ScriptedPlanner(echo_context),
        agent_card=_agent_card(streaming=False),
        config=A2AConfig(),
    )
    app = create_a2a_http_app(service, include_docs=False)
    state_store = InMemoryStateStore()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        toolset = A2AAgentToolset(
            agent_url="http://test",
            transport=httpx_to_a2a_transport(client),
        )
        spec = toolset.tool(
            name="a2a_echo",
            skill="echo",
            args_model=EchoArgs,
            out_model=ContextEchoResult,
            desc="Echo via A2A",
            streaming=False,
        )

        planner = ReactPlanner(
            llm_client=StubClient(
                [
                    {
                        "thought": "call 1",
                        "next_node": "a2a_echo",
                        "args": {"text": "first"},
                    },
                    {
                        "thought": "call 2",
                        "next_node": "a2a_echo",
                        "args": {"text": "second"},
                    },
                    {
                        "thought": "done",
                        "next_node": "final_response",
                        "args": {"answer": "ok"},
                    },
                ]
            ),
            catalog=[spec],
            max_iters=5,
            state_store=state_store,
        )
        result = await planner.run(
            "hi", tool_context={"tenant": "t", "session_id": "router-session"}
        )
        assert result.reason == "answer_complete"
        steps = result.metadata.get("steps")
        assert isinstance(steps, list)
        observations = [step["observation"] for step in steps[:2]]
        assert observations[0]["context_id"] == observations[1]["context_id"]
        assert observations[0]["context_id"] != "router-session"

        bindings = await state_store.list_bindings(
            router_session_id="router-session"
        )
        assert len(bindings) == 2
        assert bindings[-1].context_id == observations[0]["context_id"]
        assert bindings[-1].last_remote_task_id is not None
    await service.stop()


@pytest.mark.asyncio
async def test_toolset_streaming_records_chunks() -> None:
    """A2a agent toolset streaming records chunks in planner history."""

    async def streamer(run: ScriptedRun) -> PlannerFinish:
        # Emit a partial chunk without marking it as the final A2A chunk.
        run.emit_chunk("hello", done=False)
        return PlannerFinish(
            reason="answer_complete", payload={"echo": "hello"}
        )

    service = A2AService(
        ScriptedPlanner(streamer),
        agent_card=_agent_card(streaming=True),
        config=A2AConfig(),
    )
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        toolset = A2AAgentToolset(
            agent_url="http://test",
            transport=httpx_to_a2a_transport(client),
        )
        spec = toolset.tool(
            name="a2a_stream",
            skill="stream",
            args_model=EchoArgs,
            out_model=EchoResult,
            desc="Stream via A2A",
            streaming=True,
        )

        planner = ReactPlanner(
            llm_client=StubClient(
                [
                    {
                        "thought": "call",
                        "next_node": "a2a_stream",
                        "args": {"text": "hello"},
                    },
                    {
                        "thought": "done",
                        "next_node": "final_response",
                        "args": {"answer": "ok"},
                    },
                ]
            ),
            catalog=[spec],
            max_iters=4,
        )
        result = await planner.run("hi", tool_context={"tenant": "t"})
        assert result.reason == "answer_complete"
        steps = result.metadata.get("steps")
        assert isinstance(steps, list)
        assert steps
        streams = steps[0].get("streams")
        assert isinstance(streams, dict)
        assert streams
        stream_id = next(iter(streams.keys()))
        chunks = streams[stream_id]
        assert isinstance(chunks, list)
        assert chunks[0]["text"] == "hello"
        assert chunks[0]["meta"].get("channel") == "answer"
        assert steps[0]["observation"] == {"echo": "hello"}
    await service.stop()


class _FakeCtx(FakeToolContext):
    """Tool context that signals each streamed chunk and records pauses."""

    def __init__(self, tool_context: dict[str, Any] | None = None) -> None:
        """Use ``tool_context``, or a ``{"tenant": "t"}`` default."""
        super().__init__(
            tool_context=tool_context or {"tenant": "t"},
            artifact_store=NoOpArtifactStore(),
        )
        self.chunk_event = asyncio.Event()
        self.pauses: list[dict[str, Any]] = []

    @override
    async def pause(
        self,
        reason: PlannerPauseReason,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        pause = {"reason": reason, "payload": dict(payload or {})}
        self.pauses.append(pause)
        return pause

    @override
    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        await super().emit_chunk(stream_id, seq, text, done=done, meta=meta)
        self.chunk_event.set()

    @override
    async def emit_artifact(
        self,
        stream_id: str,
        chunk: object,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:  # pragma: no cover
        msg = "emit_artifact not expected"
        raise AssertionError(msg)


class _TransportDouble(RemoteTransport):
    """Transport whose calls fail unless a subclass expects them."""

    def __init__(self) -> None:
        """Start with no recorded cancellations."""
        self.cancel_calls: list[tuple[str, str]] = []

    @override
    async def send(
        self, request: RemoteCallRequest
    ) -> RemoteCallResult:  # pragma: no cover
        msg = "send not expected"
        raise AssertionError(msg)

    @override
    def stream(
        self, request: RemoteCallRequest
    ) -> AsyncIterator[RemoteStreamEvent]:  # pragma: no cover
        msg = "stream not expected"
        raise AssertionError(msg)

    @override
    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        self.cancel_calls.append((agent_url, task_id))


class _TaskTransportDouble(_TransportDouble):
    """Transport with task operations; it records each ``send_task``."""

    def __init__(self) -> None:
        """Start with no recorded requests."""
        super().__init__()
        self.requests: list[tuple[RemoteCallRequest, bool]] = []

    async def send_task(
        self, request: RemoteCallRequest, *, blocking: bool = False
    ) -> RemoteTaskSnapshot:
        """Record the request and return :meth:`snapshot` for it."""
        self.requests.append((request, blocking))
        return self.snapshot(request)

    def snapshot(self, request: RemoteCallRequest) -> RemoteTaskSnapshot:
        """Return the task snapshot ``send_task`` answers with."""
        msg = f"send_task not expected for {request.agent_url}"
        raise AssertionError(msg)

    async def get_task(
        self,
        *,
        agent_url: str,
        task_id: str,
        history_length: int | None = None,
    ) -> RemoteTaskSnapshot:  # pragma: no cover
        """Fail: this transport does not expect polling."""
        msg = (
            f"get_task not expected for {agent_url}/{task_id} "
            f"(history_length={history_length})"
        )
        raise AssertionError(msg)


class _UnsubscribableTaskTransportDouble(_TaskTransportDouble):
    """Task transport offering a subscription that must not be used."""

    def subscribe_task(
        self, *, agent_url: str, task_id: str
    ) -> AsyncIterator[RemoteTaskEvent]:  # pragma: no cover
        """Fail: the tool should not subscribe to this task."""
        msg = f"subscribe_task not expected for {agent_url}/{task_id}"
        raise AssertionError(msg)


class _FakeTransport(_TransportDouble):
    """Streams one chunk, then hangs until cancelled."""

    @override
    async def stream(
        self, request: RemoteCallRequest
    ) -> AsyncIterator[RemoteStreamEvent]:
        yield RemoteStreamEvent(
            text="hello",
            done=False,
            task_id="task-1",
            agent_url=request.agent_url,
        )
        await asyncio.sleep(10)


class _FakeUnaryTaskOnlyTransport(_TransportDouble):
    """Answers ``send`` with a task-bound result and records requests."""

    def __init__(self) -> None:
        """Start with no recorded requests."""
        super().__init__()
        self.requests: list[RemoteCallRequest] = []

    @override
    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        self.requests.append(request)
        return RemoteCallResult(
            result={"echo": "ok"},
            task_id="task-2",
            agent_url=request.agent_url,
        )


class _FakeInputRequiredTaskTransport(_UnsubscribableTaskTransportDouble):
    """Every task asks the caller for more input."""

    @override
    def snapshot(self, request: RemoteCallRequest) -> RemoteTaskSnapshot:
        return RemoteTaskSnapshot(
            task_id="task-input",
            context_id=request.context_id or "ctx-input",
            status=RemoteTaskStatus(
                state=RemoteTaskState.INPUT_REQUIRED,
                message="Need the target account.",
            ),
            agent_url=request.agent_url,
        )


class _FakeAuthRequiredUnaryTransport(_TransportDouble):
    """``send`` fails because the remote account needs authorization."""

    @override
    async def send(self, request: RemoteCallRequest) -> Never:
        raise RemoteTaskAuthRequired(
            RemoteTaskSnapshot(
                task_id="task-auth",
                context_id=request.context_id or "ctx-auth",
                status=RemoteTaskStatus(
                    state=RemoteTaskState.AUTH_REQUIRED,
                    message="Authorize the remote account.",
                ),
                agent_url=request.agent_url,
            )
        )


class _FakeCompletedTaskTransport(_UnsubscribableTaskTransportDouble):
    """Every task completes at once."""

    @override
    def snapshot(self, request: RemoteCallRequest) -> RemoteTaskSnapshot:
        return RemoteTaskSnapshot(
            task_id=request.task_id or "task-new",
            context_id=request.context_id or "ctx-new",
            status=RemoteTaskStatus(state=RemoteTaskState.COMPLETED),
            result={"echo": "done"},
            agent_url=request.agent_url,
        )


class _FakeFailedTaskTransport(_FakeCompletedTaskTransport):
    """Every task fails at once."""

    @override
    def snapshot(self, request: RemoteCallRequest) -> RemoteTaskSnapshot:
        return RemoteTaskSnapshot(
            task_id="task-failed",
            context_id=request.context_id or "ctx-failed",
            status=RemoteTaskStatus(
                state=RemoteTaskState.FAILED, message="remote boom"
            ),
            agent_url=request.agent_url,
        )


class _FakeSubscribedTaskTransport(_TaskTransportDouble):
    """Tasks are queued; their updates arrive through a subscription."""

    @override
    def snapshot(self, request: RemoteCallRequest) -> RemoteTaskSnapshot:
        return RemoteTaskSnapshot(
            task_id="task-sub",
            context_id=request.context_id or "ctx-sub",
            status=RemoteTaskStatus(
                state=RemoteTaskState.SUBMITTED, message="queued"
            ),
            agent_url=request.agent_url,
        )

    async def subscribe_task(
        self, *, agent_url: str, task_id: str
    ) -> AsyncIterator[RemoteTaskEvent]:
        """Yield a status update, a partial artifact and the final task."""
        yield RemoteTaskEvent(
            kind="status",
            status=RemoteTaskStatus(
                state=RemoteTaskState.WORKING,
                message="Rendering",
                timestamp="2026-05-01T00:00:00Z",
            ),
            context_id="ctx-sub",
            task_id=task_id,
            agent_url=agent_url,
            meta={"step_id": "render", "label": "Rendering creative"},
        )
        yield RemoteTaskEvent(
            kind="artifact",
            text="halfway",
            result="halfway",
            artifact={"parts": [{"text": "halfway"}]},
            context_id="ctx-sub",
            task_id=task_id,
            agent_url=agent_url,
            meta={
                "step_id": "render",
                "status": "running",
                "last_chunk": False,
            },
        )
        yield RemoteTaskEvent(
            kind="task",
            task=RemoteTaskSnapshot(
                task_id=task_id,
                context_id="ctx-sub",
                status=RemoteTaskStatus(
                    state=RemoteTaskState.COMPLETED, message="Done"
                ),
                result={"echo": "done"},
                agent_url=agent_url,
                meta={"step_id": "render", "status": "completed"},
            ),
            context_id="ctx-sub",
            task_id=task_id,
            agent_url=agent_url,
            done=True,
        )


class _FakePollingTaskTransport(_TaskTransportDouble):
    """Tasks start working and complete on the second poll."""

    def __init__(self) -> None:
        """Start with no recorded polls."""
        super().__init__()
        self.polls: list[int | None] = []

    @override
    def snapshot(self, request: RemoteCallRequest) -> RemoteTaskSnapshot:
        return RemoteTaskSnapshot(
            task_id="task-poll",
            context_id=request.context_id or "ctx-poll",
            status=RemoteTaskStatus(
                state=RemoteTaskState.WORKING, message="started"
            ),
            agent_url=request.agent_url,
        )

    @override
    async def get_task(
        self,
        *,
        agent_url: str,
        task_id: str,
        history_length: int | None = None,
    ) -> RemoteTaskSnapshot:
        self.polls.append(history_length)
        state = (
            RemoteTaskState.COMPLETED
            if len(self.polls) >= 2
            else RemoteTaskState.WORKING
        )
        return RemoteTaskSnapshot(
            task_id=task_id,
            context_id="ctx-poll",
            status=RemoteTaskStatus(state=state, message=state.value),
            result={"echo": "polled"}
            if state is RemoteTaskState.COMPLETED
            else None,
            agent_url=agent_url,
        )


def httpx_to_a2a_transport(client: httpx.AsyncClient) -> A2AHttpTransport:
    """Build an A2A transport over ``client``."""
    from penguiflow_a2a.transport import A2AHttpTransport

    return A2AHttpTransport(client=client)


@pytest.mark.asyncio
async def test_toolset_persists_requested_context() -> None:
    """The toolset keeps the requested context when results omit it."""
    state_store = InMemoryStateStore()
    await state_store.save_remote_binding(
        RemoteBinding(
            trace_id="trace-1",
            context_id="ctx-existing",
            task_id="task-1",
            agent_url="http://test",
            router_session_id="router-session",
            remote_skill="echo",
            tenant_id="t",
        )
    )
    transport = _FakeUnaryTaskOnlyTransport()
    toolset = A2AAgentToolset(agent_url="http://test", transport=transport)
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        streaming=False,
    )
    ctx = _FakeCtx(
        tool_context={
            "tenant": "t",
            "session_id": "router-session",
            "state_store": state_store,
        }
    )

    result = await spec.node.func(EchoArgs(text="hi"), ctx)

    assert result == {"echo": "ok"}
    assert transport.requests[0].context_id == "ctx-existing"
    found = await state_store.find_binding(
        router_session_id="router-session",
        agent_url="http://test",
        remote_skill="echo",
        tenant_id="t",
    )
    assert found is not None
    assert found.context_id == "ctx-existing"
    assert found.task_id == "task-2"


@pytest.mark.asyncio
async def test_toolset_maps_input_required_to_pause() -> None:
    """A2a agent toolset maps input required to pause and binding."""
    state_store = InMemoryStateStore()
    transport = _FakeInputRequiredTaskTransport()
    toolset = A2AAgentToolset(agent_url="http://test", transport=transport)
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        execution_mode="task",
    )
    ctx = _FakeCtx(
        tool_context={
            "tenant": "t",
            "session_id": "router-session",
            "state_store": state_store,
        }
    )

    result = await spec.node.func(EchoArgs(text="hi"), ctx)

    assert result["reason"] == "await_input"
    assert result["payload"]["message"] == "Need the target account."
    assert result["payload"]["remote_task_id"] == "task-input"
    found = await state_store.find_binding(
        router_session_id="router-session",
        agent_url="http://test",
        remote_skill="echo",
        tenant_id="t",
    )
    assert found is not None
    assert found.context_id == "ctx-input"
    assert found.task_id == "task-input"
    assert found.metadata["awaiting_remote_input"] is True


@pytest.mark.asyncio
async def test_toolset_maps_auth_required_to_binding() -> None:
    """A2a agent toolset maps auth required to resumeable binding."""
    state_store = InMemoryStateStore()
    toolset = A2AAgentToolset(
        agent_url="http://test", transport=_FakeAuthRequiredUnaryTransport()
    )
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        streaming=False,
    )
    ctx = _FakeCtx(
        tool_context={
            "tenant": "t",
            "session_id": "router-session",
            "state_store": state_store,
        }
    )

    result = await spec.node.func(EchoArgs(text="hi"), ctx)

    assert result["reason"] == "approval_required"
    found = await state_store.find_binding(
        router_session_id="router-session",
        agent_url="http://test",
        remote_skill="echo",
        tenant_id="t",
    )
    assert found is not None
    assert found.task_id == "task-auth"
    assert found.metadata["awaiting_remote_auth"] is True


@pytest.mark.asyncio
async def test_toolset_keeps_context_after_resumed_task() -> None:
    """A2a agent toolset keeps context after resumed task completes."""
    state_store = InMemoryStateStore()
    await state_store.save_remote_binding(
        RemoteBinding(
            trace_id="trace-1",
            context_id="ctx-existing",
            task_id="task-input",
            agent_url="http://test",
            router_session_id="router-session",
            remote_skill="echo",
            tenant_id="t",
            metadata={"awaiting_remote_input": True},
        )
    )
    transport = _FakeCompletedTaskTransport()
    toolset = A2AAgentToolset(agent_url="http://test", transport=transport)
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        execution_mode="task",
    )
    ctx = _FakeCtx(
        tool_context={
            "tenant": "t",
            "session_id": "router-session",
            "state_store": state_store,
        }
    )

    assert await spec.node.func(EchoArgs(text="answer"), ctx) == {
        "echo": "done"
    }
    assert transport.requests[0][0].context_id == "ctx-existing"
    assert transport.requests[0][0].task_id == "task-input"

    assert await spec.node.func(EchoArgs(text="next"), ctx) == {"echo": "done"}
    assert transport.requests[1][0].context_id == "ctx-existing"
    assert transport.requests[1][0].task_id is None


@pytest.mark.asyncio
async def test_a2a_agent_toolset_task_mode_raises_failed_snapshot() -> None:
    """A2A agent toolset task mode raises failed snapshot."""
    toolset = A2AAgentToolset(
        agent_url="http://test", transport=_FakeFailedTaskTransport()
    )
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        execution_mode="task",
    )

    with pytest.raises(RuntimeError, match="remote boom"):
        await spec.node.func(
            EchoArgs(text="hi"),
            _FakeCtx(
                tool_context={"tenant": "t", "session_id": "router-session"}
            ),
        )


@pytest.mark.asyncio
async def test_a2a_agent_toolset_task_mode_emits_remote_task_events() -> None:
    """A2A agent toolset task mode emits remote task events."""
    events: list[RemoteTaskEvent] = []

    async def sink(event: RemoteTaskEvent, _ctx: ToolContext) -> None:
        events.append(event)

    transport = _FakeSubscribedTaskTransport()
    toolset = A2AAgentToolset(agent_url="http://test", transport=transport)
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        execution_mode="task",
    )
    ctx = _FakeCtx(
        tool_context={
            "tenant": "t",
            "session_id": "router-session",
            "a2a_remote_event_sink": sink,
        }
    )

    result = await spec.node.func(EchoArgs(text="hi"), ctx)

    assert result == {"echo": "done"}
    assert [event.kind for event in events] == [
        "task",
        "status",
        "artifact",
        "task",
    ]
    assert events[0].status is not None
    assert events[0].status.state is RemoteTaskState.SUBMITTED
    assert events[1].meta == {
        "step_id": "render",
        "label": "Rendering creative",
    }
    assert events[2].text == "halfway"
    assert events[2].artifact == {"parts": [{"text": "halfway"}]}
    assert ctx.chunks[0]["text"] == "halfway"
    assert ctx.chunks[0]["meta"]["remote_event_kind"] == "artifact"
    assert ctx.chunks[0]["meta"]["step_id"] == "render"


@pytest.mark.asyncio
async def test_toolset_task_mode_polls_without_subscription() -> None:
    """A2a agent toolset task mode polls when subscription is unavailable."""
    events: list[RemoteTaskEvent] = []
    transport = _FakePollingTaskTransport()
    toolset = A2AAgentToolset(agent_url="http://test", transport=transport)
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        execution_mode="task",
        poll_interval_s=0,
        max_poll_attempts=3,
    )
    ctx = _FakeCtx(
        tool_context={
            "tenant": "t",
            "session_id": "router-session",
            "remote_task_event_sink": lambda event, _ctx: events.append(event),
        }
    )

    result = await spec.node.func(EchoArgs(text="hi"), ctx)

    assert result == {"echo": "polled"}
    assert len(transport.polls) == 2
    assert [event.kind for event in events] == ["task", "status", "status"]
    assert events[-1].status is not None
    assert events[-1].status.state is RemoteTaskState.COMPLETED


@pytest.mark.asyncio
async def test_a2a_agent_toolset_cancel_propagates_to_transport() -> None:
    """A2A agent toolset cancel propagates to transport."""
    transport = _FakeTransport()
    toolset = A2AAgentToolset(agent_url="http://test", transport=transport)
    spec = toolset.tool(
        name="a2a_stream",
        skill="stream",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Stream via A2A",
        streaming=True,
    )
    ctx = _FakeCtx()

    async def _run() -> object:
        return await spec.node.func(EchoArgs(text="hi"), ctx)

    task = asyncio.create_task(_run())
    await asyncio.wait_for(ctx.chunk_event.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.cancel_calls == [("http://test", "task-1")]
