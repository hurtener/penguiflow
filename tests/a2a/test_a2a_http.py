from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import (
    A2AConfig,
    A2AService,
    create_a2a_http_app,
    create_a2a_http_router,
    install_a2a_http,
)
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
    TaskState,
)


def _agent_card(*, push_notifications: bool = False) -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Test Agent",
        description="Test",
        supported_interfaces=[AgentInterface(url="https://example.com/a2a", protocol_binding="HTTP+JSON")],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=push_notifications,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echo",
                tags=["test"],
            )
        ],
    )


def _build_client() -> TestClient:
    async def echo(message: FlowMessage, _ctx) -> str:
        assert isinstance(message.payload, str)
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


def _build_slow_client(delay: float = 0.5) -> TestClient:
    async def slow(message: FlowMessage, _ctx) -> str:
        await asyncio.sleep(delay)
        return f"slow:{message.payload}"

    node = Node(slow, name="slow", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _build_router_client() -> tuple[TestClient, A2AService]:
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = FastAPI()
    app.include_router(create_a2a_http_router(service))
    return TestClient(app, raise_server_exceptions=False), service


def _base_headers() -> dict[str, str]:
    return {"A2A-Version": "0.3"}


def _message_payload(
    text: str,
    *,
    blocking: bool = False,
    history_length: int | None = None,
    message_id: str = "msg-1",
    context_id: str | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"blocking": blocking}
    if history_length is not None:
        config["historyLength"] = history_length
    message: dict[str, Any] = {
        "messageId": message_id,
        "role": "user",
        "parts": [{"text": text}],
    }
    if context_id is not None:
        message["contextId"] = context_id
    return {
        "message": message,
        "configuration": config,
    }


def test_send_message_blocking_and_get_task() -> None:
    with _build_client() as client:
        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=True),
            headers=_base_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        task = body["task"]
        assert task["status"]["state"] == TaskState.COMPLETED.value
        assert task["artifacts"][0]["parts"][0]["text"] == "echo:hello"

        task_id = task["id"]
        get_response = client.get(
            f"/tasks/{task_id}",
            headers=_base_headers(),
        )
        assert get_response.status_code == 200
        fetched = get_response.json()
        assert fetched["id"] == task_id

        list_response = client.get("/tasks", headers=_base_headers())
        assert list_response.status_code == 200
        data = list_response.json()
        assert data["tasks"]
        assert "artifacts" not in data["tasks"][0]


def test_agent_card_endpoint() -> None:
    with _build_client() as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        payload = response.json()
        assert payload["protocolVersions"] == ["0.3"]
        assert payload["supportedInterfaces"]


def test_send_message_non_blocking_returns_active_task() -> None:
    with _build_slow_client() as client:
        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=False),
            headers=_base_headers(),
        )
        assert response.status_code == 200
        task = response.json()["task"]
        assert task["status"]["state"] in {TaskState.SUBMITTED.value, TaskState.WORKING.value}


def test_send_message_history_length_semantics() -> None:
    with _build_client() as client:
        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=True, history_length=0, message_id="msg-0"),
            headers=_base_headers(),
        )
        assert response.status_code == 200
        task = response.json()["task"]
        assert "history" not in task

        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=True, history_length=1, message_id="msg-1"),
            headers=_base_headers(),
        )
        assert response.status_code == 200
        task = response.json()["task"]
        assert len(task["history"]) == 1

        task_id = task["id"]
        get_response = client.get(
            f"/tasks/{task_id}",
            params={"historyLength": "0"},
            headers=_base_headers(),
        )
        assert get_response.status_code == 200
        assert "history" not in get_response.json()


def test_send_message_reuses_context_with_distinct_tasks() -> None:
    with _build_client() as client:
        first_response = client.post(
            "/message:send",
            json=_message_payload("first", blocking=True, message_id="msg-context-1"),
            headers=_base_headers(),
        )
        assert first_response.status_code == 200
        first_task = first_response.json()["task"]
        context_id = first_task["contextId"]

        second_response = client.post(
            "/message:send",
            json=_message_payload(
                "second",
                blocking=True,
                message_id="msg-context-2",
                context_id=context_id,
            ),
            headers=_base_headers(),
        )
        assert second_response.status_code == 200
        second_task = second_response.json()["task"]

        assert second_task["contextId"] == context_id
        assert second_task["id"] != first_task["id"]

        list_response = client.get(
            "/tasks",
            params={"contextId": context_id},
            headers=_base_headers(),
        )
        assert list_response.status_code == 200
        tasks = list_response.json()["tasks"]
        assert {task["id"] for task in tasks} == {first_task["id"], second_task["id"]}


