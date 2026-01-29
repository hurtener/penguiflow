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
from penguiflow.planner import ReactPlanner
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
    def __init__(self) -> None:
        self._chunk_event = asyncio.Event()
        self._chunks: list[dict[str, Any]] = []
        self._tool_context: dict[str, Any] = {"tenant": "t"}

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
    def artifacts(self) -> Any:  # pragma: no cover - not used
        return None

    async def pause(self, reason: str, payload: Mapping[str, Any] | None = None) -> Any:  # pragma: no cover
        del reason, payload
        raise AssertionError("pause not expected")

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


def httpx_to_a2a_transport(client: httpx.AsyncClient):
    from penguiflow_a2a.transport import A2AHttpTransport

    return A2AHttpTransport(client=client)


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
