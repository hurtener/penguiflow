"""gRPC binding of the A2A protocol."""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

from pydantic import BaseModel
from typing_extensions import override

try:
    import grpc
    from google.protobuf import empty_pb2, json_format
    from google.rpc import error_details_pb2, status_pb2
    from grpc_status import rpc_status
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    msg = (
        "gRPC dependencies are required for the A2A gRPC binding. Install "
        "penguiflow-lite[a2a-grpc]."
    )
    raise RuntimeError(msg) from exc

from ..errors import (
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
from ..grpc import a2a_pb2, a2a_pb2_grpc
from ..models import (
    SendMessageRequest,
    StreamResponse,
    TaskPushNotificationConfig,
    TaskState,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from google.protobuf.message import Message

    from ..core import A2AService

ModelT = TypeVar("ModelT", bound=BaseModel)
MessageT = TypeVar("MessageT", bound="Message")
_Context = grpc.aio.ServicerContext[Any, Any]

_TASK_STATE_TO_STR: dict[int, str] = {
    a2a_pb2.TaskState.TASK_STATE_UNSPECIFIED: TaskState.UNSPECIFIED.value,
    a2a_pb2.TaskState.TASK_STATE_SUBMITTED: TaskState.SUBMITTED.value,
    a2a_pb2.TaskState.TASK_STATE_WORKING: TaskState.WORKING.value,
    a2a_pb2.TaskState.TASK_STATE_COMPLETED: TaskState.COMPLETED.value,
    a2a_pb2.TaskState.TASK_STATE_FAILED: TaskState.FAILED.value,
    a2a_pb2.TaskState.TASK_STATE_CANCELLED: TaskState.CANCELLED.value,
    a2a_pb2.TaskState.TASK_STATE_INPUT_REQUIRED: (
        TaskState.INPUT_REQUIRED.value
    ),
    a2a_pb2.TaskState.TASK_STATE_REJECTED: TaskState.REJECTED.value,
    a2a_pb2.TaskState.TASK_STATE_AUTH_REQUIRED: TaskState.AUTH_REQUIRED.value,
}
_ROLE_TO_STR: dict[int, str] = {
    a2a_pb2.Role.ROLE_USER: "user",
    a2a_pb2.Role.ROLE_AGENT: "agent",
}
_STR_TO_TASK_STATE: dict[str, int] = {
    value: key for key, value in _TASK_STATE_TO_STR.items()
}
_STR_TO_ROLE: dict[str, int] = {
    value: key for key, value in _ROLE_TO_STR.items()
}


def _normalize_proto_enums(data: object) -> object:
    if isinstance(data, list):
        return [_normalize_proto_enums(item) for item in data]
    if isinstance(data, dict):
        normalized: dict[str, object] = {}
        for key, value in data.items():
            if key == "state" and isinstance(value, int):
                normalized[key] = _TASK_STATE_TO_STR.get(value, value)
            elif key == "role" and isinstance(value, int):
                normalized[key] = _ROLE_TO_STR.get(value, value)
            else:
                normalized[key] = _normalize_proto_enums(value)
        return normalized
    return data


def _normalize_json_enums(data: object) -> object:
    if isinstance(data, list):
        return [_normalize_json_enums(item) for item in data]
    if isinstance(data, dict):
        return _normalize_json_dict(data)
    return data


def _normalize_json_dict(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if key == "state" and isinstance(value, str):
            normalized[key] = _STR_TO_TASK_STATE.get(value, value)
        elif key == "role" and isinstance(value, str):
            normalized[key] = _STR_TO_ROLE.get(value, value)
        else:
            normalized[key] = _normalize_json_enums(value)
    return normalized


def _proto_to_model(message: Message, model_cls: type[ModelT]) -> ModelT:
    payload = json_format.MessageToDict(
        message,
        preserving_proto_field_name=False,
        use_integers_for_enums=True,
    )
    return model_cls.model_validate(_normalize_proto_enums(payload))


def _model_to_proto(model: BaseModel, proto_cls: type[MessageT]) -> MessageT:
    payload = _normalize_json_dict(
        model.model_dump(by_alias=True, exclude_none=True)
    )
    message = proto_cls()
    json_format.ParseDict(payload, message, ignore_unknown_fields=True)
    return message


def _stream_response_to_proto(event: StreamResponse) -> a2a_pb2.StreamResponse:
    return _model_to_proto(event, a2a_pb2.StreamResponse)


def _parse_task_name(name: str) -> str:
    if name.startswith("tasks/"):
        parts = name.split("/")
        if len(parts) >= 2:
            return parts[1]
    return name


def _parse_push_config_name(name: str) -> tuple[str, str]:
    parts = name.split("/")
    if (
        len(parts) >= 4
        and parts[0] == "tasks"
        and parts[2] == "pushNotificationConfigs"
    ):
        return parts[1], parts[3]
    msg = "push notification config name is invalid"
    raise A2ARequestValidationError(msg)


def _metadata_to_headers(context: _Context) -> dict[str, str]:
    # Binary (``-bin``) metadata never carries A2A headers.
    metadata = context.invocation_metadata() or ()
    return {
        str(key).lower(): value
        for key, value in metadata
        if isinstance(value, str)
    }


def _parse_extensions(header: str | None) -> list[str]:
    if not header:
        return []
    return [value.strip() for value in header.split(",") if value.strip()]


def _validate_service_params(
    service: A2AService, context: _Context
) -> list[str]:
    headers = _metadata_to_headers(context)
    version = headers.get("a2a-version")
    service.validate_version(version)
    extensions = _parse_extensions(headers.get("a2a-extensions"))
    service.validate_extensions(extensions)
    return extensions


def _error_reason(exc: Exception) -> str:
    mapping = {
        TaskNotFoundError: "TASK_NOT_FOUND",
        TaskNotCancelableError: "TASK_NOT_CANCELABLE",
        PushNotificationNotSupportedError: "PUSH_NOTIFICATION_NOT_SUPPORTED",
        UnsupportedOperationError: "UNSUPPORTED_OPERATION",
        ContentTypeNotSupportedError: "CONTENT_TYPE_NOT_SUPPORTED",
        InvalidAgentResponseError: "INVALID_AGENT_RESPONSE",
        ExtendedAgentCardNotConfiguredError: (
            "EXTENDED_AGENT_CARD_NOT_CONFIGURED"
        ),
        ExtensionSupportRequiredError: "EXTENSION_SUPPORT_REQUIRED",
        VersionNotSupportedError: "VERSION_NOT_SUPPORTED",
        A2ARequestValidationError: "INVALID_REQUEST",
    }
    for error_type, reason in mapping.items():
        if isinstance(exc, error_type):
            return reason
    return "INTERNAL"


def _grpc_status_code(exc: Exception) -> grpc.StatusCode:
    mapping = {
        TaskNotFoundError: grpc.StatusCode.NOT_FOUND,
        TaskNotCancelableError: grpc.StatusCode.FAILED_PRECONDITION,
        PushNotificationNotSupportedError: grpc.StatusCode.UNIMPLEMENTED,
        UnsupportedOperationError: grpc.StatusCode.UNIMPLEMENTED,
        ContentTypeNotSupportedError: grpc.StatusCode.INVALID_ARGUMENT,
        InvalidAgentResponseError: grpc.StatusCode.INTERNAL,
        ExtendedAgentCardNotConfiguredError: (
            grpc.StatusCode.FAILED_PRECONDITION
        ),
        ExtensionSupportRequiredError: grpc.StatusCode.FAILED_PRECONDITION,
        VersionNotSupportedError: grpc.StatusCode.UNIMPLEMENTED,
        A2ARequestValidationError: grpc.StatusCode.INVALID_ARGUMENT,
    }
    for error_type, status in mapping.items():
        if isinstance(exc, error_type):
            return status
    return grpc.StatusCode.INTERNAL


async def _abort_with_error(context: _Context, exc: Exception) -> NoReturn:
    """Abort the RPC with the A2A error details of ``exc``."""
    status_code = _grpc_status_code(exc)
    reason = _error_reason(exc)
    metadata: dict[str, str] = {"reason": reason}
    if isinstance(exc, A2AError):
        metadata["type"] = exc.type_uri
        if exc.detail:
            metadata["detail"] = exc.detail
    info = error_details_pb2.ErrorInfo(
        reason=reason,
        domain="a2a-protocol.org",
        metadata=metadata,
    )
    message = str(exc)
    status = status_pb2.Status(code=status_code.value[0], message=message)
    status.details.add().Pack(info)
    await context.abort_with_status(rpc_status.to_status(status))


def _list_tasks_page_size(request: a2a_pb2.ListTasksRequest) -> int:
    """Return the requested page size (default 50, at most 100).

    Raises:
        A2ARequestValidationError: If the page size is out of range.
    """
    page_size = request.page_size if request.HasField("page_size") else 50
    if page_size < 1 or page_size > 100:
        msg = "pageSize must be between 1 and 100"
        raise A2ARequestValidationError(msg)
    return page_size


def _push_configs_page_size(
    request: a2a_pb2.ListTaskPushNotificationConfigRequest,
) -> int:
    """Return the requested page size (default 50).

    Raises:
        A2ARequestValidationError: If the page size is below 1.
    """
    page_size = request.page_size or 50
    if page_size < 1:
        msg = "pageSize must be >= 1"
        raise A2ARequestValidationError(msg)
    return page_size


class A2AGrpcServicer(a2a_pb2_grpc.A2AServiceServicer):
    """gRPC binding of the A2A protocol over an :class:`A2AService`."""

    def __init__(self, service: A2AService) -> None:
        """Serve the A2A operations of ``service``."""
        self._service = service

    @override
    async def SendMessage(
        self, request: a2a_pb2.SendMessageRequest, context: _Context
    ) -> a2a_pb2.SendMessageResponse:
        try:
            extensions = _validate_service_params(self._service, context)
            payload = _proto_to_model(request, SendMessageRequest)
            response = await self._service.send_message(
                payload,
                tenant=request.tenant or None,
                requested_extensions=extensions,
            )
            result = _model_to_proto(response, a2a_pb2.SendMessageResponse)
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def SendStreamingMessage(
        self, request: a2a_pb2.SendMessageRequest, context: _Context
    ) -> AsyncIterator[a2a_pb2.StreamResponse]:
        try:
            extensions = _validate_service_params(self._service, context)
            payload = _proto_to_model(request, SendMessageRequest)
            task, queue, unsubscribe = await self._service.stream_message(
                payload,
                tenant=request.tenant or None,
                requested_extensions=extensions,
            )
        except Exception as exc:
            await _abort_with_error(context, exc)

        try:
            first = StreamResponse(task=task)
            yield _stream_response_to_proto(first)
            while True:
                event = await queue.get()
                yield _stream_response_to_proto(event)
                if event.status_update and event.status_update.final:
                    break
        finally:
            await unsubscribe()

    @override
    async def GetTask(
        self, request: a2a_pb2.GetTaskRequest, context: _Context
    ) -> a2a_pb2.Task:
        try:
            _validate_service_params(self._service, context)
            task_id = _parse_task_name(request.name)
            history_length = (
                request.history_length
                if request.HasField("history_length")
                else None
            )
            task = await self._service.get_task(
                task_id, history_length=history_length
            )
            result = _model_to_proto(task, a2a_pb2.Task)
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def ListTasks(
        self, request: a2a_pb2.ListTasksRequest, context: _Context
    ) -> a2a_pb2.ListTasksResponse:
        try:
            _validate_service_params(self._service, context)
            page_size = _list_tasks_page_size(request)
            history_length = (
                request.history_length
                if request.HasField("history_length")
                else None
            )
            include_artifacts = (
                request.include_artifacts
                if request.HasField("include_artifacts")
                else False
            )
            status = None
            if request.status != a2a_pb2.TaskState.TASK_STATE_UNSPECIFIED:
                status_value = _TASK_STATE_TO_STR.get(request.status)
                status = TaskState(status_value) if status_value else None
            status_after = None
            if request.HasField("status_timestamp_after"):
                status_after = request.status_timestamp_after.ToDatetime(
                    tzinfo=UTC
                )
            response = await self._service.list_tasks(
                context_id=request.context_id or None,
                status=status,
                page_size=page_size,
                page_token=request.page_token or None,
                history_length=history_length,
                status_timestamp_after=status_after,
                include_artifacts=include_artifacts,
            )
            result = _model_to_proto(response, a2a_pb2.ListTasksResponse)
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def CancelTask(
        self, request: a2a_pb2.CancelTaskRequest, context: _Context
    ) -> a2a_pb2.Task:
        try:
            _validate_service_params(self._service, context)
            task_id = _parse_task_name(request.name)
            task = await self._service.cancel_task(task_id)
            result = _model_to_proto(task, a2a_pb2.Task)
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def SubscribeToTask(
        self, request: a2a_pb2.SubscribeToTaskRequest, context: _Context
    ) -> AsyncIterator[a2a_pb2.StreamResponse]:
        try:
            _validate_service_params(self._service, context)
            task_id = _parse_task_name(request.name)
            task, queue, unsubscribe = await self._service.subscribe_task(
                task_id
            )
        except Exception as exc:
            await _abort_with_error(context, exc)

        try:
            first = StreamResponse(task=task)
            yield _stream_response_to_proto(first)
            while True:
                event = await queue.get()
                yield _stream_response_to_proto(event)
                if event.status_update and event.status_update.final:
                    break
        finally:
            await unsubscribe()

    @override
    async def SetTaskPushNotificationConfig(
        self,
        request: a2a_pb2.SetTaskPushNotificationConfigRequest,
        context: _Context,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        try:
            _validate_service_params(self._service, context)
            task_id = _parse_task_name(request.parent)
            config = _proto_to_model(
                request.config, TaskPushNotificationConfig
            )
            response = await self._service.set_task_push_notification_config(
                task_id,
                config_id=request.config_id,
                config=config,
            )
            result = _model_to_proto(
                response, a2a_pb2.TaskPushNotificationConfig
            )
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def GetTaskPushNotificationConfig(
        self,
        request: a2a_pb2.GetTaskPushNotificationConfigRequest,
        context: _Context,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        try:
            _validate_service_params(self._service, context)
            task_id, config_id = _parse_push_config_name(request.name)
            response = await self._service.get_task_push_notification_config(
                task_id, config_id=config_id
            )
            result = _model_to_proto(
                response, a2a_pb2.TaskPushNotificationConfig
            )
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def ListTaskPushNotificationConfig(
        self,
        request: a2a_pb2.ListTaskPushNotificationConfigRequest,
        context: _Context,
    ) -> a2a_pb2.ListTaskPushNotificationConfigResponse:
        try:
            _validate_service_params(self._service, context)
            task_id = _parse_task_name(request.parent)
            page_size = _push_configs_page_size(request)
            response = await self._service.list_task_push_notification_configs(
                task_id,
                page_size=page_size,
                page_token=request.page_token or None,
            )
            result = _model_to_proto(
                response, a2a_pb2.ListTaskPushNotificationConfigResponse
            )
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def DeleteTaskPushNotificationConfig(
        self,
        request: a2a_pb2.DeleteTaskPushNotificationConfigRequest,
        context: _Context,
    ) -> empty_pb2.Empty:
        try:
            _validate_service_params(self._service, context)
            task_id, config_id = _parse_push_config_name(request.name)
            await self._service.delete_task_push_notification_config(
                task_id, config_id=config_id
            )
            result = empty_pb2.Empty()
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result

    @override
    async def GetExtendedAgentCard(
        self, request: a2a_pb2.GetExtendedAgentCardRequest, context: _Context
    ) -> a2a_pb2.AgentCard:
        if not self._service.is_extended_agent_card_authorized(
            _metadata_to_headers(context)
        ):
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "Unauthorized"
            )
        try:
            _validate_service_params(self._service, context)
            card = await self._service.get_extended_agent_card()
            result = _model_to_proto(card, a2a_pb2.AgentCard)
        except Exception as exc:
            await _abort_with_error(context, exc)
        return result


def add_a2a_grpc_service(server: grpc.aio.Server, service: A2AService) -> None:
    """Register the A2A gRPC servicer for ``service`` on ``server``."""
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(
        A2AGrpcServicer(service), server
    )


__all__ = ["A2AGrpcServicer", "add_a2a_grpc_service"]
