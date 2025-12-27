from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ag_ui.core import RunAgentInput

from penguiflow.agui_adapter import AGUIAdapter, AGUIEvent, add_agui_route, create_agui_endpoint


class DummyAdapter(AGUIAdapter):
    async def run(self, input: RunAgentInput) -> AsyncIterator[AGUIEvent]:
        async def _events() -> AsyncIterator[AGUIEvent]:
            yield self.text_start()
            yield self.text_content("hello")
            yield self.text_end()

        async for event in self.with_run_lifecycle(input, _events()):
            yield event


def _build_input() -> RunAgentInput:
    return RunAgentInput(
        thread_id="thread-1",
        run_id="run-1",
        messages=[{"id": "msg-1", "role": "user", "content": "Hello"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )


def test_create_agui_endpoint_streams_events() -> None:
    adapter = DummyAdapter()
    app = FastAPI()
    endpoint = create_agui_endpoint(adapter.run)
    app.post("/agui/agent")(endpoint)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/agui/agent",
        json=_build_input().model_dump(by_alias=True, mode="json"),
        headers={"accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert "hello" in response.text


def test_add_agui_route_registers_endpoint() -> None:
    adapter = DummyAdapter()
    app = FastAPI()
    add_agui_route(app, "/agui/agent", adapter.run)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/agui/agent",
        json=_build_input().model_dump(by_alias=True, mode="json"),
        headers={"accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "hello" in response.text
