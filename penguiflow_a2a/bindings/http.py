"""HTTP+JSON and JSON-RPC bindings of the A2A protocol, served by FastAPI.

The module does not use postponed annotations: FastAPI reads the endpoint
annotations at runtime.
"""

import asyncio
import json
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError

from ..config import SUPPORTED_CONTENT_TYPES
from ..core import A2AService
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
from ..models import (
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from ..sse import encode_stream_response

_A2A_JSON = "application/a2a+json"
_INVALID_REQUEST = "Request payload validation error"
_Unsubscribe = Callable[[], Coroutine[Any, Any, None]]
_JSONRPC_ERROR_CODES: dict[type[A2AError], int] = {
    TaskNotFoundError: -32001,
    TaskNotCancelableError: -32002,
    PushNotificationNotSupportedError: -32003,
    UnsupportedOperationError: -32004,
    ContentTypeNotSupportedError: -32005,
    InvalidAgentResponseError: -32006,
    ExtendedAgentCardNotConfiguredError: -32007,
    ExtensionSupportRequiredError: -32008,
    VersionNotSupportedError: -32009,
}


def _dump(model: BaseModel) -> dict[str, Any]:
    """Serialize a protocol model the way the A2A wire format expects."""
    return model.model_dump(by_alias=True, exclude_none=True)


def _a2a_json(model: BaseModel) -> JSONResponse:
    """Return ``model`` as an ``application/a2a+json`` response."""
    return JSONResponse(content=_dump(model), media_type=_A2A_JSON)


def _problem_response(exc: A2AError) -> JSONResponse:
    """Return ``exc`` as RFC 9457 problem details."""
    details = exc.to_problem_details().model_dump(exclude_none=True)
    return JSONResponse(
        status_code=exc.status_code,
        content=details,
        media_type="application/problem+json",
    )


def _validation_response(exc: ValidationError | str) -> JSONResponse:
    """Return a request validation failure as problem details."""
    return _problem_response(A2ARequestValidationError(str(exc)))


def _query_value(request: Request, name: str) -> str | None:
    """Return a query parameter of the request."""
    return request.query_params.get(name)


def _parse_bool(value: str | None, *, name: str) -> bool:
    """Parse a boolean query parameter (absent means ``False``).

    Raises:
        A2ARequestValidationError: If the value is not a boolean.
    """
    if value is None:
        return False
    lowered = value.strip().lower()
    if lowered in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "f", "no", "n", "off"}:
        return False
    msg = f"{name} must be a boolean"
    raise A2ARequestValidationError(msg)


def _parse_extensions(request: Request) -> list[str]:
    """Return the extensions requested in the ``A2A-Extensions`` header."""
    header = request.headers.get("A2A-Extensions") or request.headers.get(
        "a2a-extensions"
    )
    if not header:
        return []
    return [value.strip() for value in header.split(",") if value.strip()]


