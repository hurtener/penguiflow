"""Observability tests for remote transport instrumentation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from penguiflow import Headers, Message, RemoteNode, create
from penguiflow.errors import FlowError
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


def _payloads(store: RecordingStateStore, kind: str) -> list[dict[str, Any]]:
    return [event.payload for event in store.events if event.kind == kind]


class ObservabilityUnaryTransport(RemoteTransport):
    def __init__(self) -> None:
        self.requests: list[RemoteCallRequest] = []

    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        self.requests.append(request)
        return RemoteCallResult(
            result={"answer": 42},
            context_id="ctx-observe",
            task_id="task-observe",
            agent_url=request.agent_url,
        )

    async def stream(self, request: RemoteCallRequest):  # pragma: no cover - unary path
        raise AssertionError("stream() should not be called for unary transport")

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        # Unary transport never schedules long-lived work.
        return None


class ObservabilityStreamTransport(RemoteTransport):
    def __init__(self) -> None:
        self.requests: list[RemoteCallRequest] = []

    async def send(self, request: RemoteCallRequest):  # pragma: no cover
        raise AssertionError("send() should not be called for streaming transport")

    async def stream(self, request: RemoteCallRequest):
        self.requests.append(request)
        yield RemoteStreamEvent(
            text="chunk-one",
            context_id="ctx-stream",
            task_id="task-stream",
            agent_url=request.agent_url,
            meta={"index": 0},
        )
        yield RemoteStreamEvent(
            result={"final": True},
            done=True,
            context_id="ctx-stream",
            task_id="task-stream",
            agent_url=request.agent_url,
        )

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        return None


class CancelTrackingTransport(RemoteTransport):
    def __init__(self) -> None:
        self.requests: list[RemoteCallRequest] = []
        self.cancelled: list[tuple[str, str]] = []
        self._finish = asyncio.Event()
        self.cancelled_event = asyncio.Event()

    async def send(self, request: RemoteCallRequest):  # pragma: no cover
        raise AssertionError("send() should not be called")

    async def stream(self, request: RemoteCallRequest):
        self.requests.append(request)
        yield RemoteStreamEvent(
            text="work-in-flight",
            context_id="ctx-cancel",
            task_id="task-cancel",
            agent_url=request.agent_url,
        )
        await self._finish.wait()

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        self.cancelled.append((agent_url, task_id))
        self.cancelled_event.set()
        self._finish.set()


class ExplodingTransport(RemoteTransport):
    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        raise RuntimeError("remote boom")

    async def stream(self, request: RemoteCallRequest):  # pragma: no cover - unary path
        raise AssertionError("stream() should not be called")

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_remote_unary_emits_observability_events() -> None:
    store = RecordingStateStore()
    transport = ObservabilityUnaryTransport()
    node = RemoteNode(
        transport=transport,
        skill="SearchAgent.find",
        agent_url="https://agent.example",
        name="remote-search",
    )
    flow = create(node.to(), state_store=store)
    flow.run()

    message = Message(
        payload={"query": "observability"},
        headers=Headers(tenant="acme"),
    )

    try:
        await flow.emit(message)
        result = await flow.fetch()
    finally:
        await flow.stop()

    assert result == {"answer": 42}

    start_payloads = _payloads(store, "remote_call_start")
    assert len(start_payloads) == 1
    start_payload = start_payloads[0]
    assert start_payload["remote_skill"] == "SearchAgent.find"
    assert start_payload["remote_agent_url"] == "https://agent.example"
    assert start_payload["remote_request_bytes"] > 0

    success_payloads = _payloads(store, "remote_call_success")
    assert len(success_payloads) == 1
    success_payload = success_payloads[0]
    assert success_payload["remote_status"] == "success"
    assert success_payload["remote_context_id"] == "ctx-observe"
    assert success_payload["remote_task_id"] == "task-observe"
    assert success_payload["remote_response_bytes"] > 0
    assert success_payload["remote_stream_events"] == 0
    assert success_payload["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_remote_streaming_emits_chunk_metrics() -> None:
    store = RecordingStateStore()
    transport = ObservabilityStreamTransport()
    node = RemoteNode(
        transport=transport,
        skill="Writer.draft",
        agent_url="https://agent.example",
        name="remote-writer",
        streaming=True,
    )
    flow = create(node.to(), state_store=store)
    flow.run()

    message = Message(payload={"prompt": "write"}, headers=Headers(tenant="acme"))

    try:
        await flow.emit(message)
        chunk_msg = await flow.fetch()
        assert isinstance(chunk_msg, Message)
        final_result = await flow.fetch()
    finally:
        await flow.stop()

    assert final_result == {"final": True}

    stream_payloads = _payloads(store, "remote_stream_event")
    assert len(stream_payloads) == 2
    first_chunk = stream_payloads[0]
    assert first_chunk["remote_stream_seq"] == 0
    assert first_chunk["remote_chunk_bytes"] > 0
    assert first_chunk["remote_chunk_meta_keys"] == ["index"]

    success_payload = _payloads(store, "remote_call_success")[0]
    assert success_payload["remote_status"] == "success"
    assert success_payload["remote_stream_events"] == 2
    assert success_payload["remote_response_bytes"] > 0


@pytest.mark.asyncio
async def test_remote_cancel_records_reason_and_latency() -> None:
    store = RecordingStateStore()
    transport = CancelTrackingTransport()
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
        trace_id = message.trace_id
        cancelled = await flow.cancel(trace_id)
        assert cancelled
        await asyncio.wait_for(transport.cancelled_event.wait(), timeout=1.0)
    finally:
        await flow.stop()

    assert transport.cancelled == [("https://agent.example", "task-cancel")]
    cancel_payloads = _payloads(store, "remote_call_cancelled")
    assert len(cancel_payloads) == 1
    cancel_payload = cancel_payloads[0]
    assert cancel_payload["remote_cancel_reason"] in {"trace_cancel", "pre_cancelled"}
    assert cancel_payload["remote_status"] == "cancelled"
    assert cancel_payload["remote_task_id"] == "task-cancel"
    assert cancel_payload["latency_ms"] >= 0
    assert not _payloads(store, "remote_call_success")


@pytest.mark.asyncio
async def test_remote_error_publishes_failure_event() -> None:
    store = RecordingStateStore()
    transport = ExplodingTransport()
    node = RemoteNode(
        transport=transport,
        skill="Search.fail",
        agent_url="https://agent.example",
        name="remote-error",
    )
    flow = create(node.to(), state_store=store, emit_errors_to_rookery=True)
    flow.run()

    message = Message(payload={"query": "boom"}, headers=Headers(tenant="acme"))

    try:
        await flow.emit(message)
        result = await flow.fetch()
    finally:
        await flow.stop()

    assert isinstance(result, FlowError)
    error_payloads = _payloads(store, "remote_call_error")
    assert len(error_payloads) == 1
    error_payload = error_payloads[0]
    assert error_payload["remote_status"] == "error"
    assert "remote boom" in error_payload["remote_error"]
    assert error_payload["remote_agent_url"] == "https://agent.example"
