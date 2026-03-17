import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

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
    ListTaskPushNotificationConfigResponse,
    SendMessageRequest,
    StreamResponse,
    TaskPushNotificationConfig,
    TaskState,
)
from ..sse import encode_stream_response

if TYPE_CHECKING:
    from fastapi import Request as FastAPIRequest
else:  # pragma: no cover - optional dependency
    try:
        from fastapi import Request as FastAPIRequest
    except ModuleNotFoundError:  # pragma: no cover - optional extra
        class FastAPIRequest:  # type: ignore[no-redef]
            pass


def _load_fastapi():
    try:
        from fastapi import APIRouter, FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse, Response, StreamingResponse
    except ModuleNotFoundError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("FastAPI is required for the A2A server adapter. Install penguiflow[a2a-server].") from exc
    return APIRouter, FastAPI, HTTPException, Request, JSONResponse, Response, StreamingResponse


def _problem_response(exc: A2AError):
    _, _, _, _, JSONResponse, _, _ = _load_fastapi()
    details = exc.to_problem_details().model_dump(exclude_none=True)
    return JSONResponse(
        status_code=exc.status_code,
        content=details,
        media_type="application/problem+json",
    )


def _validation_response(exc: ValidationError | str):
    detail = str(exc)
    if isinstance(exc, ValidationError):
        detail = str(exc)
    return _problem_response(A2ARequestValidationError(detail))


def _query_value(request: Any, name: str) -> str | None:
    return request.query_params.get(name)


def _parse_bool(value: str | None, *, name: str) -> bool:
    if value is None:
        return False
    lowered = value.strip().lower()
    if lowered in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise A2ARequestValidationError(f"{name} must be a boolean")


def _parse_extensions(request: Any) -> list[str]:
    header = request.headers.get("A2A-Extensions") or request.headers.get("a2a-extensions")
    if not header:
        return []
    return [value.strip() for value in header.split(",") if value.strip()]


