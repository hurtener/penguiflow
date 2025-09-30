from __future__ import annotations

import asyncio
import json

import pytest

from penguiflow import Message, Node, create
from penguiflow.state import RemoteBinding, StateStore, StoredEvent
from penguiflow_a2a import (
    A2AAgentCard,
    A2AMessagePayload,
    A2AServerAdapter,
    A2ASkill,
    A2ATaskCancelRequest,
    create_a2a_app,
)
from penguiflow_a2a.server import A2ARequestError


class RecordingStateStore(StateStore):
    def __init__(self) -> None:
        self.events: list[StoredEvent] = []
        self.bindings: list[RemoteBinding] = []

    async def save_event(self, event: StoredEvent) -> None:
        self.events.append(event)

    async def load_history(self, trace_id: str) -> list[StoredEvent]:
        return [event for event in self.events if event.trace_id == trace_id]

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        self.bindings.append(binding)


def _default_agent_card() -> A2AAgentCard:
    return A2AAgentCard(
        name="Penguin Main Agent",
        description="Routes work to the local PenguiFlow graph.",
        version="2.1.0",
        skills=[
            A2ASkill(
                name="orchestrate",
                description="Primary entrypoint for routed tasks.",
                mode="both",
            )
        ],
    )


def _parse_sse(raw: str) -> list[tuple[str | None, list[str]]]:
    events: list[tuple[str | None, list[str]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        events.append((event_name, data_lines))
    return events


@pytest.mark.asyncio
async def test_a2a_server_agent_card_and_unary_send() -> None:
    store = RecordingStateStore()

    async def echo(message: Message, _ctx) -> dict[str, str]:
        payload = message.payload
        assert isinstance(payload, dict)
        return {"echo": payload["query"]}

    echo_node = Node(echo, name="echo")
    flow = create(echo_node.to(), state_store=store)
    adapter = A2AServerAdapter(
        flow,
        agent_card=_default_agent_card(),
        agent_url="https://main-agent.example",
    )
    app = create_a2a_app(adapter, include_docs=False)
    assert app.title == "Penguin Main Agent"

    await adapter.start()
    try:
        result = await adapter.handle_send(
            A2AMessagePayload(
                payload={"query": "penguins"},
                headers={"tenant": "acme"},
                meta={"request_id": "req-123"},
            )
        )
    finally:
        await asyncio.wait_for(adapter.stop(), timeout=1.0)

    assert result["status"] == "succeeded"
    assert result["output"] == {"echo": "penguins"}
    assert result["taskId"] == result["contextId"]
    assert store.bindings
    binding = store.bindings[0]
    assert binding.agent_url == "https://main-agent.example"
    assert binding.task_id == result["taskId"]
    assert binding.context_id == result["contextId"]


@pytest.mark.asyncio
async def test_a2a_server_streaming_flow() -> None:
    store = RecordingStateStore()
    release_final = asyncio.Event()

    async def streaming_node(message: Message, ctx) -> dict[str, str]:
        payload = message.payload
        assert isinstance(payload, dict)
        await ctx.emit_chunk(
            parent=message,
            text=f"partial:{payload['prompt']}",
            meta={"step": 0},
        )
        await release_final.wait()
        return {"final": payload["prompt"].upper()}

    stream_node = Node(streaming_node, name="stream")
    flow = create(stream_node.to(), state_store=store)
    adapter = A2AServerAdapter(
        flow,
        agent_card=_default_agent_card(),
        agent_url="https://main-agent.example",
    )

    await adapter.start()
    try:
        generator, task_id, context_id = await adapter.stream(
            A2AMessagePayload(
                payload={"prompt": "hello"},
                headers={"tenant": "acme"},
            )
        )
        chunks: list[str] = []
        async for item in generator:
            text = item.decode()
            chunks.append(text)
            if "partial:hello" in text and not release_final.is_set():
                release_final.set()
    finally:
        release_final.set()
        await asyncio.wait_for(adapter.stop(), timeout=1.0)

    events = _parse_sse("".join(chunks))
    status_event = json.loads(events[0][1][0])
    assert status_event["status"] == "accepted"
    chunk_event = events[1]
    assert chunk_event[0] == "chunk"
    assert "partial:hello" in chunk_event[1][0]
    chunk_meta = json.loads(chunk_event[1][1])
    assert chunk_meta["taskId"] == status_event["taskId"]
    artifact_event = json.loads(events[2][1][0])
    assert artifact_event["output"] == {"final": "HELLO"}
    done_event = json.loads(events[-1][1][0])
    assert done_event["taskId"] == status_event["taskId"]
    assert store.bindings
    binding = store.bindings[0]
    assert binding.task_id == task_id
    assert binding.context_id == context_id


@pytest.mark.asyncio
async def test_a2a_server_cancel_stream() -> None:
    store = RecordingStateStore()
    release_final = asyncio.Event()

    async def cancellable_node(message: Message, ctx) -> dict[str, str]:
        payload = message.payload
        assert isinstance(payload, dict)
        await ctx.emit_chunk(parent=message, text="hold", meta={})
        await release_final.wait()
        return {"final": "never"}

    cancel_node = Node(cancellable_node, name="cancel")
    flow = create(cancel_node.to(), state_store=store)
    adapter = A2AServerAdapter(
        flow,
        agent_card=_default_agent_card(),
        agent_url="https://main-agent.example",
    )

    await adapter.start()
    try:
        generator, task_id, context_id = await adapter.stream(
            A2AMessagePayload(
                payload={"prompt": "cancel"},
                headers={"tenant": "acme"},
            )
        )
        parts: list[str] = []
        timeout = 2.0
        handshake = await asyncio.wait_for(generator.__anext__(), timeout=timeout)
        parts.append(handshake.decode())
        first_chunk = await asyncio.wait_for(generator.__anext__(), timeout=timeout)
        parts.append(first_chunk.decode())
        cancel_result = await adapter.cancel(A2ATaskCancelRequest(task_id=task_id))
        assert cancel_result["cancelled"] is True
        release_final.set()
        while True:
            try:
                chunk = await asyncio.wait_for(generator.__anext__(), timeout=timeout)
            except TimeoutError:
                break
            except StopAsyncIteration:
                break
            parts.append(chunk.decode())
    finally:
        release_final.set()
        await asyncio.wait_for(adapter.stop(), timeout=1.0)

    events = _parse_sse("".join(parts))
    assert any(event for event in events if event[0] == "chunk")
    assert store.bindings


@pytest.mark.asyncio
async def test_a2a_server_isolates_concurrent_traces() -> None:
    store = RecordingStateStore()

    async def variable_worker(message: Message, _ctx) -> dict[str, str]:
        payload = message.payload
        assert isinstance(payload, dict)
        await asyncio.sleep(payload["delay"])
        return {"agent": payload["name"]}

    worker_node = Node(variable_worker, name="worker")
    flow = create(worker_node.to(), state_store=store)
    adapter = A2AServerAdapter(
        flow,
        agent_card=_default_agent_card(),
        agent_url="https://main-agent.example",
    )

    await adapter.start()
    try:
        payloads = [
            {"delay": 0.05, "name": "slow"},
            {"delay": 0.0, "name": "fast"},
        ]
        tasks = [
            asyncio.create_task(
                adapter.handle_send(
                    A2AMessagePayload(
                        payload=payload,
                        headers={"tenant": "acme"},
                    )
                )
            )
            for payload in payloads
        ]
        results = await asyncio.gather(*tasks)
    finally:
        await asyncio.wait_for(adapter.stop(), timeout=1.0)

    assert {result["status"] for result in results} == {"succeeded"}
    for payload, result in zip(payloads, results, strict=False):
        assert result["output"] == {"agent": payload["name"]}
        assert result["traceId"]
        assert result["taskId"]


@pytest.mark.asyncio
async def test_a2a_server_requires_headers() -> None:
    store = RecordingStateStore()

    async def noop(message: Message, _ctx) -> dict[str, str]:
        return {"echo": "noop"}

    noop_node = Node(noop, name="noop")
    flow = create(noop_node.to(), state_store=store)
    adapter = A2AServerAdapter(
        flow,
        agent_card=_default_agent_card(),
        agent_url="https://main-agent.example",
    )

    await adapter.start()
    try:
        with pytest.raises(A2ARequestError) as exc:
            await adapter.handle_send(
                A2AMessagePayload(
                    payload={"query": "x"},
                    headers={},
                )
            )
    finally:
        await asyncio.wait_for(adapter.stop(), timeout=1.0)

    assert exc.value.status_code == 422


class DummyFlow:
    def __init__(self) -> None:
        self.started = False
        self.stop_calls = 0

    def run(self, *, registry=None) -> None:  # noqa: D401 - match PenguiFlow signature
        self.started = True

    async def stop(self) -> None:
        self.started = False
        self.stop_calls += 1

    # The adapter never calls the following methods in this test, but they exist on
    # ``PenguiFlow``. Provide dummies so the adapter can be instantiated.
    async def emit(self, *_args, **_kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError

    async def fetch(self, *_args, **_kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError

    async def cancel(self, *_args, **_kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError

    async def save_remote_binding(self, *_args, **_kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_create_a2a_app_uses_lifespan_for_adapter() -> None:
    flow = DummyFlow()
    adapter = A2AServerAdapter(
        flow,
        agent_card=_default_agent_card(),
        agent_url="https://main-agent.example",
    )
    app = create_a2a_app(adapter, include_docs=False)

    assert flow.started is False

    async with app.router.lifespan_context(app):
        assert flow.started is True

    assert flow.started is False
    assert flow.stop_calls == 1
