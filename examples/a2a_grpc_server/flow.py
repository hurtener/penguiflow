"""A2A gRPC server example for PenguiFlow."""

from __future__ import annotations

import asyncio

import grpc

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import A2AConfig, A2AService
from penguiflow_a2a.bindings.grpc import add_a2a_grpc_service
from penguiflow_a2a.grpc import a2a_pb2, a2a_pb2_grpc
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill


async def echo(message: FlowMessage, _ctx) -> str:
    return f"echo:{message.payload}"


def _agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="A2A gRPC Example",
        description="Simple gRPC echo agent.",
        supported_interfaces=[AgentInterface(url=f"grpc://{host}:{port}", protocol_binding="GRPC")],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id="echo", name="Echo", description="Echo payloads", tags=["example"])],
    )


async def main() -> None:
    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)

    server = grpc.aio.server()
    port = server.add_insecure_port("127.0.0.1:0")
    service = A2AService(
        flow,
        agent_card=_agent_card("127.0.0.1", port),
        config=A2AConfig(),
    )
    add_a2a_grpc_service(server, service)

    await service.start()
    await server.start()

    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    stub = a2a_pb2_grpc.A2AServiceStub(channel)
    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id="msg-1",
            role=a2a_pb2.Role.ROLE_USER,
            parts=[a2a_pb2.Part(text="hello grpc")],
        ),
        configuration=a2a_pb2.SendMessageConfiguration(blocking=True),
    )

    try:
        response = await stub.SendMessage(
            request,
            metadata=(("a2a-version", "0.3"),),
        )
        state = a2a_pb2.TaskState.Name(response.task.status.state)
        print(f"Task {response.task.id} state: {state}")
    finally:
        await channel.close()
        await server.stop(None)
        await service.stop()


if __name__ == "__main__":  # pragma: no cover - example entrypoint
    asyncio.run(main())
