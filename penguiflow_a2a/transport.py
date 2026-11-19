"""HTTP+JSON client transport for calling remote A2A agents."""

from __future__ import annotations

import base64
import importlib
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from typing_extensions import override

from penguiflow.remote import (
    RemoteCallRequest,
    RemoteCallResult,
    RemotePushNotificationBinding,
    RemoteStreamEvent,
    RemoteTaskAuthRequired,
    RemoteTaskEvent,
    RemoteTaskInputRequired,
    RemoteTaskPage,
    RemoteTaskSnapshot,
    RemoteTaskState,
    RemoteTaskStatus,
    RemoteTransport,
    SupportsRemoteTasks,
)

if TYPE_CHECKING:
    from types import ModuleType

    import httpx

_DEFAULT_VERSION = "0.3"


class A2AResponseError(RuntimeError):
    """Raised when a remote agent answers with an unexpected payload."""


def _require_httpx() -> ModuleType:
    """Import httpx, explaining how to install it when it is missing."""
    try:
        module = importlib.import_module("httpx")
    except ImportError as exc:
        msg = (
            "httpx is required for A2AHttpTransport. Install with `pip "
            "install penguiflow-lite[a2a-client]`."
        )
        raise RuntimeError(msg) from exc
    return module


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def _build_metadata(request: RemoteCallRequest) -> dict[str, Any]:
    metadata = dict(request.metadata or {})
    metadata.setdefault("skill", request.skill)
    return metadata


def _payload_to_part(payload: object) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, bytes):
        encoded = base64.b64encode(payload).decode("ascii")
        return {"data": {"data": {"bytes": encoded}}}
    if isinstance(payload, str):
        return {"text": payload}
    return {"data": {"data": payload}}


def _build_send_message(
    request: RemoteCallRequest, *, blocking: bool
) -> dict[str, Any]:
    part = _payload_to_part(request.message.payload)
    message = {
        "messageId": uuid.uuid4().hex,
        "role": "user",
        "parts": [part],
        "metadata": _build_metadata(request),
    }
    if request.context_id is not None:
        message["contextId"] = request.context_id
    if request.task_id is not None:
        message["taskId"] = request.task_id
    return {
        "message": message,
        "configuration": {"blocking": blocking},
    }


def _extract_part_payload(part: Mapping[str, Any]) -> object:
    if "text" in part:
        return part["text"]
    if "data" in part:
        data = part["data"]
        if isinstance(data, Mapping):
            return data.get("data")
        return data
    if "file" in part:
        return {"file": part["file"]}
    return None


def _extract_artifact_payload(artifact: Mapping[str, Any]) -> object:
    parts = artifact.get("parts") or []
    values = [
        value
        for part in parts
        if (value := _extract_part_payload(part)) is not None
    ]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _extract_result_from_task(task: Mapping[str, Any]) -> object:
    artifacts = task.get("artifacts") or []
    for artifact in artifacts:
        payload = _extract_artifact_payload(artifact)
        if payload is not None:
            return payload
    return None


def _extract_message_text(message: object) -> str | None:
    if not isinstance(message, Mapping):
        return None
    parts = message.get("parts") or []
    if not isinstance(parts, list) or not parts:
        return None
    first = parts[0]
    if not isinstance(first, Mapping):
        return None
    text = first.get("text")
    if isinstance(text, str) and text:
        return text
    return None


def _extract_status_state(status: object) -> str | None:
    if not isinstance(status, Mapping):
        return None
    state = status.get("state")
    if isinstance(state, str) and state:
        return state
    return None


def _extract_status_detail(status: object) -> str | None:
    if not isinstance(status, Mapping):
        return None
    return _extract_message_text(status.get("message"))


def _normalize_task_state(value: str | None) -> RemoteTaskState:
    if value is None:
        return RemoteTaskState.UNSPECIFIED
    try:
        return RemoteTaskState(value)
    except ValueError:
        return RemoteTaskState.UNSPECIFIED


def _task_to_snapshot(
    task: Mapping[str, Any], *, agent_url: str
) -> RemoteTaskSnapshot:
    status_raw = task.get("status")
    status: Mapping[str, Any] = (
        status_raw if isinstance(status_raw, Mapping) else {}
    )
    task_id = str(task.get("id") or "")
    context_id = str(task.get("contextId") or task.get("context_id") or "")
    return RemoteTaskSnapshot(
        task_id=task_id,
        context_id=context_id,
        status=RemoteTaskStatus(
            state=_normalize_task_state(_extract_status_state(status)),
            message=_extract_status_detail(status),
            timestamp=status.get("timestamp")
            if isinstance(status.get("timestamp"), str)
            else None,
            raw=status,
        ),
        result=_extract_result_from_task(task),
        artifacts=list(task.get("artifacts") or []),
        history=list(task.get("history") or []),
        agent_url=agent_url,
        meta=dict(task.get("metadata") or {}),
    )


