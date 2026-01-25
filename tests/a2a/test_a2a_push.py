import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from penguiflow import Message as FlowMessage
from penguiflow import Node, NodePolicy, create
from penguiflow_a2a import A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    AuthenticationInfo,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
)
from penguiflow_a2a.push import is_safe_webhook_url


class FakePushSender:
    def __init__(self) -> None:
        self.events: list[tuple[PushNotificationConfig, Any]] = []

    async def send(self, config: PushNotificationConfig, event: Any) -> None:
        self.events.append((config, event))


def _agent_card(*, push_notifications: bool) -> AgentCard:
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
        skills=[AgentSkill(id="echo", name="Echo", description="Echo", tags=["test"])],
    )


def _build_client(*, push_notifications: bool) -> TestClient:
    async def echo(message: FlowMessage, _ctx) -> str:
        return f"echo:{message.payload}"

    node = Node(echo, name="echo", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(push_notifications=push_notifications), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _build_slow_client(*, push_notifications: bool, delay: float = 0.4) -> TestClient:
    async def slow(message: FlowMessage, _ctx) -> str:
        await asyncio.sleep(delay)
        return f"slow:{message.payload}"

    node = Node(slow, name="slow", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    service = A2AService(flow, agent_card=_agent_card(push_notifications=push_notifications), config=A2AConfig())
    app = create_a2a_http_app(service, include_docs=False)
    return TestClient(app, raise_server_exceptions=False)


def _base_headers() -> dict[str, str]:
    return {"A2A-Version": "0.3"}


def _message_payload(text: str, *, blocking: bool = True) -> dict[str, Any]:
    return {
        "message": {
            "messageId": "msg-1",
            "role": "user",
            "parts": [{"text": text}],
        },
        "configuration": {"blocking": blocking},
    }


def test_push_config_endpoints_require_capability() -> None:
    with _build_client(push_notifications=False) as client:
        response = client.get(
            "/tasks/task-1/pushNotificationConfigs",
            headers=_base_headers(),
        )
        assert response.status_code == 400
        assert response.json()["type"].endswith("push-notification-not-supported")


def test_push_config_crud() -> None:
    with _build_slow_client(push_notifications=True) as client:
        response = client.post(
            "/message:send",
            json=_message_payload("hello", blocking=False),
            headers=_base_headers(),
        )
        task_id = response.json()["task"]["id"]
        config_payload = {
            "pushNotificationConfig": {
                "url": "https://example.com/webhook",
                "token": "tok",
                "authentication": {"schemes": ["Bearer"], "credentials": "secret"},
            }
        }
        response = client.post(
            f"/tasks/{task_id}/pushNotificationConfigs",
            params={"configId": "cfg-1"},
            json=config_payload,
            headers=_base_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"].endswith("/pushNotificationConfigs/cfg-1")
        assert payload["pushNotificationConfig"]["id"] == "cfg-1"

        response = client.get(
            f"/tasks/{task_id}/pushNotificationConfigs/cfg-1",
            headers=_base_headers(),
        )
        assert response.status_code == 200

        response = client.get(
            f"/tasks/{task_id}/pushNotificationConfigs",
            headers=_base_headers(),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["configs"]
        assert payload["nextPageToken"] == ""

        response = client.delete(
            f"/tasks/{task_id}/pushNotificationConfigs/cfg-1",
            headers=_base_headers(),
        )
        assert response.status_code == 204


@pytest.mark.asyncio
async def test_push_delivery_receives_status_and_artifacts() -> None:
    async def streamer(message: FlowMessage, ctx) -> str:
        await ctx.emit_chunk(parent=message, text="hi", done=True)
        return "hi"

    node = Node(streamer, name="streamer", policy=NodePolicy(validate="none"))
    flow = create(node.to(), emit_errors_to_rookery=True)
    sender = FakePushSender()
    service = A2AService(
        flow,
        agent_card=_agent_card(push_notifications=True),
        config=A2AConfig(),
        push_sender=sender,
    )
    await service.start()
    try:
        request = SendMessageRequest(
            message=Message(
                message_id="msg-1",
                role=Role.USER,
                parts=[Part(text="hello")],
            ),
            configuration=SendMessageConfiguration(
                blocking=True,
                push_notification_config=PushNotificationConfig(
                    url="https://example.com/webhook",
                    authentication=AuthenticationInfo(schemes=["Bearer"], credentials="token"),
                ),
            ),
        )
        await service.send_message(request, tenant=None, requested_extensions=[])
        status_events = [event for _config, event in sender.events if event.status_update]
        artifact_events = [event for _config, event in sender.events if event.artifact_update]
        assert artifact_events
        assert any(event.status_update.final for event in status_events)
    finally:
        await service.stop()


def test_webhook_url_ssrf_checks() -> None:
    assert not is_safe_webhook_url("http://localhost:8000")
    assert not is_safe_webhook_url("http://127.0.0.1:8000")
    assert not is_safe_webhook_url("http://10.0.0.1")
    assert not is_safe_webhook_url("http://169.254.0.1")
    assert is_safe_webhook_url("https://8.8.8.8/webhook")
