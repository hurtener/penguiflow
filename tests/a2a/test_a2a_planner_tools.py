from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow.artifacts import NoOpArtifactStore, ScopedArtifacts
from penguiflow.planner import ReactPlanner
from penguiflow.remote import RemoteTaskAuthRequired, RemoteTaskSnapshot, RemoteTaskState, RemoteTaskStatus
from penguiflow.state import InMemoryStateStore, RemoteBinding
from penguiflow_a2a import A2AAgentToolset, A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


class StubClient:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = [json.dumps(item) for item in responses]

    async def complete(
        self,
        *,
        messages: list[Mapping[str, str]],
        response_format: Mapping[str, object] | None = None,
        stream: bool = False,
        on_stream_chunk: object = None,
    ) -> tuple[str, float]:
        del messages, response_format, stream, on_stream_chunk
        if not self._responses:
            raise AssertionError("No stub responses left")
        return self._responses.pop(0), 0.0


def _agent_card(*, streaming: bool) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Planner Tools Test Agent",
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
        skills=[AgentSkill(id="echo", name="Echo", description="Echo", tags=["test"])],
    )


class EchoArgs(BaseModel):
    text: str


class EchoResult(BaseModel):
    echo: str


class ContextEchoResult(EchoResult):
    context_id: str


@pytest.mark.asyncio
async def test_a2a_agent_toolset_unary_executes_via_planner() -> None:
    async def echo(message: FlowMessage, _ctx: Any) -> dict[str, str]:
        payload = message.payload
        if not isinstance(payload, Mapping):
            raise AssertionError("Expected dict payload")
        return {"echo": str(payload.get("text"))}

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(streaming=False), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
                    {"thought": "call", "next_node": "a2a_echo", "args": {"text": "hello"}},
                    {"thought": "done", "next_node": "final_response", "args": {"answer": "ok"}},
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
async def test_a2a_agent_toolset_reuses_remote_context_within_router_session() -> None:
    async def echo_context(message: FlowMessage, _ctx: Any) -> dict[str, str]:
        payload = message.payload
        if not isinstance(payload, Mapping):
            raise AssertionError("Expected dict payload")
        context_id = message.meta.get("a2a_context_id")
        if not isinstance(context_id, str):
            raise AssertionError("Expected A2A context id")
        return {"echo": str(payload.get("text")), "context_id": context_id}

    node = Node(echo_context, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(streaming=False), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    state_store = InMemoryStateStore()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
                    {"thought": "call 1", "next_node": "a2a_echo", "args": {"text": "first"}},
                    {"thought": "call 2", "next_node": "a2a_echo", "args": {"text": "second"}},
                    {"thought": "done", "next_node": "final_response", "args": {"answer": "ok"}},
                ]
            ),
            catalog=[spec],
            max_iters=5,
            state_store=state_store,
        )
        result = await planner.run("hi", tool_context={"tenant": "t", "session_id": "router-session"})
        assert result.reason == "answer_complete"
        steps = result.metadata.get("steps")
        assert isinstance(steps, list)
        observations = [step["observation"] for step in steps[:2]]
        assert observations[0]["context_id"] == observations[1]["context_id"]
        assert observations[0]["context_id"] != "router-session"

        bindings = await state_store.list_bindings(router_session_id="router-session")
        assert len(bindings) == 2
        assert bindings[-1].context_id == observations[0]["context_id"]
        assert bindings[-1].last_remote_task_id is not None
    await service.stop()


