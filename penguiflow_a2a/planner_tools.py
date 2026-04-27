"""Planner tool helpers for calling remote A2A agents.

The flow runtime supports agent-to-agent calls via :func:`penguiflow.remote.RemoteNode`.
ReactPlanner tool execution uses :class:`penguiflow.planner.context.ToolContext`, so it
cannot call :func:`penguiflow.remote.RemoteNode` directly.

This module provides a small wrapper that turns an A2A remote invocation into a regular
planner tool (a :class:`penguiflow.catalog.NodeSpec`).
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from penguiflow.catalog import NodeSpec, SideEffect
from penguiflow.node import Node, NodePolicy
from penguiflow.planner.context import ToolContext
from penguiflow.remote import (
    RemoteCallRequest,
    RemoteTaskAuthRequired,
    RemoteTaskInputRequired,
    RemoteTaskSnapshot,
    RemoteTaskState,
    RemoteTransport,
)
from penguiflow.state import RemoteBinding
from penguiflow.types import Headers, Message

ArgsModelT = TypeVar("ArgsModelT", bound=BaseModel)

PayloadBuilder = Callable[[BaseModel, ToolContext], Any]
MetadataBuilder = Callable[[BaseModel, ToolContext], Mapping[str, Any] | None]
ExecutionMode = Literal["auto", "blocking", "stream", "task"]
_FAILED_REMOTE_STATES = {
    RemoteTaskState.FAILED,
    RemoteTaskState.CANCELLED,
    RemoteTaskState.REJECTED,
}


def _resolve_tenant(ctx: ToolContext) -> str:
    raw = ctx.tool_context.get("tenant")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "default"


def _tool_context_string(ctx: ToolContext, *keys: str) -> str | None:
    for key in keys:
        value = ctx.tool_context.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _planner_state_store(ctx: ToolContext) -> Any | None:
    candidate = ctx.tool_context.get("state_store")
    if candidate is not None:
        return candidate
    planner = getattr(ctx, "_planner", None)
    return getattr(planner, "_state_store", None)


def _supports_keyword(func: Any, name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    return name in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )


async def _find_conversation_binding(
    ctx: ToolContext,
    *,
    agent_url: str,
    skill: str,
) -> RemoteBinding | None:
    router_session_id = _tool_context_string(ctx, "session_id")
    if router_session_id is None:
        return None
    store = _planner_state_store(ctx)
    finder = getattr(store, "find_binding", None)
    if finder is None:
        return None
    kwargs: dict[str, Any] = {
        "router_session_id": router_session_id,
        "agent_url": agent_url,
        "remote_skill": skill,
    }
    if _supports_keyword(finder, "tenant_id"):
        kwargs["tenant_id"] = _tool_context_string(ctx, "tenant_id", "tenant")
    if _supports_keyword(finder, "user_id"):
        kwargs["user_id"] = _tool_context_string(ctx, "user_id")
    binding = await finder(**kwargs)
    if binding is None or binding.is_terminal:
        return None
    return binding


async def _save_conversation_binding(
    ctx: ToolContext,
    *,
    agent_url: str,
    skill: str,
    context_id: str | None,
    task_id: str | None,
    is_terminal: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    if context_id is None or task_id is None:
        return
    store = _planner_state_store(ctx)
    saver = getattr(store, "save_remote_binding", None)
    if saver is None:
        return
    router_session_id = _tool_context_string(ctx, "session_id")
    if router_session_id is None:
        return
    trace_id = _tool_context_string(ctx, "trace_id", "_current_tool_call_id") or f"planner:{uuid.uuid4().hex}"
    await saver(
        RemoteBinding(
            trace_id=trace_id,
            context_id=context_id,
            task_id=task_id,
            agent_url=agent_url,
            router_session_id=router_session_id,
            remote_skill=skill,
            tenant_id=_tool_context_string(ctx, "tenant_id", "tenant"),
            user_id=_tool_context_string(ctx, "user_id"),
            last_remote_task_id=task_id,
            is_terminal=is_terminal,
            metadata={"source": "a2a_agent_toolset", **dict(metadata or {})},
        )
    )


async def _mark_conversation_binding_terminal(
    ctx: ToolContext,
    *,
    binding: RemoteBinding | None,
    task_id: str | None,
) -> None:
    store = _planner_state_store(ctx)
    marker = getattr(store, "mark_binding_terminal", None)
    if marker is None or binding is None or task_id is None:
        return
    await marker(trace_id=binding.trace_id, context_id=binding.context_id, task_id=task_id)


async def _pause_for_remote_state(
    ctx: ToolContext,
    *,
    snapshot: RemoteTaskSnapshot,
    skill: str,
    agent_url: str,
    reason: Literal["await_input", "approval_required"],
) -> Any:
    pause = getattr(ctx, "pause", None)
    if pause is None:
        return {
            "status": snapshot.status.state.value,
            "message": snapshot.status.message,
            "remote_task_id": snapshot.task_id,
            "remote_context_id": snapshot.context_id,
            "remote_agent_url": agent_url,
            "remote_skill": skill,
        }
    return await pause(
        reason,
        {
            "message": snapshot.status.message,
            "remote_status": snapshot.status.state.value,
            "remote_task_id": snapshot.task_id,
            "remote_context_id": snapshot.context_id,
            "remote_agent_url": agent_url,
            "remote_skill": skill,
        },
    )


async def _handle_remote_pause_exception(
    ctx: ToolContext,
    *,
    exc: RemoteTaskInputRequired | RemoteTaskAuthRequired,
    skill: str,
    agent_url: str,
) -> Any:
    snapshot = exc.snapshot
    await _save_conversation_binding(
        ctx,
        agent_url=agent_url,
        skill=skill,
        context_id=snapshot.context_id,
        task_id=snapshot.task_id,
        is_terminal=False,
        metadata={
            "awaiting_remote_input": isinstance(exc, RemoteTaskInputRequired),
            "awaiting_remote_auth": isinstance(exc, RemoteTaskAuthRequired),
        },
    )
    return await _pause_for_remote_state(
        ctx,
        snapshot=snapshot,
        skill=skill,
        agent_url=agent_url,
        reason="await_input" if isinstance(exc, RemoteTaskInputRequired) else "approval_required",
    )


def _task_transport(transport: RemoteTransport) -> Any | None:
    required = ("send_task", "get_task", "subscribe_task")
    if all(callable(getattr(transport, name, None)) for name in required):
        return transport
    return None


def _should_continue_existing_task(binding: RemoteBinding | None) -> bool:
    if binding is None or binding.is_terminal:
        return False
    metadata = dict(binding.metadata or {})
    return bool(metadata.get("awaiting_remote_input") or metadata.get("awaiting_remote_auth"))


def _raise_for_failed_task(snapshot: RemoteTaskSnapshot) -> None:
    if snapshot.status.state in _FAILED_REMOTE_STATES:
        raise RuntimeError(f"Remote task failed: {snapshot.status.message or snapshot.status.state.value}")


async def _wait_for_task_completion(
    *,
    transport: Any,
    ctx: ToolContext,
    agent_url: str,
    task_id: str,
    skill: str,
    stream_id: str,
    chunk_channel: str,
    use_subscription: bool,
    poll_interval_s: float,
    max_poll_attempts: int,
) -> RemoteTaskSnapshot:
    seq = 0
    if use_subscription and callable(getattr(transport, "subscribe_task", None)):
        latest_snapshot: RemoteTaskSnapshot | None = None
        try:
            async for event in transport.subscribe_task(agent_url=agent_url, task_id=task_id):
                latest_snapshot = event.task or latest_snapshot
                if event.text is not None:
                    await ctx.emit_chunk(
                        stream_id=stream_id,
                        seq=seq,
                        text=event.text,
                        done=event.done,
                        meta={
                            "channel": chunk_channel,
                            "remote_agent_url": agent_url,
                            "remote_task_id": event.task_id or task_id,
                            "remote_context_id": event.context_id,
                            "remote_skill": skill,
                            "remote_event_kind": event.kind,
                        },
                    )
                    seq += 1
                if event.task is not None and event.task.is_terminal:
                    return event.task
                if event.done and latest_snapshot is not None and latest_snapshot.is_terminal:
                    return latest_snapshot
            if latest_snapshot is not None and latest_snapshot.is_terminal:
                return latest_snapshot
        except RuntimeError:
            # Some agents expose task polling but reject subscription when streaming is disabled.
            pass

    for _ in range(max_poll_attempts):
        snapshot = await transport.get_task(agent_url=agent_url, task_id=task_id)
        if snapshot.is_terminal or snapshot.status.state in {
            RemoteTaskState.INPUT_REQUIRED,
            RemoteTaskState.AUTH_REQUIRED,
        }:
            return snapshot
        await asyncio.sleep(poll_interval_s)
    return await transport.get_task(agent_url=agent_url, task_id=task_id)


def _merge_metadata(
    *,
    base: Mapping[str, Any] | None,
    ctx: ToolContext,
    include_tool_context_keys: Sequence[str],
    override: Mapping[str, Any] | None,
    builder: MetadataBuilder | None,
    args: BaseModel,
) -> Mapping[str, Any] | None:
    merged: dict[str, Any] = dict(base or {})
    for key in include_tool_context_keys:
        if key in merged:
            continue
        value = ctx.tool_context.get(key)
        if value is None:
            continue
        merged[key] = value

    if override:
        for key, value in override.items():
            if value is None:
                continue
            merged[key] = value

    if builder is not None:
        extra = builder(args, ctx)
        if extra:
            for key, value in extra.items():
                if value is None:
                    continue
                merged[key] = value

    if not merged:
        return None
    return merged


@dataclass(slots=True)
class A2AAgentToolset:
    """Create planner-callable tool specs backed by an A2A agent."""

    agent_url: str
    transport: RemoteTransport
    agent_card: Mapping[str, Any] | None = None

    default_timeout_s: float | None = None
    default_metadata: Mapping[str, Any] | None = None
    include_tool_context_keys: Sequence[str] = ("tenant", "session_id", "task_id", "user_id")

    def tool(
        self,
        *,
        name: str,
        skill: str,
        args_model: type[ArgsModelT],
        out_model: type[BaseModel],
        desc: str,
        tags: Sequence[str] = ("a2a", "remote"),
        auth_scopes: Sequence[str] = (),
        side_effects: SideEffect = "external",
        streaming: bool = False,
        timeout_s: float | None = None,
        metadata: Mapping[str, Any] | None = None,
        metadata_builder: MetadataBuilder | None = None,
        payload_builder: PayloadBuilder | None = None,
        cancel_on_cancel: bool = True,
        chunk_channel: str = "answer",
        execution_mode: ExecutionMode = "auto",
        use_subscription: bool = True,
        poll_interval_s: float = 0.25,
        max_poll_attempts: int = 120,
    ) -> NodeSpec:
        """Build a :class:`~penguiflow.catalog.NodeSpec` that calls the remote agent."""

        async def _impl(args: ArgsModelT, ctx: ToolContext) -> Any:
            payload = payload_builder(args, ctx) if payload_builder is not None else args.model_dump(mode="json")
            headers = Headers(tenant=_resolve_tenant(ctx))
            message = Message(payload=payload, headers=headers)
            merged_metadata = _merge_metadata(
                base=self.default_metadata,
                ctx=ctx,
                include_tool_context_keys=self.include_tool_context_keys,
                override=metadata,
                builder=metadata_builder,
                args=args,
            )
            existing_binding = await _find_conversation_binding(ctx, agent_url=self.agent_url, skill=skill)
            existing_task_id = (
                existing_binding.task_id
                if existing_binding is not None and _should_continue_existing_task(existing_binding)
                else None
            )

            request = RemoteCallRequest(
                message=message,
                skill=skill,
                agent_url=self.agent_url,
                agent_card=self.agent_card,
                metadata=merged_metadata,
                timeout_s=timeout_s or self.default_timeout_s,
                context_id=existing_binding.context_id if existing_binding is not None else None,
                task_id=existing_task_id,
            )

            task_transport = _task_transport(self.transport)
            resolved_execution = execution_mode
            if resolved_execution == "auto":
                if streaming:
                    resolved_execution = "stream"
                else:
                    resolved_execution = "blocking"
            elif resolved_execution == "task" and task_transport is None:
                resolved_execution = "blocking"

            if resolved_execution == "task" and task_transport is not None:
                snapshot = await task_transport.send_task(request, blocking=False)
                _raise_for_failed_task(snapshot)
                await _save_conversation_binding(
                    ctx,
                    agent_url=snapshot.agent_url or self.agent_url,
                    skill=skill,
                    context_id=snapshot.context_id or request.context_id,
                    task_id=snapshot.task_id,
                    is_terminal=False,
                    metadata={
                        "remote_task_state": snapshot.status.state.value,
                        "remote_task_terminal": snapshot.is_terminal,
                        "awaiting_remote_input": snapshot.status.state is RemoteTaskState.INPUT_REQUIRED,
                        "awaiting_remote_auth": snapshot.status.state is RemoteTaskState.AUTH_REQUIRED,
                    },
                )
                if snapshot.status.state is RemoteTaskState.INPUT_REQUIRED:
                    return await _pause_for_remote_state(
                        ctx,
                        snapshot=snapshot,
                        skill=skill,
                        agent_url=snapshot.agent_url or self.agent_url,
                        reason="await_input",
                    )
                if snapshot.status.state is RemoteTaskState.AUTH_REQUIRED:
                    return await _pause_for_remote_state(
                        ctx,
                        snapshot=snapshot,
                        skill=skill,
                        agent_url=snapshot.agent_url or self.agent_url,
                        reason="approval_required",
                    )
                if not snapshot.is_terminal:
                    snapshot = await _wait_for_task_completion(
                        transport=task_transport,
                        ctx=ctx,
                        agent_url=snapshot.agent_url or self.agent_url,
                        task_id=snapshot.task_id,
                        skill=skill,
                        stream_id=f"a2a:{name}:{uuid.uuid4().hex}",
                        chunk_channel=chunk_channel,
                        use_subscription=use_subscription,
                        poll_interval_s=poll_interval_s,
                        max_poll_attempts=max_poll_attempts,
                    )
                    _raise_for_failed_task(snapshot)
                    await _save_conversation_binding(
                        ctx,
                        agent_url=snapshot.agent_url or self.agent_url,
                        skill=skill,
                        context_id=snapshot.context_id or request.context_id,
                        task_id=snapshot.task_id,
                        is_terminal=False,
                        metadata={
                            "remote_task_state": snapshot.status.state.value,
                            "remote_task_terminal": snapshot.is_terminal,
                        },
                    )
                if snapshot.status.state is RemoteTaskState.INPUT_REQUIRED:
                    return await _pause_for_remote_state(
                        ctx,
                        snapshot=snapshot,
                        skill=skill,
                        agent_url=snapshot.agent_url or self.agent_url,
                        reason="await_input",
                    )
                if snapshot.status.state is RemoteTaskState.AUTH_REQUIRED:
                    return await _pause_for_remote_state(
                        ctx,
                        snapshot=snapshot,
                        skill=skill,
                        agent_url=snapshot.agent_url or self.agent_url,
                        reason="approval_required",
                    )
                return snapshot.result

            if resolved_execution == "blocking":
                try:
                    result = await self.transport.send(request)
                except (RemoteTaskInputRequired, RemoteTaskAuthRequired) as exc:
                    return await _handle_remote_pause_exception(
                        ctx,
                        exc=exc,
                        skill=skill,
                        agent_url=self.agent_url,
                    )
                await _save_conversation_binding(
                    ctx,
                    agent_url=result.agent_url or self.agent_url,
                    skill=skill,
                    context_id=result.context_id or request.context_id,
                    task_id=result.task_id,
                    is_terminal=False,
                    metadata=dict(result.meta or {}),
                )
                return result.result

            stream_id = f"a2a:{name}:{uuid.uuid4().hex}"
            seq = 0
            remote_task_id: str | None = None
            remote_context_id: str | None = request.context_id
            remote_agent_url: str = self.agent_url
            try:
                async for event in self.transport.stream(request):
                    if event.task_id is not None:
                        remote_task_id = event.task_id
                    if event.context_id is not None:
                        remote_context_id = event.context_id
                    if event.agent_url is not None:
                        remote_agent_url = event.agent_url

                    if event.text is not None:
                        meta: dict[str, Any] = {
                            "channel": chunk_channel,
                            "remote_agent_url": remote_agent_url,
                            "remote_task_id": remote_task_id,
                            "remote_context_id": remote_context_id,
                            "remote_skill": skill,
                        }
                        if event.meta:
                            meta.update(dict(event.meta))
                        await ctx.emit_chunk(
                            stream_id=stream_id,
                            seq=seq,
                            text=event.text,
                            done=event.done,
                            meta=meta,
                        )
                        seq += 1

                    if event.result is not None:
                        await _save_conversation_binding(
                            ctx,
                            agent_url=remote_agent_url,
                            skill=skill,
                            context_id=remote_context_id,
                            task_id=remote_task_id,
                            is_terminal=False,
                        )
                        return event.result
                await _save_conversation_binding(
                    ctx,
                    agent_url=remote_agent_url,
                    skill=skill,
                    context_id=remote_context_id,
                    task_id=remote_task_id,
                    is_terminal=False,
                )
                return None
            except (RemoteTaskInputRequired, RemoteTaskAuthRequired) as exc:
                return await _handle_remote_pause_exception(
                    ctx,
                    exc=exc,
                    skill=skill,
                    agent_url=remote_agent_url,
                )
            except asyncio.CancelledError:
                if cancel_on_cancel and remote_task_id is not None:
                    try:
                        await self.transport.cancel(agent_url=self.agent_url, task_id=remote_task_id)
                    except Exception:
                        # Cancellation is best-effort; preserve local cancellation.
                        pass
                raise

        node = Node(_impl, name=name, policy=NodePolicy(validate="none"))
        return NodeSpec(
            node=node,
            name=name,
            desc=desc,
            args_model=args_model,
            out_model=out_model,
            side_effects=side_effects,
            tags=tuple(tags),
            auth_scopes=tuple(auth_scopes),
            extra={
                "a2a": {
                    "agent_url": self.agent_url,
                    "skill": skill,
                    "streaming": streaming,
                }
            },
        )


__all__ = ["A2AAgentToolset"]