def _raise_for_remote_task(snapshot: RemoteTaskSnapshot) -> None:
    state = snapshot.status.state
    if state in {
        RemoteTaskState.FAILED,
        RemoteTaskState.CANCELLED,
        RemoteTaskState.REJECTED,
    }:
        msg = f"Remote task failed: {snapshot.status.message or state.value}"
        raise RuntimeError(msg)
    if state is RemoteTaskState.INPUT_REQUIRED:
        raise RemoteTaskInputRequired(snapshot)
    if state is RemoteTaskState.AUTH_REQUIRED:
        raise RemoteTaskAuthRequired(snapshot)


def _push_binding(payload: Mapping[str, Any]) -> RemotePushNotificationBinding:
    config = payload.get("pushNotificationConfig")
    if not isinstance(config, Mapping):
        config = payload.get("push_notification_config")
    if not isinstance(config, Mapping):
        config = {}
    return RemotePushNotificationBinding(
        name=payload.get("name")
        if isinstance(payload.get("name"), str)
        else None,
        config=dict(config),
    )


def _raise_for_status(
    response: httpx.Response, *, body: str | None = None
) -> None:
    if 200 <= response.status_code < 300:
        return
    detail = None
    if body:
        try:
            payload = json.loads(body)
            if isinstance(payload, Mapping):
                detail = payload.get("detail") or payload.get("title")
        except json.JSONDecodeError:
            detail = None
    detail_text = f": {detail}" if detail else ""
    msg = f"A2A request failed ({response.status_code}){detail_text}"
    raise RuntimeError(msg)


def _event_to_task_event(
    event: Mapping[str, Any], *, agent_url: str
) -> RemoteTaskEvent | None:
    task = event.get("task")
    if isinstance(task, Mapping):
        snapshot = _task_to_snapshot(task, agent_url=agent_url)
        return RemoteTaskEvent(
            kind="task",
            task=snapshot,
            status=snapshot.status,
            result=snapshot.result,
            done=snapshot.is_terminal,
            context_id=snapshot.context_id,
            task_id=snapshot.task_id,
            agent_url=agent_url,
            meta=snapshot.meta,
            raw=event,
        )
    status_update = event.get("statusUpdate")
    if isinstance(status_update, Mapping):
        status_raw = status_update.get("status")
        status: Mapping[str, Any] = (
            status_raw if isinstance(status_raw, Mapping) else {}
        )
        normalized = RemoteTaskStatus(
            state=_normalize_task_state(_extract_status_state(status)),
            message=_extract_status_detail(status),
            timestamp=status.get("timestamp")
            if isinstance(status.get("timestamp"), str)
            else None,
            raw=status,
        )
        return RemoteTaskEvent(
            kind="status",
            status=normalized,
            done=bool(status_update.get("final")),
            context_id=status_update.get("contextId"),
            task_id=status_update.get("taskId"),
            agent_url=agent_url,
            meta=dict(status_update.get("metadata") or {}),
            raw=event,
        )
    artifact_update = event.get("artifactUpdate")
    if isinstance(artifact_update, Mapping):
        artifact = artifact_update.get("artifact") or {}
        payload_value = (
            _extract_artifact_payload(artifact)
            if isinstance(artifact, Mapping)
            else None
        )
        return RemoteTaskEvent(
            kind="artifact",
            text=payload_value if isinstance(payload_value, str) else None,
            result=payload_value,
            artifact=artifact if isinstance(artifact, Mapping) else None,
            done=bool(artifact_update.get("lastChunk")),
            context_id=artifact_update.get("contextId"),
            task_id=artifact_update.get("taskId"),
            agent_url=agent_url,
            meta={
                **dict(artifact_update.get("metadata") or {}),
                "append": artifact_update.get("append"),
                "last_chunk": artifact_update.get("lastChunk"),
            },
            raw=event,
        )
    return None


def _push_configs_url(
    agent_url: str, task_id: str, config_id: str | None = None
) -> str:
    """Return the URL of a task's push configs (or of one of them)."""
    url = f"{_normalize_base_url(agent_url)}/tasks/{task_id}"
    url += "/pushNotificationConfigs"
    return f"{url}/{config_id}" if config_id is not None else url


async def _raise_for_stream_status(response: httpx.Response) -> None:
    """Raise for an error status of a streaming response."""
    if response.status_code >= 400:
        body = await response.aread()
        _raise_for_status(
            response, body=body.decode("utf-8", errors="replace")
        )


