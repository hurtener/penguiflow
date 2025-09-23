"""Tests for Pydantic message types."""

from __future__ import annotations

import time

from penguiflow.types import Headers, Message


def test_headers_defaults() -> None:
    headers = Headers(tenant="acme")
    assert headers.tenant == "acme"
    assert headers.topic is None
    assert headers.priority == 0


def test_message_metadata_defaults() -> None:
    headers = Headers(tenant="penguin", topic="metrics")
    msg1 = Message(payload={"foo": "bar"}, headers=headers)
    time.sleep(0.001)
    msg2 = Message(payload={"foo": "baz"}, headers=headers)

    assert msg1.trace_id != msg2.trace_id
    assert msg1.ts < msg2.ts
    assert msg1.deadline_s is None
