"""Tests for A2A errors."""

from __future__ import annotations

import pytest

from penguiflow_a2a.errors import (
    A2AError,
    A2ARequestValidationError,
    ContentTypeNotSupportedError,
    ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError,
    InvalidAgentResponseError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)


@pytest.mark.parametrize(
    ("error", "status", "type_uri", "title"),
    [
        (
            A2ARequestValidationError("bad"),
            422,
            "https://a2a-protocol.org/errors/invalid-request",
            "Invalid request",
        ),
        (
            TaskNotFoundError("task-1"),
            404,
            "https://a2a-protocol.org/errors/task-not-found",
            "Task not found",
        ),
        (
            TaskNotCancelableError("task-2"),
            409,
            "https://a2a-protocol.org/errors/task-not-cancelable",
            "Task not cancelable",
        ),
        (
            PushNotificationNotSupportedError(),
            400,
            "https://a2a-protocol.org/errors/push-notification-not-supported",
            "Push notifications not supported",
        ),
        (
            UnsupportedOperationError("nope"),
            400,
            "https://a2a-protocol.org/errors/unsupported-operation",
            "Unsupported operation",
        ),
        (
            ContentTypeNotSupportedError("text/plain"),
            415,
            "https://a2a-protocol.org/errors/content-type-not-supported",
            "Content-Type not supported",
        ),
        (
            InvalidAgentResponseError("bad"),
            502,
            "https://a2a-protocol.org/errors/invalid-agent-response",
            "Invalid agent response",
        ),
        (
            ExtendedAgentCardNotConfiguredError(),
            400,
            "https://a2a-protocol.org/errors/extended-agent-card-not-configured",
            "Extended Agent Card not configured",
        ),
    ],
)
def test_problem_details_base_fields(
    error: A2AError, status: int, type_uri: str, title: str
) -> None:
    """Problem details base fields."""
    details = error.to_problem_details().model_dump(exclude_none=True)
    assert details["status"] == status
    assert details["type"] == type_uri
    assert details["title"] == title


def test_problem_details_extension_support_required() -> None:
    """Problem details extension support required."""
    error = ExtensionSupportRequiredError(["ext://a", "ext://b"])
    details = error.to_problem_details().model_dump(exclude_none=True)
    assert details["requiredExtensions"] == ["ext://a", "ext://b"]


def test_problem_details_version_not_supported() -> None:
    """Problem details version not supported."""
    error = VersionNotSupportedError("9.9", ("0.3",))
    details = error.to_problem_details().model_dump(exclude_none=True)
    assert details["supportedVersions"] == ["0.3"]