async def _sse_payloads(response: httpx.Response) -> AsyncIterator[Any]:
    """Yield the decoded JSON of each SSE ``data:`` line."""
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        if raw:
            yield json.loads(raw)


@dataclass(slots=True)
class _StreamState:
    """Tracks a streamed message: its task, text and final result."""

    agent_url: str
    text_chunks: list[str] = field(default_factory=list)
    final_result: object = None
    context_id: str | None = None
    task_id: str | None = None

    def handle(self, event: Mapping[str, Any]) -> RemoteStreamEvent | None:
        """Track one stream event; return what to forward, if anything."""
        task = event.get("task")
        if isinstance(task, Mapping):
            return self._on_task(task)
        status_update = event.get("statusUpdate")
        if isinstance(status_update, Mapping):
            return self._on_status_update(status_update)
        artifact_update = event.get("artifactUpdate")
        if isinstance(artifact_update, Mapping):
            return self._on_artifact_update(artifact_update)
        return None

    def _on_task(self, task: Mapping[str, Any]) -> RemoteStreamEvent:
        self.task_id = task.get("id", self.task_id)
        self.context_id = task.get("contextId", self.context_id)
        snapshot = _task_to_snapshot(task, agent_url=self.agent_url)
        _raise_for_remote_task(snapshot)
        if self.final_result is None and snapshot.result is not None:
            self.final_result = snapshot.result
        return RemoteStreamEvent(
            context_id=self.context_id,
            task_id=self.task_id,
            agent_url=self.agent_url,
        )

    def _on_status_update(
        self, status_update: Mapping[str, Any]
    ) -> RemoteStreamEvent:
        self.task_id = status_update.get("taskId", self.task_id)
        self.context_id = status_update.get("contextId", self.context_id)
        status = status_update.get("status")
        normalized = RemoteTaskStatus(
            state=_normalize_task_state(_extract_status_state(status)),
            message=_extract_status_detail(status),
            raw=status if isinstance(status, Mapping) else None,
        )
        final = bool(status_update.get("final"))
        if final or normalized.state in {
            RemoteTaskState.INPUT_REQUIRED,
            RemoteTaskState.AUTH_REQUIRED,
        }:
            _raise_for_remote_task(
                RemoteTaskSnapshot(
                    task_id=self.task_id or "",
                    context_id=self.context_id or "",
                    status=normalized,
                    agent_url=self.agent_url,
                )
            )
        return RemoteStreamEvent(
            done=final,
            context_id=self.context_id,
            task_id=self.task_id,
            agent_url=self.agent_url,
        )

    def _on_artifact_update(
        self, artifact_update: Mapping[str, Any]
    ) -> RemoteStreamEvent | None:
        self.task_id = artifact_update.get("taskId", self.task_id)
        self.context_id = artifact_update.get("contextId", self.context_id)
        artifact = artifact_update.get("artifact") or {}
        payload_value = _extract_artifact_payload(artifact)
        last_chunk = bool(artifact_update.get("lastChunk"))
        if not isinstance(payload_value, str):
            if payload_value is not None and last_chunk:
                self.final_result = payload_value
            return None
        if artifact_update.get("append") is False:
            # A non-append update replaces the text streamed so far.
            self.text_chunks.clear()
        self.text_chunks.append(payload_value)
        if last_chunk:
            self.final_result = "".join(self.text_chunks)
        return RemoteStreamEvent(
            text=payload_value,
            done=last_chunk,
            context_id=self.context_id,
            task_id=self.task_id,
            agent_url=self.agent_url,
            meta={
                **dict(artifact_update.get("metadata") or {}),
                "append": artifact_update.get("append"),
                "last_chunk": artifact_update.get("lastChunk"),
            },
        )

    def final_event(self) -> RemoteStreamEvent | None:
        """Return the closing result event, if the stream produced one."""
        if self.final_result is None and self.text_chunks:
            self.final_result = "".join(self.text_chunks)
        if self.final_result is None:
            return None
        return RemoteStreamEvent(
            result=self.final_result,
            done=True,
            context_id=self.context_id,
            task_id=self.task_id,
            agent_url=self.agent_url,
        )


