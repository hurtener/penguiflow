"""Serve a ReactPlanner agent over the A2A gRPC binding."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import grpc
from pydantic import BaseModel
from typing_extensions import override

from penguiflow import ModelRegistry, Node, build_catalog, tool
from penguiflow.planner import ReactPlanner
from penguiflow.planner.models import JSONLLMClient
from penguiflow_a2a import A2AConfig, A2AService
from penguiflow_a2a.bindings.grpc import add_a2a_grpc_service
from penguiflow_a2a.grpc import a2a_pb2, a2a_pb2_grpc
from penguiflow_a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from penguiflow.planner.context import ToolContext


class EchoArgs(BaseModel):
    """Arguments of the echo tool."""

    text: str


class EchoResult(BaseModel):
    """Output of the echo tool."""

    echo: str


@tool(desc="Echo the given text back")
async def echo(args: EchoArgs, _ctx: ToolContext) -> EchoResult:
    """Echo the input back."""
    return EchoResult(echo=f"echo:{args.text}")


class ScriptedLLM(JSONLLMClient):
    """Offline LLM stand-in: call the echo tool once, then answer."""

    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        if not any(message.get("role") == "assistant" for message in messages):
            action: dict[str, object] = {
                "thought": "echo the request",
                "next_node": "echo",
                "args": {"text": "hello grpc"},
            }
        else:
            action = {
                "thought": "done",
                "next_node": None,
                "args": {"raw_answer": "echo:hello grpc"},
            }
        return json.dumps(action), 0.0


def build_planner() -> ReactPlanner:
    """Build the planner that the gRPC server serves."""
    registry = ModelRegistry()
    registry.register("echo", EchoArgs, EchoResult)
    catalog = build_catalog([Node(echo, name="echo")], registry)
    return ReactPlanner(llm_client=ScriptedLLM(), catalog=catalog, max_iters=4)


def _agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="A2A gRPC Example",
        description="ReactPlanner echo agent served over gRPC.",
        supported_interfaces=[
            AgentInterface(
                url=f"grpc://{host}:{port}", protocol_binding="GRPC"
            )
        ],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echo payloads",
                tags=["example"],
            )
        ],
    )


async def main() -> None:
    """Run the example."""
    server = grpc.aio.server()
    port = server.add_insecure_port("127.0.0.1:0")
    service = A2AService(
        build_planner(),
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
        answer = (
            response.task.artifacts[0].parts[0].text
            if response.task.artifacts
            else ""
        )
        sys.stdout.write(f"Task {response.task.id} state: {state}\n")
        sys.stdout.write(f"Answer: {answer}\n")
    finally:
        await channel.close()
        await server.stop(None)
        await service.stop()


if __name__ == "__main__":  # pragma: no cover - example entrypoint
    asyncio.run(main())
