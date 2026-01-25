import httpx
import pytest

from penguiflow import Headers, Message, Node, NodePolicy, RemoteNode, create
from penguiflow.types import StreamChunk
from penguiflow_a2a import A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from penguiflow_a2a.transport import A2AHttpTransport


def _agent_card() -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Test Agent",
        description="Test",
        supported_interfaces=[AgentInterface(url="http://test/a2a", protocol_binding="HTTP+JSON")],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id="echo", name="Echo", description="Echo", tags=["test"])],
    )


@pytest.mark.asyncio
async def test_a2a_http_transport_unary() -> None:
    async def echo(message, _ctx) -> dict[str, str]:
        return {"echo": str(message.payload)}

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        transport = A2AHttpTransport(client=client)
        remote = RemoteNode(
            transport=transport,
            skill="echo",
            agent_url="http://test",
            name="remote-echo",
        )
        client_flow = create(remote.to())
        client_flow.run()
        try:
            message = Message(payload="hello", headers=Headers(tenant="t"))
            await client_flow.emit(message)
            result = await client_flow.fetch()
            assert result == {"echo": "hello"}
        finally:
            await client_flow.stop()
    await service.stop()


@pytest.mark.asyncio
async def test_a2a_http_transport_streaming() -> None:
    async def streamer(message, ctx) -> str:
        await ctx.emit_chunk(parent=message, text="hello", done=True)
        return "hello"

    node = Node(streamer, name="streamer", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        transport = A2AHttpTransport(client=client)
        remote = RemoteNode(
            transport=transport,
            skill="stream",
            agent_url="http://test",
            name="remote-stream",
            streaming=True,
        )
        client_flow = create(remote.to())
        client_flow.run()
        try:
            message = Message(payload="hello", headers=Headers(tenant="t"))
            await client_flow.emit(message)
            chunk_message = await client_flow.fetch()
            assert isinstance(chunk_message, Message)
            chunk = chunk_message.payload
            assert isinstance(chunk, StreamChunk)
            assert chunk.text == "hello"
            final = await client_flow.fetch()
            assert final == "hello"
        finally:
            await client_flow.stop()
    await service.stop()
