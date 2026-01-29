from __future__ import annotations

import pytest
from pydantic import ValidationError

from penguiflow_a2a.models import (
    DataPart,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)


def test_part_oneof_validation() -> None:
    with pytest.raises(ValidationError):
        Part(text="hi", data=DataPart(data={"a": 1}))


def test_stream_response_oneof_validation() -> None:
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.SUBMITTED),
    )
    message = Message(
        message_id="msg-1",
        role=Role.USER,
        parts=[Part(text="hello")],
    )
    with pytest.raises(ValidationError):
        StreamResponse(task=task, message=message)


def test_enum_and_alias_serialization() -> None:
    message = Message(
        message_id="msg-1",
        role=Role.USER,
        parts=[Part(text="hello")],
        context_id="ctx-1",
    )
    payload = message.model_dump(by_alias=True)
    assert payload["messageId"] == "msg-1"
    assert payload["contextId"] == "ctx-1"
    assert payload["role"] == "user"

    status = TaskStatus(state=TaskState.INPUT_REQUIRED)
    status_payload = status.model_dump(by_alias=True)
    assert status_payload["state"] == "input-required"
