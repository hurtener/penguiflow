import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState


def _agent_card() -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Test Agent",
        description="Test",
        supported_interfaces=[AgentInterface(url="https://example.com/a2a", protocol_binding="HTTP+JSON")],
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


def _build_client() -> TestClient:
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _build_stream_client() -> TestClient:
    async def streamer(message: FlowMessage, ctx) -> str:
        words = str(message.payload).split()
        for idx, word in enumerate(words):
            await ctx.emit_chunk(parent=message, text=word, done=idx == len(words) - 1)
        return " ".join(words)

    node = Node(streamer, name="streamer", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _build_slow_client(delay: float = 0.4) -> TestClient:
    async def slow(message: FlowMessage, _ctx) -> str:
        await asyncio.sleep(delay)
        return f"slow:{message.payload}"

    node = Node(slow, name="slow", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _base_headers() -> dict[str, str]:
    return {"A2A-Version": "0.3"}


def _send_params(text: str, *, blocking: bool = True) -> dict[str, Any]:
    return {
        "message": {
            "messageId": "msg-1",
            "role": "user",
            "parts": [{"text": text}],
        },
        "configuration": {"blocking": blocking},
    }


def test_jsonrpc_send_message_get_task_and_list() -> None:
    with _build_client() as client:
        response = client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": _send_params("hello")},
            headers=_base_headers(),
        )
        assert response.status_code == 200
        task = response.json()["result"]["task"]
        assert task["status"]["state"] == TaskState.COMPLETED.value

        task_id = task["id"]
        response = client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}},
            headers=_base_headers(),
        )
        assert response.json()["result"]["id"] == task_id

        response = client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 3, "method": "ListTasks", "params": {"pageSize": 1}},
            headers=_base_headers(),
        )
        assert response.json()["result"]["tasks"]


def test_jsonrpc_send_streaming_message() -> None:
    with _build_stream_client() as client:
        events: list[dict[str, Any]] = []
        with client.stream(
            "POST",
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "SendStreamingMessage",
                "params": _send_params("one two", blocking=False),
            },
            headers=_base_headers(),
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    events.append(payload)
        assert events
        assert events[0]["result"]["id"]
        final_events = [event for event in events if "final" in event["result"]]
        assert final_events
        assert final_events[-1]["result"]["final"] is True


def test_jsonrpc_subscribe_task() -> None:
    with _build_slow_client() as client:
        response = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "SendMessage",
                "params": _send_params("hello", blocking=False),
            },
            headers=_base_headers(),
        )
        task_id = response.json()["result"]["task"]["id"]
        events: list[dict[str, Any]] = []
        with client.stream(
            "POST",
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "SubscribeToTask",
                "params": {"id": task_id},
            },
            headers=_base_headers(),
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    events.append(payload)
        assert events
        assert events[0]["result"]["id"] == task_id
        final_events = [event for event in events if "final" in event["result"]]
        assert final_events[-1]["result"]["final"] is True


def test_jsonrpc_error_mapping_task_not_found() -> None:
    with _build_client() as client:
        response = client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 99, "method": "CancelTask", "params": {"id": "missing"}},
            headers=_base_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["error"]["code"] == -32001