def _parse_history_length(raw: str | int | None) -> int | None:
    """Parse the ``historyLength`` parameter.

    Raises:
        A2ARequestValidationError: If it is not a non-negative integer.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        msg = "historyLength must be an integer"
        raise A2ARequestValidationError(msg) from exc
    if value < 0:
        msg = "historyLength must be >= 0"
        raise A2ARequestValidationError(msg)
    return value


def _parse_status_timestamp(raw: str | None) -> datetime | None:
    """Parse the ``statusTimestampAfter`` parameter.

    Raises:
        A2ARequestValidationError: If it is not an ISO 8601 timestamp.
    """
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        msg = "statusTimestampAfter must be ISO 8601"
        raise A2ARequestValidationError(msg) from exc


def _parse_page_size(
    raw: str | None, *, min_value: int = 1, max_value: int | None = None
) -> int:
    """Parse the ``pageSize`` parameter (default 50).

    Raises:
        A2ARequestValidationError: If it is not an integer in range.
    """
    if raw is None:
        return 50
    try:
        value = int(raw)
    except ValueError as exc:
        msg = "pageSize must be an integer"
        raise A2ARequestValidationError(msg) from exc
    if value < min_value:
        if max_value is None:
            msg = "pageSize must be >= 1"
            raise A2ARequestValidationError(msg)
        msg = "pageSize must be between 1 and 100"
        raise A2ARequestValidationError(msg)
    if max_value is not None and value > max_value:
        msg = "pageSize must be between 1 and 100"
        raise A2ARequestValidationError(msg)
    return value


def _parse_task_id(value: str) -> str:
    """Return the task id of a ``tasks/{id}`` resource name (or the id)."""
    if value.startswith("tasks/"):
        parts = value.split("/")
        if len(parts) >= 2:
            return parts[1]
    return value


def _parse_push_config_name(value: str) -> tuple[str, str]:
    """Split a ``tasks/{id}/pushNotificationConfigs/{config}`` name.

    Raises:
        A2ARequestValidationError: If the name is malformed.
    """
    parts = value.split("/")
    if (
        len(parts) >= 4
        and parts[0] == "tasks"
        and parts[2] == "pushNotificationConfigs"
    ):
        return parts[1], parts[3]
    msg = "push notification config name is invalid"
    raise A2ARequestValidationError(msg)


def _parse_task_state(value: str | None) -> TaskState | None:
    """Parse the ``status`` filter.

    Raises:
        A2ARequestValidationError: If it is not a task state.
    """
    if value is None:
        return None
    try:
        return TaskState(value)
    except ValueError as exc:
        msg = "status is invalid"
        raise A2ARequestValidationError(msg) from exc


def _validate_content_type(request: Request) -> None:
    """Reject request bodies in unsupported content types.

    Raises:
        ContentTypeNotSupportedError: If the content type is unsupported.
    """
    content_type = request.headers.get("content-type")
    if content_type is None:
        raise ContentTypeNotSupportedError(content_type)
    if not any(item in content_type for item in SUPPORTED_CONTENT_TYPES):
        raise ContentTypeNotSupportedError(content_type)


def _validate_service_params(
    service: A2AService, request: Request
) -> list[str]:
    """Check the requested protocol version and extensions; return these."""
    version = request.headers.get("A2A-Version") or request.headers.get(
        "a2a-version"
    )
    service.validate_version(version)
    extensions = _parse_extensions(request)
    service.validate_extensions(extensions)
    return extensions


async def _request_json(request: Request) -> object:
    """Return the request's JSON body.

    Raises:
        A2ARequestValidationError: If the body is not valid JSON.
    """
    _validate_content_type(request)
    try:
        return await request.json()
    except Exception as exc:  # pragma: no cover - fastapi wraps invalid JSON
        msg = "Request body must be valid JSON"
        raise A2ARequestValidationError(msg) from exc


async def _load_send_message_request(request: Request) -> SendMessageRequest:
    """Validate the request body as a ``SendMessageRequest``.

    Raises:
        A2ARequestValidationError: If the body is invalid.
    """
    payload = await _request_json(request)
    try:
        return SendMessageRequest.model_validate(payload)
    except ValidationError as exc:
        raise A2ARequestValidationError(str(exc)) from exc


async def _load_push_config(request: Request) -> TaskPushNotificationConfig:
    """Validate the request body as a push notification config.

    Raises:
        A2ARequestValidationError: If the body is invalid.
    """
    payload = await _request_json(request)
    try:
        return TaskPushNotificationConfig.model_validate(payload)
    except ValidationError as exc:
        raise A2ARequestValidationError(str(exc)) from exc


async def _stream_events(
    first: StreamResponse,
    queue: asyncio.Queue[StreamResponse],
    unsubscribe: _Unsubscribe,
) -> AsyncIterator[bytes]:
    """Stream task events as SSE until the final status update."""
    try:
        yield encode_stream_response(first)
        while True:
            event = await queue.get()
            yield encode_stream_response(event)
            if event.status_update and event.status_update.final:
                break
    finally:
        await unsubscribe()


def _event_stream(
    task: Task, queue: asyncio.Queue[StreamResponse], unsubscribe: _Unsubscribe
) -> StreamingResponse:
    """Return the SSE response streaming a task and its events."""
    return StreamingResponse(
        _stream_events(StreamResponse(task=task), queue, unsubscribe),
        media_type="text/event-stream",
    )


def _jsonrpc_result(
    request_id: object, result: object, *, status_code: int = 200
) -> JSONResponse:
    """Return a JSON-RPC success response."""
    return JSONResponse(
        status_code=status_code,
        content={"jsonrpc": "2.0", "id": request_id, "result": result},
        media_type="application/json",
    )


def _jsonrpc_error(
    request_id: object,
    *,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    """Return a JSON-RPC error response."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if data:
        payload["data"] = data
    return JSONResponse(
        status_code=status_code,
        content={"jsonrpc": "2.0", "id": request_id, "error": payload},
        media_type="application/json",
    )


