"""Tests for A2A transport."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest

from penguiflow import Headers, Message
from penguiflow.planner import PlannerFinish
from penguiflow.remote import (
    RemoteCallRequest,
    RemoteTaskInputRequired,
    RemoteTaskState,
)
from penguiflow_a2a import A2AConfig, A2AService, create_a2a_http_app
from penguiflow_a2a.models import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from penguiflow_a2a.transport import A2AHttpTransport, _build_send_message

from .planner_doubles import ScriptedPlanner, ScriptedRun

if TYPE_CHECKING:
    from penguiflow.planner.models import PlannerPause


def _agent_card() -> AgentCard:
    return AgentCard(
        protocol_versions=["0.3"],
        name="Test Agent",
        description="Test",
        supported_interfaces=[
            AgentInterface(url="http://test/a2a", protocol_binding="HTTP+JSON")
        ],
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
            state_transition_history=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="echo", name="Echo", description="Echo", tags=["test"]
            )
        ],
    )


def test_a2a_http_transport_builds_context_id_without_task_id() -> None:
    """A2A HTTP transport builds context ID without task ID."""
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
    """A2A HTTP transport builds explicit task ID only when set."""
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
    """A2A HTTP transport merges agent specific headers."""
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

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        transport = A2AHttpTransport(
            client=client,
            headers={"X-Global": "global"},
            agent_headers={
                "http://secure-agent/": {"Authorization": "Bearer test-token"}
            },
        )

        await transport.get_task(
            agent_url="http://secure-agent", task_id="task-1"
        )

    assert seen_headers["x-global"] == "global"
    assert seen_headers["authorization"] == "Bearer test-token"


def _request(text: str = "hello") -> RemoteCallRequest:
    return RemoteCallRequest(
        message=Message(payload=text, headers=Headers(tenant="t")),
        skill="echo",
        agent_url="http://test",
    )


@pytest.mark.asyncio
async def test_a2a_http_transport_unary() -> None:
    """A2A HTTP transport unary."""

    async def echo(run: ScriptedRun) -> PlannerFinish:
        return PlannerFinish(
            reason="answer_complete", payload={"echo": run.query}
        )

    service = A2AService(
        ScriptedPlanner(echo), agent_card=_agent_card(), config=A2AConfig()
    )
    app = create_a2a_http_app(service, include_docs=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        transport = A2AHttpTransport(client=client)
        result = await transport.send(_request())
        assert result.result == {"echo": "hello"}
        assert result.context_id
        assert result.task_id
    await service.stop()


@pytest.mark.asyncio
async def test_a2a_http_transport_streaming() -> None:
    """A2A HTTP transport streaming."""

    async def streamer(run: ScriptedRun) -> str:
        run.emit_chunk("hel")
        run.emit_chunk("lo", done=True)
        return "hello"

    service = A2AService(
        ScriptedPlanner(streamer), agent_card=_agent_card(), config=A2AConfig()
    )
    app = create_a2a_http_app(service, include_docs=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        transport = A2AHttpTransport(client=client)
        events = [event async for event in transport.stream(_request())]

    texts = [event.text for event in events if event.text]
    assert texts[:2] == ["hel", "lo"]
    assert events[-1].done is True
    assert events[-1].result == "hello"
    await service.stop()


@pytest.mark.asyncio
async def test_http_transport_stream_surfaces_failure() -> None:
    """A2a http transport streaming surfaces failed status update."""

    async def fail(run: ScriptedRun) -> str:
        msg = f"boom:{run.query}"
        raise RuntimeError(msg)

    service = A2AService(
        ScriptedPlanner(fail), agent_card=_agent_card(), config=A2AConfig()
    )
    app = create_a2a_http_app(service, include_docs=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        transport = A2AHttpTransport(client=client)
        with pytest.raises(
            RuntimeError, match="Remote task failed: boom:hello"
        ):
            async for _event in transport.stream(_request()):
                pass
    await service.stop()


@pytest.mark.asyncio
async def test_a2a_http_transport_surfaces_input_required() -> None:
    """A2A HTTP transport surfaces input required."""

    async def ask(run: ScriptedRun) -> PlannerPause:
        return run.pause("await_input", prompt="Which region?")

    service = A2AService(
        ScriptedPlanner(ask), agent_card=_agent_card(), config=A2AConfig()
    )
    app = create_a2a_http_app(service, include_docs=False)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        transport = A2AHttpTransport(client=client)
        with pytest.raises(RemoteTaskInputRequired) as excinfo:
            await transport.send(_request())
    assert (
        excinfo.value.snapshot.status.state is RemoteTaskState.INPUT_REQUIRED
    )
    assert excinfo.value.snapshot.status.message == "Which region?"
    await service.stop()


@pytest.mark.asyncio
async def test_a2a_http_transport_task_lifecycle_and_push_config() -> None:
    """A2A HTTP transport task lifecycle and push config."""

    async def slow(run: ScriptedRun) -> PlannerFinish:
        await asyncio.sleep(0.05)
        return PlannerFinish(
            reason="answer_complete", payload={"echo": run.query}
        )

    card = _agent_card().model_copy(
        update={
            "capabilities": _agent_card().capabilities.model_copy(
                update={"push_notifications": True}
            )
        }
    )
    service = A2AService(
        ScriptedPlanner(slow), agent_card=card, config=A2AConfig()
    )
    app = create_a2a_http_app(service, include_docs=False)

    asgi_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=asgi_transport, base_url="http://test"
    ) as client:
        transport = A2AHttpTransport(client=client)
        request = RemoteCallRequest(
            message=Message(payload="hello", headers=Headers(tenant="t")),
            skill="echo",
            agent_url="http://test",
            context_id="ctx-task",
        )

        snapshot = await transport.send_task(request, blocking=False)
        assert snapshot.context_id == "ctx-task"
        assert snapshot.status.state in {
            RemoteTaskState.SUBMITTED,
            RemoteTaskState.WORKING,
        }

        push = await transport.set_task_push_notification_config(
            agent_url="http://test",
            task_id=snapshot.task_id,
            config_id="cfg-1",
            config={"url": "https://example.com/hook"},
        )
        assert push.name and push.name.endswith(
            "/pushNotificationConfigs/cfg-1"
        )
        assert push.config["url"] == "https://example.com/hook"
        assert (
            await transport.get_task_push_notification_config(
                agent_url="http://test",
                task_id=snapshot.task_id,
                config_id="cfg-1",
            )
        ).config["url"] == "https://example.com/hook"
        configs = await transport.list_task_push_notification_configs(
            agent_url="http://test",
            task_id=snapshot.task_id,
        )
        assert len(configs) == 1

        events = []
        async for event in transport.subscribe_task(
            agent_url="http://test", task_id=snapshot.task_id
        ):
            events.append(event)
            if event.done:
                break
        assert events

        final = await transport.get_task(
            agent_url="http://test", task_id=snapshot.task_id
        )
        assert final.status.state is RemoteTaskState.COMPLETED
        assert final.result == {"echo": "hello"}
        page = await transport.list_tasks(
            agent_url="http://test",
            context_id="ctx-task",
            include_artifacts=True,
        )
        assert [task.task_id for task in page.tasks] == [snapshot.task_id]

        await transport.delete_task_push_notification_config(
            agent_url="http://test",
            task_id=snapshot.task_id,
            config_id="cfg-1",
        )
        assert (
            await transport.list_task_push_notification_configs(
                agent_url="http://test",
                task_id=snapshot.task_id,
            )
            == []
        )

    await service.stop()
