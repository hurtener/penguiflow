"""Tests for the distribution hooks (state store + message bus)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from penguiflow import Headers, Message, Node, create
from penguiflow.bus import BusEnvelope, MessageBus
from penguiflow.state import RemoteBinding, StateStore, StoredEvent


async def _echo(message: Message, ctx: Any) -> Message:
    return message


echo_node = Node(_echo, name="echo")


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


class FailingStateStore(RecordingStateStore):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def save_event(self, event: StoredEvent) -> None:
        if not self._failed:
            self._failed = True
            raise RuntimeError("boom")
        await super().save_event(event)


class RecordingBus(MessageBus):
    def __init__(self) -> None:
        self.envelopes: list[BusEnvelope] = []

    async def publish(self, envelope: BusEnvelope) -> None:
        self.envelopes.append(envelope)


class FailingBus(RecordingBus):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def publish(self, envelope: BusEnvelope) -> None:
        if not self._failed:
            self._failed = True
            raise RuntimeError("bus-down")
        await super().publish(envelope)


@pytest.mark.asyncio
async def test_state_store_receives_events_and_history() -> None:
    store = RecordingStateStore()
    flow = create(echo_node.to(), state_store=store)
    flow.run()
    try:
        message = Message(payload={"ok": True}, headers=Headers(tenant="acme"))
        await flow.emit(message)
        result = await flow.fetch()
        assert isinstance(result, Message)
        assert result.payload == {"ok": True}

        history = await flow.load_history(message.trace_id)
        assert history
        assert history[0].trace_id == message.trace_id
    finally:
        await flow.stop()


@pytest.mark.asyncio
async def test_state_store_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    store = FailingStateStore()
    flow = create(echo_node.to(), state_store=store)
    flow.run()
    caplog.set_level(logging.ERROR, logger="penguiflow.core")
    try:
        message = Message(payload={"boom": 1}, headers=Headers(tenant="acme"))
        await flow.emit(message)
        await flow.fetch()
    finally:
        await flow.stop()
    assert any("state_store_save_failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_message_bus_records_envelopes() -> None:
    bus = RecordingBus()
    flow = create(echo_node.to(), message_bus=bus)
    flow.run()
    message = Message(payload={"hello": "world"}, headers=Headers(tenant="acme"))
    other = Message(payload={"mode": "nowait"}, headers=Headers(tenant="acme"))
    try:
        await flow.emit(message)
        await flow.fetch()

        flow.emit_nowait(other)
        await flow.fetch()
    finally:
        await flow.stop()

    assert bus.envelopes
    trace_ids = {envelope.trace_id for envelope in bus.envelopes}
    assert message.trace_id in trace_ids
    assert other.trace_id in trace_ids
    assert any(envelope.target == "Rookery" for envelope in bus.envelopes)


@pytest.mark.asyncio
async def test_message_bus_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    bus = FailingBus()
    flow = create(echo_node.to(), message_bus=bus)
    flow.run()
    caplog.set_level(logging.ERROR, logger="penguiflow.core")
    message = Message(payload={"warn": True}, headers=Headers(tenant="acme"))
    try:
        flow.emit_nowait(message)
        await flow.fetch()
    finally:
        await flow.stop()
    assert any(
        "message_bus_publish_failed" in record.message for record in caplog.records
    )