def _jsonrpc_error_from_exception(
    request_id: object, exc: Exception
) -> JSONResponse:
    """Map an exception to its JSON-RPC error response."""
    if isinstance(exc, A2ARequestValidationError):
        return _jsonrpc_error(
            request_id,
            code=-32602,
            message="Invalid parameters",
            data={"detail": exc.detail},
        )
    if isinstance(exc, ValidationError):
        return _jsonrpc_error(
            request_id,
            code=-32602,
            message="Invalid parameters",
            data={"detail": str(exc)},
        )
    if isinstance(exc, A2AError):
        code = next(
            (
                mapped
                for error_type, mapped in _JSONRPC_ERROR_CODES.items()
                if isinstance(exc, error_type)
            ),
            -32603,
        )
        details = exc.to_problem_details().model_dump(exclude_none=True)
        return _jsonrpc_error(
            request_id, code=code, message=exc.title, data=details
        )
    return _jsonrpc_error(
        request_id,
        code=-32603,
        message="Internal error",
        data={"detail": str(exc)},
    )


def _encode_jsonrpc_stream(request_id: object, result: object) -> bytes:
    """Encode one JSON-RPC streaming result as an SSE event."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"data: {payload}\n\n".encode()


def _jsonrpc_stream_payload(event: StreamResponse) -> dict[str, Any]:
    """Return the payload a JSON-RPC stream carries for ``event``.

    Raises:
        RuntimeError: If the event carries no payload.
    """
    if event.task is not None:
        return _dump(event.task)
    if event.message is not None:
        return _dump(event.message)
    if event.status_update is not None:
        return _dump(event.status_update)
    if event.artifact_update is not None:
        return _dump(event.artifact_update)
    msg = "StreamResponse missing payload"
    raise RuntimeError(msg)


async def _jsonrpc_events(
    request_id: object,
    task: Task,
    queue: asyncio.Queue[StreamResponse],
    unsubscribe: _Unsubscribe,
) -> AsyncIterator[bytes]:
    """Stream task events as JSON-RPC results until the final update."""
    try:
        yield _encode_jsonrpc_stream(request_id, _dump(task))
        while True:
            event = await queue.get()
            yield _encode_jsonrpc_stream(
                request_id, _jsonrpc_stream_payload(event)
            )
            if event.status_update and event.status_update.final:
                break
    finally:
        await unsubscribe()


def _jsonrpc_event_stream(
    request_id: object,
    task: Task,
    queue: asyncio.Queue[StreamResponse],
    unsubscribe: _Unsubscribe,
) -> StreamingResponse:
    """Return the SSE response of a streaming JSON-RPC call."""
    return StreamingResponse(
        _jsonrpc_events(request_id, task, queue, unsubscribe),
        media_type="text/event-stream",
    )


async def _wrap_http(call: Awaitable[Response]) -> Response:
    """Await an HTTP+JSON handler, mapping A2A errors to problem details."""
    try:
        return await call
    except A2AError as exc:
        return _problem_response(exc)
    except ValidationError as exc:
        return _validation_response(exc)


def _agent_card_response(service: A2AService) -> JSONResponse:
    """Return the public agent card."""
    return _a2a_json(service.agent_card)


def _parse_jsonrpc_envelope(
    payload: object,
) -> tuple[object, str, dict[str, Any]] | JSONResponse:
    """Return a JSON-RPC request's id, method and params, or the error."""
    if not isinstance(payload, dict):
        return _jsonrpc_error(None, code=-32600, message=_INVALID_REQUEST)
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return _jsonrpc_error(
            request_id, code=-32600, message=_INVALID_REQUEST
        )
    if "id" not in payload or request_id is None:
        return _jsonrpc_error(None, code=-32600, message=_INVALID_REQUEST)
    method = payload.get("method")
    if not isinstance(method, str):
        return _jsonrpc_error(
            request_id, code=-32600, message=_INVALID_REQUEST
        )
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _jsonrpc_error(
            request_id, code=-32602, message="Invalid parameters"
        )
    return request_id, method, params


