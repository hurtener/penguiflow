from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None

    model_config = ConfigDict(extra="allow")


class A2AError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        type_uri: str,
        title: str,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status_code = status_code
        self.type_uri = type_uri
        self.title = title
        self.detail = detail
        self.extra = extra or {}

    def to_problem_details(self) -> ProblemDetails:
        payload: dict[str, Any] = {
            "type": self.type_uri,
            "title": self.title,
            "status": self.status_code,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.extra:
            payload.update(self.extra)
        return ProblemDetails.model_validate(payload)


class A2ARequestValidationError(A2AError):
    def __init__(self, detail: str, *, status_code: int = 422) -> None:
        super().__init__(
            status_code=status_code,
            type_uri="https://a2a-protocol.org/errors/invalid-request",
            title="Invalid request",
            detail=detail,
        )


class TaskNotFoundError(A2AError):
    def __init__(self, task_id: str) -> None:
        super().__init__(
            status_code=404,
            type_uri="https://a2a-protocol.org/errors/task-not-found",
            title="Task not found",
            detail=f"Task '{task_id}' was not found.",
        )


class TaskNotCancelableError(A2AError):
    def __init__(self, task_id: str) -> None:
        super().__init__(
            status_code=409,
            type_uri="https://a2a-protocol.org/errors/task-not-cancelable",
            title="Task not cancelable",
            detail=f"Task '{task_id}' cannot be cancelled.",
        )


class PushNotificationNotSupportedError(A2AError):
    def __init__(self) -> None:
        super().__init__(
            status_code=400,
            type_uri="https://a2a-protocol.org/errors/push-notification-not-supported",
            title="Push notifications not supported",
        )


class UnsupportedOperationError(A2AError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=400,
            type_uri="https://a2a-protocol.org/errors/unsupported-operation",
            title="Unsupported operation",
            detail=detail,
        )


class ContentTypeNotSupportedError(A2AError):
    def __init__(self, content_type: str | None) -> None:
        detail = "Content-Type header is required."
        if content_type:
            detail = f"Content-Type '{content_type}' is not supported."
        super().__init__(
            status_code=415,
            type_uri="https://a2a-protocol.org/errors/content-type-not-supported",
            title="Content-Type not supported",
            detail=detail,
        )


class InvalidAgentResponseError(A2AError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=502,
            type_uri="https://a2a-protocol.org/errors/invalid-agent-response",
            title="Invalid agent response",
            detail=detail,
        )


class ExtendedAgentCardNotConfiguredError(A2AError):
    def __init__(self) -> None:
        super().__init__(
            status_code=400,
            type_uri="https://a2a-protocol.org/errors/extended-agent-card-not-configured",
            title="Extended Agent Card not configured",
        )


class ExtensionSupportRequiredError(A2AError):
    def __init__(self, required_extensions: list[str]) -> None:
        super().__init__(
            status_code=400,
            type_uri="https://a2a-protocol.org/errors/extension-support-required",
            title="Extension support required",
            detail="Required extensions are missing from the request.",
            extra={"requiredExtensions": required_extensions},
        )


class VersionNotSupportedError(A2AError):
    def __init__(self, requested_version: str, supported_versions: tuple[str, ...]) -> None:
        super().__init__(
            status_code=400,
            type_uri="https://a2a-protocol.org/errors/version-not-supported",
            title="Version not supported",
            detail=f"Requested version '{requested_version}' is not supported.",
            extra={"supportedVersions": list(supported_versions)},
        )
