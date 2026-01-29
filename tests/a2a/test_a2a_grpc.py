import grpc
import pytest
from google.rpc import error_details_pb2
from grpc_status import rpc_status

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import A2AConfig, A2AService
from penguiflow_a2a.bindings.grpc import add_a2a_grpc_service
from penguiflow_a2a.grpc import a2a_pb2, a2a_pb2_grpc
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


def _agent_card() -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Test Agent",
        description="Test",
        supported_interfaces=[AgentInterface(url="https://example.com/a2a", protocol_binding="GRPC")],
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


async def _start_grpc_service():
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    server = grpc.aio.server()
    add_a2a_grpc_service(server, service)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, service, port


def _metadata():
    return (("a2a-version", "0.3"),)


@pytest.mark.asyncio
async def test_grpc_send_get_list() -> None:
    server, service, port = await _start_grpc_service()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    stub = a2a_pb2_grpc.A2AServiceStub(channel)
    try:
        request = a2a_pb2.SendMessageRequest(
            message=a2a_pb2.Message(
                message_id="msg-1",
                role=a2a_pb2.Role.ROLE_USER,
                parts=[a2a_pb2.Part(text="hello")],
            ),
            configuration=a2a_pb2.SendMessageConfiguration(blocking=True),
        )
        response = await stub.SendMessage(request, metadata=_metadata())
        assert response.task.status.state == a2a_pb2.TaskState.TASK_STATE_COMPLETED

        task_id = response.task.id
        get_task = await stub.GetTask(
            a2a_pb2.GetTaskRequest(name=f"tasks/{task_id}"),
            metadata=_metadata(),
        )
        assert get_task.id == task_id

        list_response = await stub.ListTasks(
            a2a_pb2.ListTasksRequest(page_size=1),
            metadata=_metadata(),
        )
        assert list_response.tasks
    finally:
        await channel.close()
        await server.stop(None)
        await service.stop()


@pytest.mark.asyncio
async def test_grpc_error_info_task_not_found() -> None:
    server, service, port = await _start_grpc_service()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    stub = a2a_pb2_grpc.A2AServiceStub(channel)
    try:
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stub.CancelTask(
                a2a_pb2.CancelTaskRequest(name="tasks/missing"),
                metadata=_metadata(),
            )
        exc = exc_info.value
        assert exc.code() == grpc.StatusCode.NOT_FOUND
        status = rpc_status.from_call(exc)
        assert status is not None
        info = error_details_pb2.ErrorInfo()
        unpacked = False
        for detail in status.details:
            if detail.Unpack(info):
                unpacked = True
                break
        assert unpacked
        assert info.domain == "a2a-protocol.org"
        assert info.reason == "TASK_NOT_FOUND"
    finally:
        await channel.close()
        await server.stop(None)
        await service.stop()