def _rpc_task_id(params: dict[str, Any]) -> str:
    """Return the task named by ``id``, ``taskId`` or a ``name``.

    Raises:
        A2ARequestValidationError: If no task is named.
    """
    task_id = None
    if "id" in params:
        task_id = params["id"]
    elif "taskId" in params:
        task_id = params["taskId"]
    elif "name" in params:
        task_id = _parse_task_id(str(params["name"]))
    if not task_id:
        msg = "id is required"
        raise A2ARequestValidationError(msg)
    return str(task_id)


def _rpc_parent_task_id(params: dict[str, Any]) -> str:
    """Return the task named by ``parent`` or ``taskId``.

    Raises:
        A2ARequestValidationError: If no task is named.
    """
    task_id = None
    if "parent" in params:
        task_id = _parse_task_id(str(params["parent"]))
    elif "taskId" in params:
        task_id = params["taskId"]
    if not task_id:
        msg = "parent is required"
        raise A2ARequestValidationError(msg)
    return str(task_id)


def _rpc_push_config_ref(params: dict[str, Any]) -> tuple[str, str]:
    """Return the task and push config named by the params.

    Raises:
        A2ARequestValidationError: If either is missing.
    """
    task_id = None
    config_id = None
    if "name" in params:
        task_id, config_id = _parse_push_config_name(str(params["name"]))
    if not task_id:
        if "parent" in params:
            task_id = _parse_task_id(str(params["parent"]))
        elif "taskId" in params:
            task_id = params["taskId"]
    if not config_id:
        config_id = params.get("configId") or params.get("config_id")
    if not task_id or not config_id:
        msg = "name or taskId/configId is required"
        raise A2ARequestValidationError(msg)
    return str(task_id), str(config_id)


def _rpc_push_config(
    params: dict[str, Any],
) -> tuple[str, TaskPushNotificationConfig]:
    """Return the push config id and config to store.

    Raises:
        A2ARequestValidationError: If the config or its id is missing.
    """
    config_id = params.get("configId") or params.get("config_id")
    config_payload = params.get("config") or params.get(
        "pushNotificationConfig"
    )
    if config_payload is None:
        msg = "config is required"
        raise A2ARequestValidationError(msg)
    if (
        "pushNotificationConfig" not in config_payload
        and "url" in config_payload
    ):
        config_payload = {"pushNotificationConfig": config_payload}
    config = TaskPushNotificationConfig.model_validate(config_payload)
    if not config_id:
        config_id = config.push_notification_config.id
    if not config_id:
        msg = "configId is required"
        raise A2ARequestValidationError(msg)
    return str(config_id), config


def _rpc_page_size(
    params: dict[str, Any], *, max_value: int | None = None
) -> int:
    """Parse the ``pageSize`` param of a JSON-RPC call."""
    raw = params.get("pageSize")
    return _parse_page_size(
        None if raw is None else str(raw), max_value=max_value
    )


@dataclass(frozen=True, slots=True)
class _RpcCall:
    """One JSON-RPC call: its id and params plus the request context."""

    request: Request
    request_id: object
    params: dict[str, Any]
    tenant: str | None
    extensions: list[str]


_RpcHandler = Callable[[_RpcCall], Awaitable[Response]]