def test_stream_message_emits_task_and_final_status() -> None:
    with _build_stream_client() as client:
        events: list[dict[str, Any]] = []
        with client.stream(
            "POST",
            "/message:stream",
            json=_message_payload("one two", blocking=False),
            headers=_base_headers(),
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    events.append(payload)
        assert events
        assert "task" in events[0]
        assert all("task" not in event for event in events[1:])
        final_events = [event for event in events if "statusUpdate" in event]
        artifact_events = [event for event in events if "artifactUpdate" in event]
        assert artifact_events
        assert final_events
        assert final_events[-1]["statusUpdate"]["final"] is True


def test_subscribe_task_first_event_is_snapshot() -> None:
    with _build_slow_client() as client:
        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=False),
            headers=_base_headers(),
        )
        task_id = response.json()["task"]["id"]
        events: list[dict[str, Any]] = []
        with client.stream(
            "GET",
            f"/tasks/{task_id}:subscribe",
            headers=_base_headers(),
        ) as stream:
            assert stream.status_code == 200
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    events.append(payload)
        assert events
        assert "task" in events[0]
        final_events = [event for event in events if "statusUpdate" in event]
        assert final_events[-1]["statusUpdate"]["final"] is True


def test_list_tasks_pagination_and_ordering() -> None:
    with _build_client() as client:
        task_ids = []
        for idx in range(3):
            response = client.post(
                "/message:send",
                json=_message_payload(
                    f"hello-{idx}",
                    blocking=True,
                    message_id=f"msg-{idx}",
                ),
                headers=_base_headers(),
            )
            task_ids.append(response.json()["task"]["id"])

        list_response = client.get(
            "/tasks",
            params={"pageSize": 2},
            headers=_base_headers(),
        )
        assert list_response.status_code == 200
        payload = list_response.json()
        assert payload["nextPageToken"]
        assert payload["tasks"][0]["id"] == task_ids[-1]
        assert payload["tasks"][1]["id"] == task_ids[-2]
        assert "artifacts" not in payload["tasks"][0]

        list_response = client.get(
            "/tasks",
            params={"pageSize": 2, "pageToken": payload["nextPageToken"]},
            headers=_base_headers(),
        )
        payload = list_response.json()
        assert payload["nextPageToken"] == ""
        assert payload["tasks"][0]["id"] == task_ids[0]

        list_response = client.get(
            "/tasks",
            params={"includeArtifacts": True},
            headers=_base_headers(),
        )
        payload = list_response.json()
        assert "artifacts" in payload["tasks"][0]


def test_version_not_supported() -> None:
    with _build_client() as client:
        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=True),
            headers={"A2A-Version": "9.9"},
        )
        assert response.status_code == 400
        assert response.json()["type"].endswith("version-not-supported")


def test_router_direct_include_starts_service_via_lifespan() -> None:
    client, service = _build_router_client()
    assert service._flow_started is False

    with client:
        assert service._flow_started is True
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200
        assert response.json()["name"] == "Test Agent"

        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=True),
            headers=_base_headers(),
        )
        assert response.status_code == 200
        assert response.json()["task"]["status"]["state"] == TaskState.COMPLETED.value

    assert service._flow_started is False


def test_router_include_does_not_change_unrelated_validation_errors() -> None:
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    class EchoPayload(BaseModel):
        count: int

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = FastAPI()

    @app.post("/echo")
    async def local_echo(payload: EchoPayload) -> dict[str, int]:
        return payload.model_dump()

    app.include_router(create_a2a_http_router(service))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/echo", json={"count": "bad"})
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)

        response = client.get(
            "/tasks",
            params={"pageSize": "bad"},
            headers=_base_headers(),
        )
        assert response.status_code == 422
        assert response.json()["type"].endswith("invalid-request")


def test_install_helper_mounts_aliases_and_agent_card_once() -> None:
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    app = FastAPI()
    install_a2a_http(app, service)

    agent_card_routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/.well-known/agent-card.json"
    ]
    assert len(agent_card_routes) == 1

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/.well-known/agent-card.json")
        assert response.status_code == 200

        assert client.get("/v1/.well-known/agent-card.json").status_code == 404
        assert client.get("/tenant-a/v1/.well-known/agent-card.json").status_code == 404

        for path in ("/message:send", "/v1/message:send", "/tenant-a/v1/message:send"):
            response = client.post(
                path,
                json=_message_payload("hello", blocking=True),
                headers=_base_headers(),
            )
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_subscribe_task_multiple_subscribers_receive_same_events() -> None:
    async def streamer(message: FlowMessage, ctx) -> str:
        await asyncio.sleep(0.1)
        await ctx.emit_chunk(parent=message, text="hello", done=True)
        return "hello"

    node = Node(streamer, name="streamer", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(), config=A2AConfig())
    await service.start()
    try:
        request = SendMessageRequest(
            message=Message(
                message_id="msg-1",
                role=Role.USER,
                parts=[Part(text="hello")],
            ),
            configuration=SendMessageConfiguration(blocking=False),
        )
        response = await service.send_message(request, tenant=None, requested_extensions=[])
        assert response.task is not None
        task_id = response.task.id

        _, queue_a, unsub_a = await service.subscribe_task(task_id)
        _, queue_b, unsub_b = await service.subscribe_task(task_id)

        async def _collect(queue):
            events = []
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=2)
                events.append(event.model_dump(by_alias=True, exclude_none=True))
                if event.status_update and event.status_update.final:
                    break
            return events

        try:
            events_a, events_b = await asyncio.gather(_collect(queue_a), _collect(queue_b))
        finally:
            await unsub_a()
            await unsub_b()
        assert events_a == events_b
    finally:
        await service.stop()
