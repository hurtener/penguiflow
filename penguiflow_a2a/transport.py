from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from penguiflow.remote import RemoteCallRequest, RemoteCallResult, RemoteStreamEvent, RemoteTransport

_DEFAULT_VERSION = "0.3"


def _require_httpx():
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is required for A2AHttpTransport. Install with `pip install penguiflow[a2a-client]`."
        ) from exc
    return httpx


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def _build_metadata(request: RemoteCallRequest) -> dict[str, Any]:
    metadata = dict(request.metadata or {})
    metadata.setdefault("skill", request.skill)
    return metadata


def _payload_to_part(payload: Any) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, bytes):
        encoded = base64.b64encode(payload).decode("ascii")
        return {"data": {"data": {"bytes": encoded}}}
    if isinstance(payload, str):
        return {"text": payload}
    return {"data": {"data": payload}}


def _build_send_message(request: RemoteCallRequest, *, blocking: bool) -> dict[str, Any]:
    part = _payload_to_part(request.message.payload)
    message = {
        "messageId": uuid.uuid4().hex,
        "role": "user",
        "parts": [part],
        "metadata": _build_metadata(request),
    }
    return {
        "message": message,
        "configuration": {"blocking": blocking},
    }


def _extract_part_payload(part: Mapping[str, Any]) -> Any:
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


def _extract_artifact_payload(artifact: Mapping[str, Any]) -> Any:
    parts = artifact.get("parts") or []
    values = [value for part in parts if (value := _extract_part_payload(part)) is not None]
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _extract_result_from_task(task: Mapping[str, Any]) -> Any:
    artifacts = task.get("artifacts") or []
    for artifact in artifacts:
        payload = _extract_artifact_payload(artifact)
        if payload is not None:
            return payload
    return None


def _extract_message_text(message: Any) -> str | None:
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


def _extract_status_state(status: Any) -> str | None:
    if not isinstance(status, Mapping):
        return None
    state = status.get("state")
    if isinstance(state, str) and state:
        return state
    return None


def _extract_status_detail(status: Any) -> str | None:
    if not isinstance(status, Mapping):
        return None
    return _extract_message_text(status.get("message"))


def _raise_for_status(response, *, body: str | None = None) -> None:
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
    raise RuntimeError(f"A2A request failed ({response.status_code}){detail_text}")


@dataclass(slots=True)
class A2AHttpTransport(RemoteTransport):
    version: str = _DEFAULT_VERSION
    headers: Mapping[str, str] | None = None
    timeout_s: float | None = None
    client: Any | None = None

    @asynccontextmanager
    async def _client_context(self, timeout: float | None):
        if self.client is not None:
            yield self.client
            return
        httpx = _require_httpx()
        async with httpx.AsyncClient(timeout=timeout) as client:
            yield client

    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/a2a+json",
            "Accept": "application/a2a+json",
            "A2A-Version": self.version,
        }
        if self.headers:
            headers.update(self.headers)
        return headers

    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        payload = _build_send_message(request, blocking=True)
        url = f"{_normalize_base_url(request.agent_url)}/message:send"
        timeout = request.timeout_s or self.timeout_s
        async with self._client_context(timeout) as client:
            response = await client.post(url, json=payload, headers=self._base_headers())
            body = response.text
            _raise_for_status(response, body=body)
            data = response.json()

        if "task" in data:
            task = data["task"]
            status = task.get("status")
            state = _extract_status_state(status)
            if state in {"failed", "cancelled", "rejected"}:
                detail = _extract_status_detail(status)
                raise RuntimeError(f"Remote task failed: {detail or state}")
            return RemoteCallResult(
                result=_extract_result_from_task(task),
                context_id=task.get("contextId"),
                task_id=task.get("id"),
                agent_url=request.agent_url,
            )
        if "message" in data:
            message = data["message"]
            parts = message.get("parts") or []
            values = [value for part in parts if (value := _extract_part_payload(part)) is not None]
            result = values[0] if len(values) == 1 else values
            return RemoteCallResult(
                result=result,
                context_id=message.get("contextId"),
                task_id=message.get("taskId"),
                agent_url=request.agent_url,
            )
        raise RuntimeError("Unexpected A2A response payload")

    async def stream(self, request: RemoteCallRequest) -> AsyncIterator[RemoteStreamEvent]:
        payload = _build_send_message(request, blocking=False)
        url = f"{_normalize_base_url(request.agent_url)}/message:stream"
        timeout = request.timeout_s or self.timeout_s
        text_chunks: list[str] = []
        final_result: Any | None = None
        context_id: str | None = None
        task_id: str | None = None

        async with self._client_context(timeout) as client:
            async with client.stream("POST", url, json=payload, headers=self._base_headers()) as response:
                body = None
                if response.status_code >= 400:
                    body = await response.aread()
                    _raise_for_status(response, body=body.decode("utf-8", errors="replace"))

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if not raw:
                        continue
                    event = json.loads(raw)
                    task = event.get("task")
                    if isinstance(task, Mapping):
                        task_id = task.get("id", task_id)
                        context_id = task.get("contextId", context_id)
                        state = _extract_status_state(task.get("status"))
                        if state in {"failed", "cancelled", "rejected"}:
                            detail = _extract_status_detail(task.get("status"))
                            raise RuntimeError(f"Remote task failed: {detail or state}")
                        if final_result is None:
                            candidate = _extract_result_from_task(task)
                            if candidate is not None:
                                final_result = candidate
                        yield RemoteStreamEvent(
                            context_id=context_id,
                            task_id=task_id,
                            agent_url=request.agent_url,
                        )
                        continue

                    status_update = event.get("statusUpdate")
                    if isinstance(status_update, Mapping):
                        task_id = status_update.get("taskId", task_id)
                        context_id = status_update.get("contextId", context_id)
                        status = status_update.get("status")
                        state = _extract_status_state(status)
                        if bool(status_update.get("final")) and state in {"failed", "cancelled", "rejected"}:
                            detail = _extract_status_detail(status)
                            raise RuntimeError(f"Remote task failed: {detail or state}")
                        yield RemoteStreamEvent(
                            done=bool(status_update.get("final")),
                            context_id=context_id,
                            task_id=task_id,
                            agent_url=request.agent_url,
                        )
                        continue

                    artifact_update = event.get("artifactUpdate")
                    if isinstance(artifact_update, Mapping):
                        task_id = artifact_update.get("taskId", task_id)
                        context_id = artifact_update.get("contextId", context_id)
                        artifact = artifact_update.get("artifact") or {}
                        payload_value = _extract_artifact_payload(artifact)
                        last_chunk = bool(artifact_update.get("lastChunk"))
                        if isinstance(payload_value, str):
                            text_chunks.append(payload_value)
                            yield RemoteStreamEvent(
                                text=payload_value,
                                done=last_chunk,
                                context_id=context_id,
                                task_id=task_id,
                                agent_url=request.agent_url,
                            )
                            if last_chunk:
                                final_result = "".join(text_chunks)
                            continue
                        if payload_value is not None and last_chunk:
                            final_result = payload_value
                        continue

        if final_result is None and text_chunks:
            final_result = "".join(text_chunks)
        if final_result is not None:
            yield RemoteStreamEvent(
                result=final_result,
                done=True,
                context_id=context_id,
                task_id=task_id,
                agent_url=request.agent_url,
            )

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        url = f"{_normalize_base_url(agent_url)}/tasks/{task_id}:cancel"
        async with self._client_context(self.timeout_s) as client:
            response = await client.post(url, headers=self._base_headers())
            _raise_for_status(response, body=response.text)


__all__ = ["A2AHttpTransport"]