class _A2AHttpRoutes:
    """The route handlers of the HTTP+JSON and JSON-RPC A2A bindings."""

    def __init__(self, service: A2AService) -> None:
        """Serve ``service`` over HTTP."""
        self._service = service
        self._rpc_methods: dict[str, _RpcHandler] = {
            "SendMessage": self._rpc_send_message,
            "SendStreamingMessage": self._rpc_send_streaming_message,
            "GetTask": self._rpc_get_task,
            "ListTasks": self._rpc_list_tasks,
            "CancelTask": self._rpc_cancel_task,
            "SubscribeToTask": self._rpc_subscribe_to_task,
            "SetTaskPushNotificationConfig": self._rpc_set_push_config,
            "GetTaskPushNotificationConfig": self._rpc_get_push_config,
            "ListTaskPushNotificationConfig": self._rpc_list_push_configs,
            "DeleteTaskPushNotificationConfig": self._rpc_delete_push_config,
            "GetExtendedAgentCard": self._rpc_extended_agent_card,
        }

    def register(
        self,
        router: APIRouter,
        *,
        include_agent_card: bool,
        include_jsonrpc: bool,
    ) -> None:
        """Add the routes to ``router``."""
        if include_agent_card:
            router.add_api_route(
                "/.well-known/agent-card.json",
                self.agent_card,
                methods=["GET"],
            )
        router.add_api_route(
            "/extendedAgentCard", self.extended_agent_card, methods=["GET"]
        )
        if include_jsonrpc:
            router.add_api_route("/rpc", self.jsonrpc, methods=["POST"])
        routes: list[tuple[str, Callable[..., Awaitable[Response]], str]] = [
            ("/message:send", self.send_message, "POST"),
            ("/message:stream", self.stream_message, "POST"),
            ("/tasks/{task_id}:subscribe", self.subscribe_task, "GET"),
            ("/tasks/{task_id}:subscribe", self.subscribe_task, "POST"),
            (
                "/tasks/{task_id}/pushNotificationConfigs",
                self.set_push_notification_config,
                "POST",
            ),
            (
                "/tasks/{task_id}/pushNotificationConfigs/{config_id}",
                self.get_push_notification_config,
                "GET",
            ),
            (
                "/tasks/{task_id}/pushNotificationConfigs",
                self.list_push_notification_configs,
                "GET",
            ),
            (
                "/tasks/{task_id}/pushNotificationConfigs/{config_id}",
                self.delete_push_notification_config,
                "DELETE",
            ),
            ("/tasks/{task_id}", self.get_task, "GET"),
            ("/tasks", self.list_tasks, "GET"),
            ("/tasks/{task_id}:cancel", self.cancel_task, "POST"),
        ]
        for path, endpoint, method in routes:
            router.add_api_route(path, endpoint, methods=[method])

    # HTTP+JSON binding

    async def agent_card(self) -> Response:
        """Return the public agent card."""
        return _agent_card_response(self._service)

    async def extended_agent_card(self, request: Request) -> Response:
        """Return the extended agent card to authorized callers."""
        return await _wrap_http(self._extended_agent_card(request))

    async def _extended_agent_card(self, request: Request) -> Response:
        _validate_service_params(self._service, request)
        if not self._service.is_extended_agent_card_authorized(
            request.headers
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return _a2a_json(await self._service.get_extended_agent_card())

    async def send_message(
        self, request: Request, tenant: str | None = None
    ) -> Response:
        """Send a message and return the task or reply."""
        return await _wrap_http(self._send_message(request, tenant))

    async def _send_message(
        self, request: Request, tenant: str | None
    ) -> Response:
        extensions = _validate_service_params(self._service, request)
        payload = await _load_send_message_request(request)
        response = await self._service.send_message(
            payload, tenant=tenant, requested_extensions=extensions
        )
        return _a2a_json(response)

    async def stream_message(
        self, request: Request, tenant: str | None = None
    ) -> Response:
        """Send a message and stream the task's events."""
        return await _wrap_http(self._stream_message(request, tenant))

    async def _stream_message(
        self, request: Request, tenant: str | None
    ) -> Response:
        extensions = _validate_service_params(self._service, request)
        payload = await _load_send_message_request(request)
        task, queue, unsubscribe = await self._service.stream_message(
            payload, tenant=tenant, requested_extensions=extensions
        )
        return _event_stream(task, queue, unsubscribe)

    async def subscribe_task(self, request: Request, task_id: str) -> Response:
        """Stream the events of a running task."""
        return await _wrap_http(self._subscribe_task(request, task_id))

    async def _subscribe_task(
        self, request: Request, task_id: str
    ) -> Response:
        _validate_service_params(self._service, request)
        task, queue, unsubscribe = await self._service.subscribe_task(task_id)
        return _event_stream(task, queue, unsubscribe)

    async def set_push_notification_config(
        self, request: Request, task_id: str
    ) -> Response:
        """Store a task's push notification config."""
        return await _wrap_http(self._set_push_config(request, task_id))

    async def _set_push_config(
        self, request: Request, task_id: str
    ) -> Response:
        _validate_service_params(self._service, request)
        config_id = _query_value(request, "configId")
        if not config_id:
            msg = "configId is required"
            raise A2ARequestValidationError(msg)
        payload = await _load_push_config(request)
        response = await self._service.set_task_push_notification_config(
            task_id, config_id=config_id, config=payload
        )
        return _a2a_json(response)

    async def get_push_notification_config(
        self, request: Request, task_id: str, config_id: str
    ) -> Response:
        """Return one of a task's push notification configs."""
        return await _wrap_http(
            self._get_push_config(request, task_id, config_id)
        )

    async def _get_push_config(
        self, request: Request, task_id: str, config_id: str
    ) -> Response:
        _validate_service_params(self._service, request)
        config = await self._service.get_task_push_notification_config(
            task_id, config_id=config_id
        )
        return _a2a_json(config)

    async def list_push_notification_configs(
        self, request: Request, task_id: str
    ) -> Response:
        """Return a page of a task's push notification configs."""
        return await _wrap_http(self._list_push_configs(request, task_id))

    async def _list_push_configs(
        self, request: Request, task_id: str
    ) -> Response:
        _validate_service_params(self._service, request)
        page_size = _parse_page_size(_query_value(request, "pageSize"))
        response = await self._service.list_task_push_notification_configs(
            task_id,
            page_size=page_size,
            page_token=_query_value(request, "pageToken"),
        )
        return _a2a_json(response)

    async def delete_push_notification_config(
        self, request: Request, task_id: str, config_id: str
    ) -> Response:
        """Delete one of a task's push notification configs."""
        return await _wrap_http(
            self._delete_push_config(request, task_id, config_id)
        )

    async def _delete_push_config(
        self, request: Request, task_id: str, config_id: str
    ) -> Response:
        _validate_service_params(self._service, request)
        await self._service.delete_task_push_notification_config(
            task_id, config_id=config_id
        )
        return Response(status_code=204)

    async def get_task(self, request: Request, task_id: str) -> Response:
        """Return a task."""
        return await _wrap_http(self._get_task(request, task_id))

    async def _get_task(self, request: Request, task_id: str) -> Response:
        _validate_service_params(self._service, request)
        history_length = _parse_history_length(
            _query_value(request, "historyLength")
        )
        task = await self._service.get_task(
            task_id, history_length=history_length
        )
        return _a2a_json(task)

    async def list_tasks(self, request: Request) -> Response:
        """Return a page of tasks, optionally filtered."""
        return await _wrap_http(self._list_tasks(request))

    async def _list_tasks(self, request: Request) -> Response:
        _validate_service_params(self._service, request)
        page_size = _parse_page_size(
            _query_value(request, "pageSize"), max_value=100
        )
        history_length = _parse_history_length(
            _query_value(request, "historyLength")
        )
        status_after = _parse_status_timestamp(
            _query_value(request, "statusTimestampAfter")
        )
        try:
            response = await self._service.list_tasks(
                context_id=_query_value(request, "contextId"),
                status=_parse_task_state(_query_value(request, "status")),
                page_size=page_size,
                page_token=_query_value(request, "pageToken"),
                history_length=history_length,
                status_timestamp_after=status_after,
                include_artifacts=_parse_bool(
                    _query_value(request, "includeArtifacts"),
                    name="includeArtifacts",
                ),
            )
        except ValueError as exc:
            msg = "pageToken is invalid"
            raise A2ARequestValidationError(msg) from exc
        return _a2a_json(response)

    async def cancel_task(self, request: Request, task_id: str) -> Response:
        """Cancel a task."""
        return await _wrap_http(self._cancel_task(request, task_id))

    async def _cancel_task(self, request: Request, task_id: str) -> Response:
        _validate_service_params(self._service, request)
        return _a2a_json(await self._service.cancel_task(task_id))

    # JSON-RPC binding

    async def jsonrpc(
        self, request: Request, tenant: str | None = None
    ) -> Response:
        """Serve one JSON-RPC 2.0 call."""
        try:
            _validate_content_type(request)
        except A2AError as exc:
            return _jsonrpc_error_from_exception(None, exc)
        try:
            payload = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            return _jsonrpc_error(
                None, code=-32700, message="Invalid JSON payload"
            )
        envelope = _parse_jsonrpc_envelope(payload)
        if isinstance(envelope, JSONResponse):
            return envelope
        request_id, method, params = envelope
        handler = self._rpc_methods.get(method)
        try:
            extensions = _validate_service_params(self._service, request)
            if handler is not None:
                return await handler(
                    _RpcCall(
                        request=request,
                        request_id=request_id,
                        params=params,
                        tenant=tenant,
                        extensions=extensions,
                    )
                )
        except Exception as exc:
            return _jsonrpc_error_from_exception(request_id, exc)
        return _jsonrpc_error(
            request_id,
            code=-32601,
            message="Method not found",
            data={"method": method},
        )

    async def _rpc_send_message(self, call: _RpcCall) -> Response:
        response = await self._service.send_message(
            SendMessageRequest.model_validate(call.params),
            tenant=call.tenant,
            requested_extensions=call.extensions,
        )
        return _jsonrpc_result(call.request_id, _dump(response))

    async def _rpc_send_streaming_message(self, call: _RpcCall) -> Response:
        task, queue, unsubscribe = await self._service.stream_message(
            SendMessageRequest.model_validate(call.params),
            tenant=call.tenant,
            requested_extensions=call.extensions,
        )
        return _jsonrpc_event_stream(call.request_id, task, queue, unsubscribe)

    async def _rpc_get_task(self, call: _RpcCall) -> Response:
        task_id = _rpc_task_id(call.params)
        history_length = _parse_history_length(
            call.params.get("historyLength")
        )
        task = await self._service.get_task(
            task_id, history_length=history_length
        )
        return _jsonrpc_result(call.request_id, _dump(task))

    async def _rpc_list_tasks(self, call: _RpcCall) -> Response:
        params = call.params
        page_size = _rpc_page_size(params, max_value=100)
        history_length = _parse_history_length(params.get("historyLength"))
        status_after = _parse_status_timestamp(
            params.get("statusTimestampAfter")
        )
        response = await self._service.list_tasks(
            context_id=params.get("contextId"),
            status=_parse_task_state(params.get("status")),
            page_size=page_size,
            page_token=params.get("pageToken"),
            history_length=history_length,
            status_timestamp_after=status_after,
            include_artifacts=bool(params.get("includeArtifacts", False)),
        )
        return _jsonrpc_result(call.request_id, _dump(response))

    async def _rpc_cancel_task(self, call: _RpcCall) -> Response:
        task = await self._service.cancel_task(_rpc_task_id(call.params))
        return _jsonrpc_result(call.request_id, _dump(task))

    async def _rpc_subscribe_to_task(self, call: _RpcCall) -> Response:
        task, queue, unsubscribe = await self._service.subscribe_task(
            _rpc_task_id(call.params)
        )
        return _jsonrpc_event_stream(call.request_id, task, queue, unsubscribe)

    async def _rpc_set_push_config(self, call: _RpcCall) -> Response:
        task_id = _rpc_parent_task_id(call.params)
        config_id, config = _rpc_push_config(call.params)
        response = await self._service.set_task_push_notification_config(
            task_id, config_id=config_id, config=config
        )
        return _jsonrpc_result(call.request_id, _dump(response))

    async def _rpc_get_push_config(self, call: _RpcCall) -> Response:
        task_id, config_id = _rpc_push_config_ref(call.params)
        config = await self._service.get_task_push_notification_config(
            task_id, config_id=config_id
        )
        return _jsonrpc_result(call.request_id, _dump(config))

    async def _rpc_list_push_configs(self, call: _RpcCall) -> Response:
        task_id = _rpc_parent_task_id(call.params)
        response = await self._service.list_task_push_notification_configs(
            task_id,
            page_size=_rpc_page_size(call.params),
            page_token=call.params.get("pageToken"),
        )
        return _jsonrpc_result(call.request_id, _dump(response))

    async def _rpc_delete_push_config(self, call: _RpcCall) -> Response:
        task_id, config_id = _rpc_push_config_ref(call.params)
        await self._service.delete_task_push_notification_config(
            task_id, config_id=config_id
        )
        return _jsonrpc_result(call.request_id, {})

    async def _rpc_extended_agent_card(self, call: _RpcCall) -> Response:
        if not self._service.is_extended_agent_card_authorized(
            call.request.headers
        ):
            return _jsonrpc_error(
                call.request_id,
                code=-32603,
                message="Unauthorized",
                status_code=401,
            )
        card = await self._service.get_extended_agent_card()
        return _jsonrpc_result(call.request_id, _dump(card))


def _include_operation_routes(
    app: FastAPI | APIRouter,
    router: APIRouter,
    service: A2AService,
    *,
    include_jsonrpc: bool,
) -> None:
    """Mount the routes at the root and the enabled tenant/v1 prefixes."""
    app.include_router(router)
    prefixes: list[str] = []
    if service.config.allow_tenant_prefix:
        prefixes.append("/{tenant}")
    if service.config.allow_v1_aliases:
        prefixes.append("/v1")
        if service.config.allow_tenant_prefix:
            prefixes.append("/{tenant}/v1")
    for prefix in prefixes:
        app.include_router(
            create_a2a_http_router(
                service,
                include_agent_card=False,
                include_jsonrpc=include_jsonrpc,
                _attach_lifespan=False,
            ),
            prefix=prefix,
        )


def create_a2a_http_router(
    service: A2AService,
    *,
    include_agent_card: bool = True,
    include_jsonrpc: bool = True,
    _attach_lifespan: bool = True,
) -> APIRouter:
    """Return a router serving ``service`` over HTTP+JSON and JSON-RPC.

    Args:
        service: The A2A service to serve.
        include_agent_card: Serve ``/.well-known/agent-card.json``.
        include_jsonrpc: Serve the JSON-RPC binding at ``/rpc``.
        _attach_lifespan: Start and stop ``service`` with the router.
    """

    @asynccontextmanager
    async def _lifespan(_router: APIRouter) -> AsyncGenerator[None, None]:
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    router = APIRouter(lifespan=_lifespan if _attach_lifespan else None)
    _A2AHttpRoutes(service).register(
        router,
        include_agent_card=include_agent_card,
        include_jsonrpc=include_jsonrpc,
    )
    return router


def install_a2a_http(
    app: FastAPI,
    service: A2AService,
    *,
    include_jsonrpc: bool = True,
    include_agent_card: bool = True,
) -> None:
    """Add the A2A routes (and the agent card) to an existing app."""
    if include_agent_card:
        app.add_api_route(
            "/.well-known/agent-card.json",
            _A2AHttpRoutes(service).agent_card,
            methods=["GET"],
        )
    router = create_a2a_http_router(
        service,
        include_agent_card=False,
        include_jsonrpc=include_jsonrpc,
    )
    _include_operation_routes(
        app, router, service, include_jsonrpc=include_jsonrpc
    )


def create_a2a_http_app(
    service: A2AService, *, include_docs: bool = True
) -> FastAPI:
    """Return a FastAPI app serving ``service`` over A2A."""
    docs_url = "/docs" if include_docs else None
    openapi_url = "/openapi.json" if include_docs else None
    app = FastAPI(
        title=service.agent_card.name,
        description=service.agent_card.description,
        version=service.agent_card.version,
        docs_url=docs_url,
        openapi_url=openapi_url,
    )
    install_a2a_http(app, service)
    return app


__all__ = [
    "create_a2a_http_app",
    "create_a2a_http_router",
    "install_a2a_http",
]