@dataclass(slots=True)
class A2AHttpTransport(RemoteTransport, SupportsRemoteTasks):
    """Remote transport speaking the A2A HTTP+JSON binding."""

    version: str = _DEFAULT_VERSION
    headers: Mapping[str, str] | None = None
    agent_headers: Mapping[str, Mapping[str, str]] | None = None
    timeout_s: float | None = None
    client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def _client_context(
        self, timeout_s: float | None
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        if self.client is not None:
            yield self.client
            return
        httpx_module = _require_httpx()
        async with httpx_module.AsyncClient(timeout=timeout_s) as client:
            yield client

    def _base_headers(self, agent_url: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/a2a+json",
            "Accept": "application/a2a+json",
            "A2A-Version": self.version,
        }
        if self.headers:
            headers.update(self.headers)
        if self.agent_headers and agent_url is not None:
            normalized_agent_url = _normalize_base_url(agent_url)
            agent_specific = self.agent_headers.get(
                agent_url
            ) or self.agent_headers.get(normalized_agent_url)
            if agent_specific is None:
                agent_specific = next(
                    (
                        configured_headers
                        for configured_url, configured_headers in (
                            self.agent_headers.items()
                        )
                        if _normalize_base_url(configured_url)
                        == normalized_agent_url
                    ),
                    None,
                )
            if agent_specific:
                headers.update(agent_specific)
        return headers

    @override
    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        payload = _build_send_message(request, blocking=True)
        url = f"{_normalize_base_url(request.agent_url)}/message:send"
        timeout = request.timeout_s or self.timeout_s
        async with self._client_context(timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers=self._base_headers(request.agent_url),
            )
            body = response.text
            _raise_for_status(response, body=body)
            data = response.json()

        if "task" in data:
            task = data["task"]
            snapshot = _task_to_snapshot(task, agent_url=request.agent_url)
            _raise_for_remote_task(snapshot)
            return RemoteCallResult(
                result=snapshot.result,
                context_id=snapshot.context_id,
                task_id=snapshot.task_id,
                agent_url=request.agent_url,
                meta={"remote_task_state": snapshot.status.state.value},
            )
        if "message" in data:
            message = data["message"]
            parts = message.get("parts") or []
            values = [
                value
                for part in parts
                if (value := _extract_part_payload(part)) is not None
            ]
            result = values[0] if len(values) == 1 else values
            return RemoteCallResult(
                result=result,
                context_id=message.get("contextId"),
                task_id=message.get("taskId"),
                agent_url=request.agent_url,
            )
        msg = "Unexpected A2A response payload"
        raise RuntimeError(msg)

    @override
    async def send_task(
        self, request: RemoteCallRequest, *, blocking: bool = False
    ) -> RemoteTaskSnapshot:
        payload = _build_send_message(request, blocking=blocking)
        url = f"{_normalize_base_url(request.agent_url)}/message:send"
        timeout = request.timeout_s or self.timeout_s
        async with self._client_context(timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers=self._base_headers(request.agent_url),
            )
            body = response.text
            _raise_for_status(response, body=body)
            data = response.json()
        task = data.get("task")
        if not isinstance(task, Mapping):
            msg = "Unexpected A2A send_task response payload"
            raise A2AResponseError(msg)
        return _task_to_snapshot(task, agent_url=request.agent_url)

    @override
    async def stream(
        self, request: RemoteCallRequest
    ) -> AsyncIterator[RemoteStreamEvent]:
        payload = _build_send_message(request, blocking=False)
        url = f"{_normalize_base_url(request.agent_url)}/message:stream"
        timeout = request.timeout_s or self.timeout_s
        state = _StreamState(agent_url=request.agent_url)
        async with (
            self._client_context(timeout) as client,
            client.stream(
                "POST",
                url,
                json=payload,
                headers=self._base_headers(request.agent_url),
            ) as response,
        ):
            await _raise_for_stream_status(response)
            async for event in _sse_payloads(response):
                stream_event = state.handle(event)
                if stream_event is not None:
                    yield stream_event
        final_event = state.final_event()
        if final_event is not None:
            yield final_event

    @override
    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        url = f"{_normalize_base_url(agent_url)}/tasks/{task_id}:cancel"
        async with self._client_context(self.timeout_s) as client:
            response = await client.post(
                url, headers=self._base_headers(agent_url)
            )
            _raise_for_status(response, body=response.text)

    @override
    async def get_task(
        self,
        *,
        agent_url: str,
        task_id: str,
        history_length: int | None = None,
    ) -> RemoteTaskSnapshot:
        url = f"{_normalize_base_url(agent_url)}/tasks/{task_id}"
        params: dict[str, Any] = {}
        if history_length is not None:
            params["historyLength"] = history_length
        async with self._client_context(self.timeout_s) as client:
            response = await client.get(
                url, params=params, headers=self._base_headers(agent_url)
            )
            _raise_for_status(response, body=response.text)
            data = response.json()
        return _task_to_snapshot(data, agent_url=agent_url)

    @override
    async def list_tasks(
        self,
        *,
        agent_url: str,
        context_id: str | None = None,
        status: RemoteTaskState | str | None = None,
        page_size: int = 100,
        page_token: str | None = None,
        history_length: int | None = None,
        include_artifacts: bool = False,
    ) -> RemoteTaskPage:
        url = f"{_normalize_base_url(agent_url)}/tasks"
        params: dict[str, Any] = {"pageSize": page_size}
        if context_id is not None:
            params["contextId"] = context_id
        if status is not None:
            params["status"] = (
                status.value if isinstance(status, RemoteTaskState) else status
            )
        if page_token is not None:
            params["pageToken"] = page_token
        if history_length is not None:
            params["historyLength"] = history_length
        if include_artifacts:
            params["includeArtifacts"] = "true"
        async with self._client_context(self.timeout_s) as client:
            response = await client.get(
                url, params=params, headers=self._base_headers(agent_url)
            )
            _raise_for_status(response, body=response.text)
            data = response.json()
        tasks = data.get("tasks") or []
        return RemoteTaskPage(
            tasks=[
                _task_to_snapshot(task, agent_url=agent_url)
                for task in tasks
                if isinstance(task, Mapping)
            ],
            next_page_token=str(data.get("nextPageToken") or ""),
            page_size=int(data.get("pageSize") or page_size),
            total_size=int(data.get("totalSize") or len(tasks)),
        )

    @override
    async def subscribe_task(
        self, *, agent_url: str, task_id: str
    ) -> AsyncIterator[RemoteTaskEvent]:
        url = f"{_normalize_base_url(agent_url)}/tasks/{task_id}:subscribe"
        async with (
            self._client_context(self.timeout_s) as client,
            client.stream(
                "GET", url, headers=self._base_headers(agent_url)
            ) as response,
        ):
            await _raise_for_stream_status(response)
            async for parsed in _sse_payloads(response):
                event = _event_to_task_event(parsed, agent_url=agent_url)
                if event is None:
                    continue
                if event.status is not None:
                    snapshot = event.task or RemoteTaskSnapshot(
                        task_id=event.task_id or task_id,
                        context_id=event.context_id or "",
                        status=event.status,
                        agent_url=agent_url,
                    )
                    _raise_for_remote_task(snapshot)
                yield event

    @override
    async def set_task_push_notification_config(
        self,
        *,
        agent_url: str,
        task_id: str,
        config_id: str,
        config: Mapping[str, Any],
    ) -> RemotePushNotificationBinding:
        url = _push_configs_url(agent_url, task_id)
        payload: Mapping[str, Any]
        if (
            "pushNotificationConfig" in config
            or "push_notification_config" in config
        ):
            payload = config
        else:
            payload = {"pushNotificationConfig": dict(config)}
        async with self._client_context(self.timeout_s) as client:
            response = await client.post(
                url,
                params={"configId": config_id},
                json=payload,
                headers=self._base_headers(agent_url),
            )
            _raise_for_status(response, body=response.text)
            data = response.json()
        return _push_binding(data)

    @override
    async def get_task_push_notification_config(
        self,
        *,
        agent_url: str,
        task_id: str,
        config_id: str,
    ) -> RemotePushNotificationBinding:
        url = _push_configs_url(agent_url, task_id, config_id)
        async with self._client_context(self.timeout_s) as client:
            response = await client.get(
                url, headers=self._base_headers(agent_url)
            )
            _raise_for_status(response, body=response.text)
            data = response.json()
        return _push_binding(data)

    @override
    async def list_task_push_notification_configs(
        self,
        *,
        agent_url: str,
        task_id: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> list[RemotePushNotificationBinding]:
        url = _push_configs_url(agent_url, task_id)
        params: dict[str, Any] = {"pageSize": page_size}
        if page_token is not None:
            params["pageToken"] = page_token
        async with self._client_context(self.timeout_s) as client:
            response = await client.get(
                url, params=params, headers=self._base_headers(agent_url)
            )
            _raise_for_status(response, body=response.text)
            data = response.json()
        configs = data.get("configs") or []
        return [
            _push_binding(config)
            for config in configs
            if isinstance(config, Mapping)
        ]

    @override
    async def delete_task_push_notification_config(
        self,
        *,
        agent_url: str,
        task_id: str,
        config_id: str,
    ) -> None:
        url = _push_configs_url(agent_url, task_id, config_id)
        async with self._client_context(self.timeout_s) as client:
            response = await client.delete(
                url, headers=self._base_headers(agent_url)
            )
            _raise_for_status(response, body=response.text)


__all__ = ["A2AHttpTransport", "A2AResponseError"]