@pytest.mark.asyncio
async def test_a2a_agent_toolset_streaming_records_chunks_in_planner_history() -> None:
    async def streamer(message: FlowMessage, ctx: Any) -> dict[str, str]:
        # Emit a partial chunk without marking it as the final A2A chunk.
        await ctx.emit_chunk(parent=message, text="hello", done=False)
        return {"echo": "hello"}

    node = Node(streamer, name="streamer", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(streaming=True), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
                    {"thought": "call", "next_node": "a2a_stream", "args": {"text": "hello"}},
                    {"thought": "done", "next_node": "final_response", "args": {"answer": "ok"}},
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


class _FakeCtx:
    def __init__(self, tool_context: dict[str, Any] | None = None) -> None:
        self._chunk_event = asyncio.Event()
        self._chunks: list[dict[str, Any]] = []
        self.pauses: list[dict[str, Any]] = []
        self._tool_context: dict[str, Any] = tool_context or {"tenant": "t"}

    @property
    def llm_context(self) -> Mapping[str, Any]:
        return {}

    @property
    def tool_context(self) -> dict[str, Any]:
        return self._tool_context

    @property
    def meta(self) -> dict[str, Any]:
        return dict(self._tool_context)

    @property
    def _artifacts(self) -> Any:  # pragma: no cover - not used
        return NoOpArtifactStore()

    @property
    def artifacts(self) -> Any:  # pragma: no cover - not used
        return ScopedArtifacts(
            NoOpArtifactStore(),
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )

    async def pause(self, reason: str, payload: Mapping[str, Any] | None = None) -> Any:
        pause = {"reason": reason, "payload": dict(payload or {})}
        self.pauses.append(pause)
        return pause

    async def emit_chunk(
        self,
        stream_id: str,
        seq: int,
        text: str,
        *,
        done: bool = False,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        self._chunks.append(
            {
                "stream_id": stream_id,
                "seq": seq,
                "text": text,
                "done": done,
                "meta": dict(meta or {}),
            }
        )
        self._chunk_event.set()

    async def emit_artifact(
        self,
        stream_id: str,
        chunk: Any,
        *,
        done: bool = False,
        artifact_type: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:  # pragma: no cover
        del stream_id, chunk, done, artifact_type, meta
        raise AssertionError("emit_artifact not expected")


class _FakeTransport:
    def __init__(self) -> None:
        self.cancel_calls: list[tuple[str, str]] = []

    async def send(self, request):  # pragma: no cover
        del request
        raise AssertionError("send not expected")

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        self.cancel_calls.append((agent_url, task_id))

    async def stream(self, request):
        from penguiflow.remote import RemoteStreamEvent

        yield RemoteStreamEvent(text="hello", done=False, task_id="task-1", agent_url=request.agent_url)
        await asyncio.sleep(10)


class _FakeUnaryTaskOnlyTransport:
    def __init__(self) -> None:
        self.requests = []

    async def send(self, request):
        from penguiflow.remote import RemoteCallResult

        self.requests.append(request)
        return RemoteCallResult(result={"echo": "ok"}, task_id="task-2", agent_url=request.agent_url)

    async def cancel(self, *, agent_url: str, task_id: str) -> None:  # pragma: no cover
        del agent_url, task_id

    async def stream(self, request):  # pragma: no cover
        del request
        raise AssertionError("stream not expected")


class _FakeInputRequiredTaskTransport:
    def __init__(self) -> None:
        self.requests = []

    async def send(self, request):  # pragma: no cover
        del request
        raise AssertionError("send not expected")

    async def send_task(self, request, *, blocking: bool = False):
        self.requests.append((request, blocking))
        return RemoteTaskSnapshot(
            task_id="task-input",
            context_id=request.context_id or "ctx-input",
            status=RemoteTaskStatus(
                state=RemoteTaskState.INPUT_REQUIRED,
                message="Need the target account.",
            ),
            agent_url=request.agent_url,
        )

    async def get_task(self, *, agent_url: str, task_id: str, history_length: int | None = None):  # pragma: no cover
        del agent_url, task_id, history_length
        raise AssertionError("get_task not expected")

    async def cancel(self, *, agent_url: str, task_id: str) -> None:  # pragma: no cover
        del agent_url, task_id

    async def stream(self, request):  # pragma: no cover
        del request
        raise AssertionError("stream not expected")

    async def subscribe_task(self, *, agent_url: str, task_id: str):  # pragma: no cover
        del agent_url, task_id
        raise AssertionError("subscribe_task not expected")


class _FakeAuthRequiredUnaryTransport:
    async def send(self, request):
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

    async def cancel(self, *, agent_url: str, task_id: str) -> None:  # pragma: no cover
        del agent_url, task_id

    async def stream(self, request):  # pragma: no cover
        del request
        raise AssertionError("stream not expected")


class _FakeCompletedTaskTransport:
    def __init__(self) -> None:
        self.requests = []

    async def send(self, request):  # pragma: no cover
        del request
        raise AssertionError("send not expected")

    async def send_task(self, request, *, blocking: bool = False):
        self.requests.append((request, blocking))
        return RemoteTaskSnapshot(
            task_id=request.task_id or "task-new",
            context_id=request.context_id or "ctx-new",
            status=RemoteTaskStatus(state=RemoteTaskState.COMPLETED),
            result={"echo": "done"},
            agent_url=request.agent_url,
        )

    async def get_task(self, *, agent_url: str, task_id: str, history_length: int | None = None):  # pragma: no cover
        del agent_url, task_id, history_length
        raise AssertionError("get_task not expected")

    async def cancel(self, *, agent_url: str, task_id: str) -> None:  # pragma: no cover
        del agent_url, task_id

    async def stream(self, request):  # pragma: no cover
        del request
        raise AssertionError("stream not expected")

    async def subscribe_task(self, *, agent_url: str, task_id: str):  # pragma: no cover
        del agent_url, task_id
        raise AssertionError("subscribe_task not expected")


class _FakeFailedTaskTransport(_FakeCompletedTaskTransport):
    async def send_task(self, request, *, blocking: bool = False):
        self.requests.append((request, blocking))
        return RemoteTaskSnapshot(
            task_id="task-failed",
            context_id=request.context_id or "ctx-failed",
            status=RemoteTaskStatus(state=RemoteTaskState.FAILED, message="remote boom"),
            agent_url=request.agent_url,
        )


def httpx_to_a2a_transport(client: httpx.AsyncClient):
    from penguiflow_a2a.transport import A2AHttpTransport

    return A2AHttpTransport(client=client)


@pytest.mark.asyncio
async def test_a2a_agent_toolset_persists_requested_context_when_result_omits_context() -> None:
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
    ctx = _FakeCtx(tool_context={"tenant": "t", "session_id": "router-session", "state_store": state_store})

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
async def test_a2a_agent_toolset_maps_input_required_to_pause_and_binding() -> None:
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
    ctx = _FakeCtx(tool_context={"tenant": "t", "session_id": "router-session", "state_store": state_store})

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
async def test_a2a_agent_toolset_maps_auth_required_to_resumeable_binding() -> None:
    state_store = InMemoryStateStore()
    toolset = A2AAgentToolset(agent_url="http://test", transport=_FakeAuthRequiredUnaryTransport())
    spec = toolset.tool(
        name="a2a_echo",
        skill="echo",
        args_model=EchoArgs,
        out_model=EchoResult,
        desc="Echo via A2A",
        streaming=False,
    )
    ctx = _FakeCtx(tool_context={"tenant": "t", "session_id": "router-session", "state_store": state_store})

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
async def test_a2a_agent_toolset_keeps_context_after_resumed_task_completes() -> None:
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
    ctx = _FakeCtx(tool_context={"tenant": "t", "session_id": "router-session", "state_store": state_store})

    assert await spec.node.func(EchoArgs(text="answer"), ctx) == {"echo": "done"}
    assert transport.requests[0][0].context_id == "ctx-existing"
    assert transport.requests[0][0].task_id == "task-input"

    assert await spec.node.func(EchoArgs(text="next"), ctx) == {"echo": "done"}
    assert transport.requests[1][0].context_id == "ctx-existing"
    assert transport.requests[1][0].task_id is None


@pytest.mark.asyncio
async def test_a2a_agent_toolset_task_mode_raises_failed_snapshot() -> None:
    toolset = A2AAgentToolset(agent_url="http://test", transport=_FakeFailedTaskTransport())
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
            _FakeCtx(tool_context={"tenant": "t", "session_id": "router-session"}),
        )


@pytest.mark.asyncio
async def test_a2a_agent_toolset_cancel_propagates_to_transport() -> None:
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
    task = asyncio.create_task(spec.node.func(EchoArgs(text="hi"), ctx))
    await asyncio.wait_for(ctx._chunk_event.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.cancel_calls == [("http://test", "task-1")]
