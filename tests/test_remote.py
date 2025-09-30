from __future__ import annotations

import asyncio

import pytest

from penguiflow import Headers, Message, RemoteNode, StreamChunk, create
from penguiflow.remote import (
    RemoteCallRequest,
    RemoteCallResult,
    RemoteStreamEvent,
    RemoteTransport,
)
from penguiflow.state import RemoteBinding, StateStore, StoredEvent


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


class UnaryTransport(RemoteTransport):
    def __init__(self) -> None:
        self.sent: list[RemoteCallRequest] = []
        self.cancelled: list[tuple[str, str]] = []

    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        self.sent.append(request)
        return RemoteCallResult(
            result={"answer": 42},
            context_id="ctx-send",
            task_id="task-send",
            agent_url=request.agent_url,
        )

    async def stream(self, request: RemoteCallRequest):  # pragma: no cover - unary path
        raise AssertionError("stream() should not be called in unary tests")

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        self.cancelled.append((agent_url, task_id))


class StreamingTransport(RemoteTransport):
    def __init__(self) -> None:
        self.requests: list[RemoteCallRequest] = []
        self.cancelled: list[tuple[str, str]] = []
        self._finish = asyncio.Event()

    async def send(self, request: RemoteCallRequest):
        raise AssertionError("send() should not be called in streaming tests")

    async def stream(self, request: RemoteCallRequest):
        self.requests.append(request)
        yield RemoteStreamEvent(
            text="chunk-1",
            context_id="ctx-stream",
            task_id="task-stream",
            agent_url=request.agent_url,
            meta={"index": 0},
        )
        await self._finish.wait()
        yield RemoteStreamEvent(
            result={"final": True},
            done=True,
            task_id="task-stream",
            agent_url=request.agent_url,
        )

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        self.cancelled.append((agent_url, task_id))
        self._finish.set()


class CancelAwareTransport(RemoteTransport):
    def __init__(self) -> None:
        self.requests: list[RemoteCallRequest] = []
        self.cancelled: list[tuple[str, str]] = []
        self._finish = asyncio.Event()
        self.cancelled_event = asyncio.Event()

    async def send(self, request: RemoteCallRequest):
        raise AssertionError("send() should not be called")

    async def stream(self, request: RemoteCallRequest):
        self.requests.append(request)
        yield RemoteStreamEvent(
            text="to-cancel",
            context_id="ctx-cancel",
            task_id="task-cancel",
            agent_url=request.agent_url,
        )
        await self._finish.wait()
        yield RemoteStreamEvent(
            result={"final": "ignored"},
            done=True,
            task_id="task-cancel",
            agent_url=request.agent_url,
        )

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        self.cancelled.append((agent_url, task_id))
        self.cancelled_event.set()
        self._finish.set()


@pytest.mark.asyncio
async def test_remote_node_unary_call_records_binding() -> None:
    store = RecordingStateStore()
    transport = UnaryTransport()
    node = RemoteNode(
        transport=transport,
        skill="SearchAgent.find",
        agent_url="https://agent.example",
        name="remote-search",
    )
    flow = create(node.to(), state_store=store)
    flow.run()

    message = Message(payload={"query": "penguins"}, headers=Headers(tenant="acme"))

    try:
        await flow.emit(message)
        result = await flow.fetch()
    finally:
        await flow.stop()

    assert result == {"answer": 42}
    assert transport.sent
    sent_message = transport.sent[0].message
    assert sent_message.payload == message.payload
    assert sent_message.headers == message.headers
    assert store.bindings == [
        RemoteBinding(
            trace_id=message.trace_id,
            context_id="ctx-send",
            task_id="task-send",
            agent_url="https://agent.example",
        )
    ]


@pytest.mark.asyncio
async def test_remote_node_streams_chunks_and_returns_final_result() -> None:
    store = RecordingStateStore()
    transport = StreamingTransport()
    node = RemoteNode(
        transport=transport,
        skill="Writer.draft",
        agent_url="https://agent.example",
        name="remote-writer",
        streaming=True,
    )
    flow = create(node.to(), state_store=store)
    flow.run()

    message = Message(payload={"prompt": "hello"}, headers=Headers(tenant="acme"))

    try:
        await flow.emit(message)
        chunk_msg = await flow.fetch()
        assert isinstance(chunk_msg, Message)
        chunk = chunk_msg.payload
        assert isinstance(chunk, StreamChunk)
        assert chunk.text == "chunk-1"
        transport._finish.set()
        final = await flow.fetch()
    finally:
        await flow.stop()

    assert final == {"final": True}
    assert store.bindings[0].task_id == "task-stream"
    assert not transport.cancelled


@pytest.mark.asyncio
async def test_remote_node_cancels_remote_task_on_trace_cancel() -> None:
    store = RecordingStateStore()
    transport = CancelAwareTransport()
    node = RemoteNode(
        transport=transport,
        skill="Planner.plan",
        agent_url="https://agent.example",
        name="remote-planner",
        streaming=True,
    )
    flow = create(node.to(), state_store=store)
    flow.run()

    message = Message(payload={"goal": "cancel"}, headers=Headers(tenant="acme"))

    try:
        await flow.emit(message)
        first = await flow.fetch()
        assert isinstance(first, Message)
        chunk = first.payload
        assert isinstance(chunk, StreamChunk)
        assert chunk.text == "to-cancel"
        trace_id = message.trace_id
        cancelled = await flow.cancel(trace_id)
        assert cancelled
        await asyncio.wait_for(transport.cancelled_event.wait(), timeout=1.0)
    finally:
        await flow.stop()

    assert transport.cancelled == [("https://agent.example", "task-cancel")]
    assert store.bindings and store.bindings[0].task_id == "task-cancel"