def _parse_history_length(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise A2ARequestValidationError("historyLength must be an integer") from exc
    if value < 0:
        raise A2ARequestValidationError("historyLength must be >= 0")
    return value


def _parse_status_timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        cleaned = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise A2ARequestValidationError("statusTimestampAfter must be ISO 8601") from exc


def _parse_page_size(raw: str | None, *, min_value: int = 1, max_value: int | None = None) -> int:
    if raw is None:
        return 50
    try:
        value = int(raw)
    except ValueError as exc:
        raise A2ARequestValidationError("pageSize must be an integer") from exc
    if value < min_value:
        if max_value is None:
            raise A2ARequestValidationError("pageSize must be >= 1")
        raise A2ARequestValidationError("pageSize must be between 1 and 100")
    if max_value is not None and value > max_value:
        raise A2ARequestValidationError("pageSize must be between 1 and 100")
    return value


def _parse_task_id(value: str) -> str:
    if value.startswith("tasks/"):
        parts = value.split("/")
        if len(parts) >= 2:
            return parts[1]
    return value


def _parse_push_config_name(value: str) -> tuple[str, str]:
    parts = value.split("/")
    if len(parts) >= 4 and parts[0] == "tasks" and parts[2] == "pushNotificationConfigs":
        return parts[1], parts[3]
    raise A2ARequestValidationError("push notification config name is invalid")


def _parse_task_state(value: str | None) -> TaskState | None:
    if value is None:
        return None
    try:
        return TaskState(value)
    except ValueError as exc:
        raise A2ARequestValidationError("status is invalid") from exc


def _validate_content_type(request: Any) -> None:
    content_type = request.headers.get("content-type")
    if content_type is None:
        raise ContentTypeNotSupportedError(content_type)
    if not any(item in content_type for item in SUPPORTED_CONTENT_TYPES):
        raise ContentTypeNotSupportedError(content_type)


def _validate_service_params(service: A2AService, request: Any) -> list[str]:
    version = request.headers.get("A2A-Version") or request.headers.get("a2a-version")
    service.validate_version(version)
    extensions = _parse_extensions(request)
    service.validate_extensions(extensions)
    return extensions


async def _load_send_message_request(request: Any) -> SendMessageRequest:
    _validate_content_type(request)
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - fastapi wraps invalid JSON
        raise A2ARequestValidationError("Request body must be valid JSON") from exc
    try:
        return SendMessageRequest.model_validate(payload)
    except ValidationError as exc:
        raise A2ARequestValidationError(str(exc)) from exc


async def _load_push_config(request: Any) -> TaskPushNotificationConfig:
    _validate_content_type(request)
    try:
        payload = await request.json()
    except Exception as exc:  # pragma: no cover - fastapi wraps invalid JSON
        raise A2ARequestValidationError("Request body must be valid JSON") from exc
    try:
        return TaskPushNotificationConfig.model_validate(payload)
    except ValidationError as exc:
        raise A2ARequestValidationError(str(exc)) from exc


async def _stream_events(
    first: StreamResponse,
    queue,
    unsubscribe: Callable[[], Any],
):
    try:
        yield encode_stream_response(first)
        while True:
            event = await queue.get()
            yield encode_stream_response(event)
            if event.status_update and event.status_update.final:
                break
    finally:
        await unsubscribe()


def _jsonrpc_result(request_id: Any, result: Any, *, status_code: int = 200):
    _, _, _, _, JSONResponse, _, _ = _load_fastapi()
    return JSONResponse(
        status_code=status_code,
        content={"jsonrpc": "2.0", "id": request_id, "result": result},
        media_type="application/json",
    )


def _jsonrpc_error(
    request_id: Any,
    *,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
    status_code: int = 200,
):
    _, _, _, _, JSONResponse, _, _ = _load_fastapi()
    payload: dict[str, Any] = {"code": code, "message": message}
    if data:
        payload["data"] = data
    return JSONResponse(
        status_code=status_code,
        content={"jsonrpc": "2.0", "id": request_id, "error": payload},
        media_type="application/json",
    )


def _jsonrpc_error_from_exception(request_id: Any, exc: Exception):
    if isinstance(exc, A2ARequestValidationError):
        return _jsonrpc_error(request_id, code=-32602, message="Invalid parameters", data={"detail": exc.detail})
    if isinstance(exc, A2AError):
        error_map = {
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
        code = -32603
        for error_type, mapped in error_map.items():
            if isinstance(exc, error_type):
                code = mapped
                break
        details = exc.to_problem_details().model_dump(exclude_none=True)
        return _jsonrpc_error(request_id, code=code, message=exc.title, data=details)
    return _jsonrpc_error(request_id, code=-32603, message="Internal error", data={"detail": str(exc)})


def _encode_jsonrpc_stream(request_id: Any, result: Any) -> bytes:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"data: {payload}\n\n".encode()


def _jsonrpc_stream_payload(event: StreamResponse) -> dict[str, Any]:
    if event.task is not None:
        return event.task.model_dump(by_alias=True, exclude_none=True)
    if event.message is not None:
        return event.message.model_dump(by_alias=True, exclude_none=True)
    if event.status_update is not None:
        return event.status_update.model_dump(by_alias=True, exclude_none=True)
    if event.artifact_update is not None:
        return event.artifact_update.model_dump(by_alias=True, exclude_none=True)
    raise RuntimeError("StreamResponse missing payload")


async def _wrap_http(handler: Callable[[], Awaitable[Any]]):
    try:
        return await handler()
    except A2AError as exc:
        return _problem_response(exc)
    except ValidationError as exc:
        return _validation_response(exc)


def _agent_card_response(service: A2AService):
    _, _, _, _, JSONResponse, _, _ = _load_fastapi()
    payload = service.agent_card.model_dump(by_alias=True, exclude_none=True)
    return JSONResponse(content=payload, media_type="application/a2a+json")


def _include_operation_routes(app: Any, router: Any, service: A2AService, *, include_jsonrpc: bool) -> None:
    app.include_router(router)
    if service.config.allow_tenant_prefix:
        app.include_router(
            create_a2a_http_router(
                service,
                include_jsonrpc=include_jsonrpc,
                _attach_lifespan=False,
            ),
            prefix="/{tenant}",
        )
    if service.config.allow_v1_aliases:
        app.include_router(
            create_a2a_http_router(
                service,
                include_jsonrpc=include_jsonrpc,
                _attach_lifespan=False,
            ),
            prefix="/v1",
        )
        if service.config.allow_tenant_prefix:
            app.include_router(
                create_a2a_http_router(
                    service,
                    include_jsonrpc=include_jsonrpc,
                    _attach_lifespan=False,
                ),
                prefix="/{tenant}/v1",
            )


def create_a2a_http_router(
    service: A2AService,
    *,
    include_jsonrpc: bool = True,
    _attach_lifespan: bool = True,
):
    APIRouter, _, HTTPException, _, JSONResponse, Response, StreamingResponse = _load_fastapi()

    lifespan = None
    if _attach_lifespan:
        @asynccontextmanager
        async def _lifespan(_router: Any):
            await service.start()
            try:
                yield
            finally:
                await service.stop()

        lifespan = _lifespan

    router = APIRouter(lifespan=lifespan)

    @router.get("/extendedAgentCard")
    async def extended_agent_card(request: FastAPIRequest, tenant: str | None = None):
        async def _handle():
            _validate_service_params(service, request)
            if not service.is_extended_agent_card_authorized(request.headers):
                raise HTTPException(status_code=401, detail="Unauthorized")
            card = await service.get_extended_agent_card()
            return JSONResponse(
                content=card.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    if include_jsonrpc:
        @router.post("/rpc")
        async def jsonrpc(request: FastAPIRequest, tenant: str | None = None):
            try:
                _validate_content_type(request)
            except A2AError as exc:
                return _jsonrpc_error_from_exception(None, exc)
            try:
                raw = await request.body()
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return _jsonrpc_error(None, code=-32700, message="Invalid JSON payload")

            if not isinstance(payload, dict):
                return _jsonrpc_error(None, code=-32600, message="Request payload validation error")

            request_id = payload.get("id")
            if payload.get("jsonrpc") != "2.0":
                return _jsonrpc_error(request_id, code=-32600, message="Request payload validation error")
            if "id" not in payload:
                return _jsonrpc_error(None, code=-32600, message="Request payload validation error")
            if request_id is None:
                return _jsonrpc_error(None, code=-32600, message="Request payload validation error")
            method = payload.get("method")
            if not isinstance(method, str):
                return _jsonrpc_error(request_id, code=-32600, message="Request payload validation error")
            params = payload.get("params") or {}
            if not isinstance(params, dict):
                return _jsonrpc_error(request_id, code=-32602, message="Invalid parameters")

            try:
                extensions = _validate_service_params(service, request)
                if method == "SendMessage":
                    send_request = SendMessageRequest.model_validate(params)
                    send_response = await service.send_message(
                        send_request,
                        tenant=tenant,
                        requested_extensions=extensions,
                    )
                    return _jsonrpc_result(
                        request_id,
                        send_response.model_dump(by_alias=True, exclude_none=True),
                    )
                if method == "SendStreamingMessage":
                    send_request = SendMessageRequest.model_validate(params)
                    task, queue, unsubscribe = await service.stream_message(
                        send_request,
                        tenant=tenant,
                        requested_extensions=extensions,
                    )
                    first_payload = task.model_dump(by_alias=True, exclude_none=True)

                    async def _jsonrpc_stream():
                        try:
                            yield _encode_jsonrpc_stream(request_id, first_payload)
                            while True:
                                event = await queue.get()
                                payload_data = _jsonrpc_stream_payload(event)
                                yield _encode_jsonrpc_stream(request_id, payload_data)
                                if event.status_update and event.status_update.final:
                                    break
                        finally:
                            await unsubscribe()

                    return StreamingResponse(_jsonrpc_stream(), media_type="text/event-stream")
                if method == "GetTask":
                    task_id = None
                    if "id" in params:
                        task_id = params["id"]
                    elif "taskId" in params:
                        task_id = params["taskId"]
                    elif "name" in params:
                        task_id = _parse_task_id(str(params["name"]))
                    if not task_id:
                        raise A2ARequestValidationError("id is required")
                    history_length = _parse_history_length(params.get("historyLength"))
                    task = await service.get_task(str(task_id), history_length=history_length)
                    return _jsonrpc_result(request_id, task.model_dump(by_alias=True, exclude_none=True))
                if method == "ListTasks":
                    page_size = _parse_page_size(
                        None if params.get("pageSize") is None else str(params["pageSize"]),
                        max_value=100,
                    )
                    history_length = _parse_history_length(params.get("historyLength"))
                    status_after = _parse_status_timestamp(params.get("statusTimestampAfter"))
                    list_response = await service.list_tasks(
                        context_id=params.get("contextId"),
                        status=_parse_task_state(params.get("status")),
                        page_size=page_size,
                        page_token=params.get("pageToken"),
                        history_length=history_length,
                        status_timestamp_after=status_after,
                        include_artifacts=bool(params.get("includeArtifacts", False)),
                    )
                    return _jsonrpc_result(
                        request_id,
                        list_response.model_dump(by_alias=True, exclude_none=True),
                    )
                if method == "CancelTask":
                    task_id = None
                    if "id" in params:
                        task_id = params["id"]
                    elif "taskId" in params:
                        task_id = params["taskId"]
                    elif "name" in params:
                        task_id = _parse_task_id(str(params["name"]))
                    if not task_id:
                        raise A2ARequestValidationError("id is required")
                    task = await service.cancel_task(str(task_id))
                    return _jsonrpc_result(request_id, task.model_dump(by_alias=True, exclude_none=True))
                if method == "SubscribeToTask":
                    task_id = None
                    if "id" in params:
                        task_id = params["id"]
                    elif "taskId" in params:
                        task_id = params["taskId"]
                    elif "name" in params:
                        task_id = _parse_task_id(str(params["name"]))
                    if not task_id:
                        raise A2ARequestValidationError("id is required")
                    task, queue, unsubscribe = await service.subscribe_task(str(task_id))
                    first_payload = task.model_dump(by_alias=True, exclude_none=True)

                    async def _jsonrpc_stream():
                        try:
                            yield _encode_jsonrpc_stream(request_id, first_payload)
                            while True:
                                event = await queue.get()
                                payload_data = _jsonrpc_stream_payload(event)
                                yield _encode_jsonrpc_stream(request_id, payload_data)
                                if event.status_update and event.status_update.final:
                                    break
                        finally:
                            await unsubscribe()

                    return StreamingResponse(_jsonrpc_stream(), media_type="text/event-stream")
                if method == "SetTaskPushNotificationConfig":
                    task_id = None
                    if "parent" in params:
                        task_id = _parse_task_id(str(params["parent"]))
                    elif "taskId" in params:
                        task_id = params["taskId"]
                    if not task_id:
                        raise A2ARequestValidationError("parent is required")
                    config_id = params.get("configId") or params.get("config_id")
                    config_payload = params.get("config") or params.get("pushNotificationConfig")
                    if config_payload is None:
                        raise A2ARequestValidationError("config is required")
                    if "pushNotificationConfig" not in config_payload and "url" in config_payload:
                        config_payload = {"pushNotificationConfig": config_payload}
                    config = TaskPushNotificationConfig.model_validate(config_payload)
                    if not config_id:
                        config_id = config.push_notification_config.id
                    if not config_id:
                        raise A2ARequestValidationError("configId is required")
                    push_response = await service.set_task_push_notification_config(
                        str(task_id),
                        config_id=str(config_id),
                        config=config,
                    )
                    return _jsonrpc_result(
                        request_id,
                        push_response.model_dump(by_alias=True, exclude_none=True),
                    )
                if method == "GetTaskPushNotificationConfig":
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
                        raise A2ARequestValidationError("name or taskId/configId is required")
                    config = await service.get_task_push_notification_config(
                        str(task_id),
                        config_id=str(config_id),
                    )
                    return _jsonrpc_result(request_id, config.model_dump(by_alias=True, exclude_none=True))
                if method == "ListTaskPushNotificationConfig":
                    task_id = None
                    if "parent" in params:
                        task_id = _parse_task_id(str(params["parent"]))
                    elif "taskId" in params:
                        task_id = params["taskId"]
                    if not task_id:
                        raise A2ARequestValidationError("parent is required")
                    page_size = _parse_page_size(
                        None if params.get("pageSize") is None else str(params["pageSize"])
                    )
                    list_response = await service.list_task_push_notification_configs(
                        str(task_id),
                        page_size=page_size,
                        page_token=params.get("pageToken"),
                    )
                    return _jsonrpc_result(
                        request_id,
                        list_response.model_dump(by_alias=True, exclude_none=True),
                    )
                if method == "DeleteTaskPushNotificationConfig":
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
                        raise A2ARequestValidationError("name or taskId/configId is required")
                    await service.delete_task_push_notification_config(
                        str(task_id),
                        config_id=str(config_id),
                    )
                    return _jsonrpc_result(request_id, {})
                if method == "GetExtendedAgentCard":
                    if not service.is_extended_agent_card_authorized(request.headers):
                        return _jsonrpc_error(request_id, code=-32603, message="Unauthorized", status_code=401)
                    card = await service.get_extended_agent_card()
                    return _jsonrpc_result(request_id, card.model_dump(by_alias=True, exclude_none=True))
            except Exception as exc:
                if isinstance(exc, A2AError):
                    return _jsonrpc_error_from_exception(request_id, exc)
                if isinstance(exc, ValidationError):
                    return _jsonrpc_error(
                        request_id,
                        code=-32602,
                        message="Invalid parameters",
                        data={"detail": str(exc)},
                    )
                return _jsonrpc_error_from_exception(request_id, exc)

            return _jsonrpc_error(
                request_id,
                code=-32601,
                message="Method not found",
                data={"method": method},
            )

    @router.post("/message:send")
    async def send_message(request: FastAPIRequest, tenant: str | None = None):
        async def _handle():
            extensions = _validate_service_params(service, request)
            payload = await _load_send_message_request(request)
            response = await service.send_message(
                payload,
                tenant=tenant,
                requested_extensions=extensions,
            )
            return JSONResponse(
                content=response.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    @router.post("/message:stream")
    async def stream_message(request: FastAPIRequest, tenant: str | None = None):
        async def _handle():
            extensions = _validate_service_params(service, request)
            payload = await _load_send_message_request(request)
            task, queue, unsubscribe = await service.stream_message(
                payload,
                tenant=tenant,
                requested_extensions=extensions,
            )
            first = StreamResponse(task=task)
            return StreamingResponse(
                _stream_events(first, queue, unsubscribe),
                media_type="text/event-stream",
            )

        return await _wrap_http(_handle)

    @router.get("/tasks/{task_id}:subscribe")
    async def subscribe_task(request: FastAPIRequest, task_id: str, tenant: str | None = None):
        async def _handle():
            _validate_service_params(service, request)
            task, queue, unsubscribe = await service.subscribe_task(task_id)
            first = StreamResponse(task=task)
            return StreamingResponse(
                _stream_events(first, queue, unsubscribe),
                media_type="text/event-stream",
            )

        return await _wrap_http(_handle)

    @router.post("/tasks/{task_id}:subscribe")
    async def subscribe_task_post(request: FastAPIRequest, task_id: str, tenant: str | None = None):
        return await subscribe_task(request, task_id, tenant)

    @router.post("/tasks/{task_id}/pushNotificationConfigs")
    async def set_push_notification_config(request: FastAPIRequest, task_id: str, tenant: str | None = None):
        async def _handle():
            _validate_service_params(service, request)
            config_id = _query_value(request, "configId")
            if not config_id:
                raise A2ARequestValidationError("configId is required")
            payload = await _load_push_config(request)
            response = await service.set_task_push_notification_config(
                task_id,
                config_id=config_id,
                config=payload,
            )
            return JSONResponse(
                content=response.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    @router.get("/tasks/{task_id}/pushNotificationConfigs/{config_id}")
    async def get_push_notification_config(
        request: FastAPIRequest,
        task_id: str,
        config_id: str,
        tenant: str | None = None,
    ):
        async def _handle():
            _validate_service_params(service, request)
            config = await service.get_task_push_notification_config(task_id, config_id=config_id)
            return JSONResponse(
                content=config.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    @router.get("/tasks/{task_id}/pushNotificationConfigs")
    async def list_push_notification_configs(
        request: FastAPIRequest,
        task_id: str,
        tenant: str | None = None,
    ):
        async def _handle():
            _validate_service_params(service, request)
            page_size = _parse_page_size(_query_value(request, "pageSize"))
            response: ListTaskPushNotificationConfigResponse = await service.list_task_push_notification_configs(
                task_id,
                page_size=page_size,
                page_token=_query_value(request, "pageToken"),
            )
            return JSONResponse(
                content=response.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    @router.delete("/tasks/{task_id}/pushNotificationConfigs/{config_id}")
    async def delete_push_notification_config(
        request: FastAPIRequest,
        task_id: str,
        config_id: str,
        tenant: str | None = None,
    ):
        async def _handle():
            _validate_service_params(service, request)
            await service.delete_task_push_notification_config(task_id, config_id=config_id)
            return Response(status_code=204)

        return await _wrap_http(_handle)

    @router.get("/tasks/{task_id}")
    async def get_task(
        request: FastAPIRequest,
        task_id: str,
        tenant: str | None = None,
    ):
        async def _handle():
            _validate_service_params(service, request)
            history_length = _parse_history_length(_query_value(request, "historyLength"))
            task = await service.get_task(task_id, history_length=history_length)
            return JSONResponse(
                content=task.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    @router.get("/tasks")
    async def list_tasks(request: FastAPIRequest, tenant: str | None = None):
        async def _handle():
            _validate_service_params(service, request)
            page_size = _parse_page_size(_query_value(request, "pageSize"), max_value=100)
            history_length = _parse_history_length(_query_value(request, "historyLength"))
            status_after = _parse_status_timestamp(_query_value(request, "statusTimestampAfter"))
            try:
                response = await service.list_tasks(
                    context_id=_query_value(request, "contextId"),
                    status=_parse_task_state(_query_value(request, "status")),
                    page_size=page_size,
                    page_token=_query_value(request, "pageToken"),
                    history_length=history_length,
                    status_timestamp_after=status_after,
                    include_artifacts=_parse_bool(_query_value(request, "includeArtifacts"), name="includeArtifacts"),
                )
            except ValueError as exc:
                raise A2ARequestValidationError("pageToken is invalid") from exc
            return JSONResponse(
                content=response.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    @router.post("/tasks/{task_id}:cancel")
    async def cancel_task(request: FastAPIRequest, task_id: str, tenant: str | None = None):
        async def _handle():
            _validate_service_params(service, request)
            task = await service.cancel_task(task_id)
            return JSONResponse(
                content=task.model_dump(by_alias=True, exclude_none=True),
                media_type="application/a2a+json",
            )

        return await _wrap_http(_handle)

    return router


def install_a2a_http(
    app: Any,
    service: A2AService,
    *,
    include_jsonrpc: bool = True,
    include_agent_card: bool = True,
) -> None:
    _, _, _, _, _, _, _ = _load_fastapi()

    if include_agent_card:
        async def agent_card():
            return _agent_card_response(service)

        app.add_api_route(
            "/.well-known/agent-card.json",
            agent_card,
            methods=["GET"],
        )

    router = create_a2a_http_router(service, include_jsonrpc=include_jsonrpc)
    _include_operation_routes(app, router, service, include_jsonrpc=include_jsonrpc)


def create_a2a_http_app(service: A2AService, *, include_docs: bool = True):
    _, FastAPI, _, _, _, _, _ = _load_fastapi()
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
