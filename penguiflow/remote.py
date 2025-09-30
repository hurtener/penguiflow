"""Remote transport protocol and helper node for PenguiFlow."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .core import TraceCancelled
from .node import Node, NodePolicy
from .state import RemoteBinding
from .types import Message

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from .core import Context, PenguiFlow


@dataclass(slots=True)
class RemoteCallRequest:
    """Input to :class:`RemoteTransport` implementations."""

    message: Message
    skill: str
    agent_url: str
    agent_card: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None
    timeout_s: float | None = None


@dataclass(slots=True)
class RemoteCallResult:
    """Return value for :meth:`RemoteTransport.send`."""

    result: Any
    context_id: str | None = None
    task_id: str | None = None
    agent_url: str | None = None
    meta: Mapping[str, Any] | None = None


@dataclass(slots=True)
class RemoteStreamEvent:
    """Streaming event yielded by :meth:`RemoteTransport.stream`."""

    text: str | None = None
    done: bool = False
    meta: Mapping[str, Any] | None = None
    context_id: str | None = None
    task_id: str | None = None
    agent_url: str | None = None
    result: Any | None = None


class RemoteTransport(Protocol):
    """Protocol describing the minimal remote invocation surface."""

    async def send(self, request: RemoteCallRequest) -> RemoteCallResult:
        """Perform a unary remote call."""

    def stream(self, request: RemoteCallRequest) -> AsyncIterator[RemoteStreamEvent]:
        """Perform a remote call that yields streaming events."""

    async def cancel(self, *, agent_url: str, task_id: str) -> None:
        """Cancel a remote task identified by ``task_id`` at ``agent_url``."""


def RemoteNode(
    *,
    transport: RemoteTransport,
    skill: str,
    agent_url: str,
    name: str,
    agent_card: Mapping[str, Any] | None = None,
    policy: NodePolicy | None = None,
    streaming: bool = False,
    record_binding: bool = True,
) -> Node:
    """Create a node that proxies work to a remote agent via ``transport``."""

    node_policy = policy or NodePolicy()

    async def _record_binding(
        *,
        runtime: PenguiFlow,
        trace_id: str,
        context_id: str | None,
        task_id: str | None,
        agent_url_override: str | None,
    ) -> tuple[asyncio.Task[None], asyncio.Event] | None:
        if context_id is None or task_id is None:
            return None

        agent_ref = agent_url_override or agent_url

        if record_binding:
            binding = RemoteBinding(
                trace_id=trace_id,
                context_id=context_id,
                task_id=task_id,
                agent_url=agent_ref,
            )
            await runtime.save_remote_binding(binding)

        cancel_event = runtime.ensure_trace_event(trace_id)
        if cancel_event.is_set():
            with suppress(Exception):
                await transport.cancel(agent_url=agent_ref, task_id=task_id)
            raise TraceCancelled(trace_id)

        async def _mirror_cancel() -> None:
            try:
                await cancel_event.wait()
            except asyncio.CancelledError:
                return
            with suppress(Exception):
                await transport.cancel(agent_url=agent_ref, task_id=task_id)

        cancel_task = asyncio.create_task(_mirror_cancel())
        runtime.register_external_task(trace_id, cancel_task)
        return cancel_task, cancel_event

    async def _remote_impl(message: Message, ctx: Context) -> Any:
        if not isinstance(message, Message):
            raise TypeError("Remote nodes require penguiflow.types.Message inputs")

        runtime = ctx.runtime
        if runtime is None:
            raise RuntimeError("Context is not bound to a running PenguiFlow")

        trace_id = message.trace_id
        cancel_task: asyncio.Task[None] | None = None
        cancel_event: asyncio.Event | None = None
        binding_registered = False

        request = RemoteCallRequest(
            message=message,
            skill=skill,
            agent_url=agent_url,
            agent_card=agent_card,
            metadata=message.meta,
            timeout_s=node_policy.timeout_s,
        )

        async def _ensure_binding(
            *,
            context_id: str | None,
            task_id: str | None,
            agent_url_override: str | None,
        ) -> None:
            nonlocal cancel_task, cancel_event, binding_registered
            if binding_registered:
                return
            if context_id is None or task_id is None:
                return
            record = await _record_binding(
                runtime=runtime,
                trace_id=trace_id,
                context_id=context_id,
                task_id=task_id,
                agent_url_override=agent_url_override,
            )
            if record is None:
                return
            cancel_task, cancel_event = record
            binding_registered = True

        async def _cleanup_cancel_task() -> None:
            if cancel_task is not None:
                if cancel_event is not None and cancel_event.is_set():
                    with suppress(BaseException):
                        await cancel_task
                    return
                if not cancel_task.done():
                    cancel_task.cancel()
                with suppress(BaseException):
                    await cancel_task

        try:
            if streaming:
                final_result: Any | None = None
                async for event in transport.stream(request):
                    await _ensure_binding(
                        context_id=event.context_id,
                        task_id=event.task_id,
                        agent_url_override=event.agent_url,
                    )
                    if event.text is not None:
                        meta = dict(event.meta) if event.meta is not None else None
                        await ctx.emit_chunk(
                            parent=message,
                            text=event.text,
                            done=event.done,
                            meta=meta,
                        )
                    if event.result is not None:
                        final_result = event.result
                return final_result

            result = await transport.send(request)
            await _ensure_binding(
                context_id=result.context_id,
                task_id=result.task_id,
                agent_url_override=result.agent_url,
            )
            return result.result
        except TraceCancelled:
            raise
        finally:
            await _cleanup_cancel_task()

    return Node(_remote_impl, name=name, policy=node_policy)


__all__ = [
    "RemoteCallRequest",
    "RemoteCallResult",
    "RemoteStreamEvent",
    "RemoteTransport",
    "RemoteNode",
]
