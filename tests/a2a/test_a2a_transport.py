import asyncio

import httpx
import pytest

from penguiflow import Headers, Message, Node, NodePolicy, RemoteNode, create
from penguiflow.errors import FlowError
from penguiflow.remote import RemoteCallRequest, RemoteTaskState
from penguiflow.types import StreamChunk
from penguiflow_a2a import A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from penguiflow_a2a.transport import A2AHttpTransport, _build_send_message


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


def test_a2a_http_transport_builds_context_id_without_task_id() -> None:
    payload = _build_send_message(
        RemoteCallRequest(
            message=Message(payload="hello", headers=Headers(tenant="t")),
            skill="echo",
            agent_url="http://test",
            context_id="ctx-1",
        ),
        blocking=True,
    )

    message = payload["message"]
    assert message["contextId"] == "ctx-1"
    assert "taskId" not in message


def test_a2a_http_transport_builds_explicit_task_id_only_when_set() -> None:
    payload = _build_send_message(
        RemoteCallRequest(
            message=Message(payload="hello", headers=Headers(tenant="t")),
            skill="echo",
            agent_url="http://test",
            context_id="ctx-1",
            task_id="task-1",
        ),
        blocking=True,
    )

    message = payload["message"]
    assert message["contextId"] == "ctx-1"
    assert message["taskId"] == "task-1"


@pytest.mark.asyncio
async def test_a2a_http_transport_merges_agent_specific_headers() -> None:
    seen_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "completed"},
                "metadata": {},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = A2AHttpTransport(
            client=client,
            headers={"X-Global": "global"},
            agent_headers={"http://secure-agent/": {"Authorization": "Bearer test-token"}},
        )

        await transport.get_task(agent_url="http://secure-agent", task_id="task-1")

    assert seen_headers["x-global"] == "global"
    assert seen_headers["authorization"] == "Bearer test-token"


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


@pytest.mark.asyncio
async def test_a2a_http_transport_streaming_surfaces_failed_status_update() -> None:
    async def fail(message, _ctx) -> str:
        raise RuntimeError(f"boom:{message.payload}")

    node = Node(fail, name="fail", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        transport = A2AHttpTransport(client=client)
        remote = RemoteNode(
            transport=transport,
            skill="fail",
            agent_url="http://test",
            name="remote-fail",
            streaming=True,
        )
        client_flow = create(remote.to(), emit_errors_to_rookery=True)
        client_flow.run()
        try:
            message = Message(payload="hello", headers=Headers(tenant="t"))
            await client_flow.emit(message)
            result = await client_flow.fetch()
            assert isinstance(result, FlowError)
            assert "Remote task failed" in result.message
            assert "boom:hello" in result.message
        finally:
            await client_flow.stop()
    await service.stop()


@pytest.mark.asyncio
async def test_a2a_http_transport_task_lifecycle_and_push_config() -> None:
    async def slow(message, _ctx) -> dict[str, str]:
        await asyncio.sleep(0.05)
        return {"echo": str(message.payload)}

    node = Node(slow, name="slow", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    card = _agent_card().model_copy(
        update={"capabilities": _agent_card().capabilities.model_copy(update={"push_notifications": True})}
    )
    service = A2AService(flow, agent_card=card, config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)

    asgi_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=asgi_transport, base_url="http://test") as client:
        transport = A2AHttpTransport(client=client)
        request = RemoteCallRequest(
            message=Message(payload="hello", headers=Headers(tenant="t")),
            skill="echo",
            agent_url="http://test",
            context_id="ctx-task",
        )

        snapshot = await transport.send_task(request, blocking=False)
        assert snapshot.context_id == "ctx-task"
        assert snapshot.status.state in {RemoteTaskState.SUBMITTED, RemoteTaskState.WORKING}

        push = await transport.set_task_push_notification_config(
            agent_url="http://test",
            task_id=snapshot.task_id,
            config_id="cfg-1",
            config={"url": "https://example.com/hook"},
        )
        assert push.name and push.name.endswith("/pushNotificationConfigs/cfg-1")
        assert push.config["url"] == "https://example.com/hook"
        assert (await transport.get_task_push_notification_config(
            agent_url="http://test",
            task_id=snapshot.task_id,
            config_id="cfg-1",
        )).config["url"] == "https://example.com/hook"
        configs = await transport.list_task_push_notification_configs(
            agent_url="http://test",
            task_id=snapshot.task_id,
        )
        assert len(configs) == 1

        events = []
        async for event in transport.subscribe_task(agent_url="http://test", task_id=snapshot.task_id):
            events.append(event)
            if event.done:
                break
        assert events

        final = await transport.get_task(agent_url="http://test", task_id=snapshot.task_id)
        assert final.status.state is RemoteTaskState.COMPLETED
        assert final.result == {"echo": "hello"}
        page = await transport.list_tasks(agent_url="http://test", context_id="ctx-task", include_artifacts=True)
        assert [task.task_id for task in page.tasks] == [snapshot.task_id]

        await transport.delete_task_push_notification_config(
            agent_url="http://test",
            task_id=snapshot.task_id,
            config_id="cfg-1",
        )
        assert await transport.list_task_push_notification_configs(
            agent_url="http://test",
            task_id=snapshot.task_id,
        ) == []

    await service.stop()
