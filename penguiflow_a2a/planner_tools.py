"""Planner tool helpers for calling remote A2A agents.

This module provides a small wrapper that turns an A2A remote invocation, made
through a :class:`penguiflow.remote.RemoteTransport`, into a regular planner
tool (a :class:`penguiflow.catalog.NodeSpec`).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import uuid
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from penguiflow.catalog import NodeSpec, SideEffect
from penguiflow.node import Node, NodePolicy
from penguiflow.planner.context import ToolContext
from penguiflow.remote import (
    RemoteCallRequest,
    RemoteStreamEvent,
    RemoteTaskAuthRequired,
    RemoteTaskEvent,
    RemoteTaskInputRequired,
    RemoteTaskSnapshot,
    RemoteTaskState,
    RemoteTaskStatus,
    RemoteTransport,
)
from penguiflow.state import RemoteBinding
from penguiflow.types import Headers, Message

ArgsModelT = TypeVar("ArgsModelT", bound=BaseModel)

PayloadBuilder = Callable[[BaseModel, ToolContext], Any]
MetadataBuilder = Callable[[BaseModel, ToolContext], Mapping[str, Any] | None]
RemoteEventSink = Callable[[RemoteTaskEvent, ToolContext], Any]
ExecutionMode = Literal["auto", "blocking", "stream", "task"]
_FAILED_REMOTE_STATES = {
    RemoteTaskState.FAILED,
    RemoteTaskState.CANCELLED,
    RemoteTaskState.REJECTED,
}


def _coerce_text_result(result: object, out_model: type[BaseModel]) -> object:
    """Wrap a plain-text remote answer into a single-field ``out_model``.

    A2A agents usually answer with a text part. When the declared output model
    has exactly one field, the text becomes that field's value so it validates;
    any other result is returned unchanged.
    """
    if not isinstance(result, str):
        return result
    fields = list(out_model.model_fields)
    if len(fields) != 1:
        return result
    return {fields[0]: result}


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


def _planner_state_store(ctx: ToolContext) -> object:
    candidate = ctx.tool_context.get("state_store")
    if candidate is not None:
        return candidate
    planner = getattr(ctx, "planner", None)
    return getattr(planner, "state_store", None)


def _supports_keyword(func: Callable[..., object], name: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    return name in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
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
    binding: RemoteBinding | None = await finder(**kwargs)
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
    trace_id = (
        _tool_context_string(ctx, "trace_id", "_current_tool_call_id")
        or f"planner:{uuid.uuid4().hex}"
    )
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
    await marker(
        trace_id=binding.trace_id,
        context_id=binding.context_id,
        task_id=task_id,
    )


async def _pause_for_remote_state(
    ctx: ToolContext,
    *,
    snapshot: RemoteTaskSnapshot,
    skill: str,
    agent_url: str,
    reason: Literal["await_input", "approval_required"],
) -> object:
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
) -> object:
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
        reason="await_input"
        if isinstance(exc, RemoteTaskInputRequired)
        else "approval_required",
    )


@runtime_checkable
class _TaskTransport(Protocol):
    """The task operations the tool needs from a transport."""

    async def send_task(
        self, request: RemoteCallRequest, *, blocking: bool = False
    ) -> RemoteTaskSnapshot:
        """Create or continue a remote task."""
        ...

    async def get_task(
        self,
        *,
        agent_url: str,
        task_id: str,
        history_length: int | None = None,
    ) -> RemoteTaskSnapshot:
        """Fetch a remote task snapshot."""
        ...


@runtime_checkable
class _SubscribingTransport(Protocol):
    """A transport that streams a remote task's updates."""

    def subscribe_task(
        self, *, agent_url: str, task_id: str
    ) -> AsyncIterator[RemoteTaskEvent]:
        """Subscribe to a remote task's updates."""
        ...


def _task_transport(transport: RemoteTransport) -> _TaskTransport | None:
    required = ("send_task", "get_task")
    if isinstance(transport, _TaskTransport) and all(
        callable(getattr(transport, name, None)) for name in required
    ):
        return transport
    return None


def _should_continue_existing_task(binding: RemoteBinding | None) -> bool:
    if binding is None or binding.is_terminal:
        return False
    metadata = dict(binding.metadata or {})
    return bool(
        metadata.get("awaiting_remote_input")
        or metadata.get("awaiting_remote_auth")
    )


def _raise_for_failed_task(snapshot: RemoteTaskSnapshot) -> None:
    if snapshot.status.state in _FAILED_REMOTE_STATES:
        msg = (
            "Remote task failed: "
            f"{snapshot.status.message or snapshot.status.state.value}"
        )
        raise RuntimeError(msg)


def _event_from_snapshot(
    snapshot: RemoteTaskSnapshot,
    *,
    kind: str,
    agent_url: str | None = None,
    done: bool | None = None,
) -> RemoteTaskEvent:
    return RemoteTaskEvent(
        kind=kind,
        task=snapshot,
        status=snapshot.status,
        result=snapshot.result,
        done=snapshot.is_terminal if done is None else done,
        context_id=snapshot.context_id,
        task_id=snapshot.task_id,
        agent_url=snapshot.agent_url or agent_url,
        meta=snapshot.meta,
    )


def _remote_event_signature(event: RemoteTaskEvent) -> tuple[Any, ...]:
    status: RemoteTaskStatus | None = event.status
    return (
        event.kind,
        event.task_id,
        event.context_id,
        status.state.value if status is not None else None,
        status.message if status is not None else None,
        status.timestamp if status is not None else None,
        event.done,
    )


async def _emit_remote_task_event(
    ctx: ToolContext,
    configured_sink: RemoteEventSink | None,
    event: RemoteTaskEvent,
) -> None:
    """Forward a task event to the configured sinks (best effort).

    Routers use this to bridge A2A task events into their own UI/event bus.
    """
    sinks: list[Any] = []
    if configured_sink is not None:
        sinks.append(configured_sink)
    for key in ("a2a_remote_event_sink", "remote_task_event_sink"):
        runtime_sink = ctx.tool_context.get(key)
        if runtime_sink is not None and not any(
            runtime_sink is sink for sink in sinks
        ):
            sinks.append(runtime_sink)

    for sink in sinks:
        if not callable(sink):
            continue
        try:
            result = sink(event, ctx)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Progress hooks must not change remote execution semantics.
            continue


async def _follow_subscription(
    transport: _SubscribingTransport,
    *,
    ctx: ToolContext,
    agent_url: str,
    task_id: str,
    skill: str,
    stream_id: str,
    chunk_channel: str,
    remote_event_sink: RemoteEventSink | None,
) -> RemoteTaskSnapshot | None:
    """Stream a task's events as chunks; return it once it is terminal."""
    seq = 0
    latest_snapshot: RemoteTaskSnapshot | None = None
    async for event in transport.subscribe_task(
        agent_url=agent_url, task_id=task_id
    ):
        await _emit_remote_task_event(ctx, remote_event_sink, event)
        latest_snapshot = event.task or latest_snapshot
        if event.text is not None:
            chunk_meta: dict[str, Any] = {
                "channel": chunk_channel,
                "remote_agent_url": agent_url,
                "remote_task_id": event.task_id or task_id,
                "remote_context_id": event.context_id,
                "remote_skill": skill,
                "remote_event_kind": event.kind,
            }
            if event.meta:
                chunk_meta.update(dict(event.meta))
            await ctx.emit_chunk(
                stream_id=stream_id,
                seq=seq,
                text=event.text,
                done=event.done,
                meta=chunk_meta,
            )
            seq += 1
        if event.task is not None and event.task.is_terminal:
            return event.task
        if (
            event.done
            and latest_snapshot is not None
            and latest_snapshot.is_terminal
        ):
            return latest_snapshot
    if latest_snapshot is not None and latest_snapshot.is_terminal:
        return latest_snapshot
    return None


async def _poll_task(
    transport: _TaskTransport,
    *,
    ctx: ToolContext,
    agent_url: str,
    task_id: str,
    poll_interval_s: float,
    max_poll_attempts: int,
    remote_event_sink: RemoteEventSink | None,
) -> RemoteTaskSnapshot:
    """Poll a task until it ends, needs input or runs out of attempts."""
    last_poll_signature: tuple[Any, ...] | None = None
    for _ in range(max_poll_attempts):
        snapshot = await transport.get_task(
            agent_url=agent_url, task_id=task_id
        )
        event = _event_from_snapshot(
            snapshot, kind="status", agent_url=agent_url
        )
        signature = _remote_event_signature(event)
        if signature != last_poll_signature:
            await _emit_remote_task_event(ctx, remote_event_sink, event)
            last_poll_signature = signature
        if snapshot.is_terminal or snapshot.status.state in {
            RemoteTaskState.INPUT_REQUIRED,
            RemoteTaskState.AUTH_REQUIRED,
        }:
            return snapshot
        await asyncio.sleep(poll_interval_s)
    snapshot = await transport.get_task(agent_url=agent_url, task_id=task_id)
    event = _event_from_snapshot(snapshot, kind="status", agent_url=agent_url)
    if _remote_event_signature(event) != last_poll_signature:
        await _emit_remote_task_event(ctx, remote_event_sink, event)
    return snapshot


async def _wait_for_task_completion(
    *,
    transport: _TaskTransport,
    ctx: ToolContext,
    agent_url: str,
    task_id: str,
    skill: str,
    stream_id: str,
    chunk_channel: str,
    use_subscription: bool,
    poll_interval_s: float,
    max_poll_attempts: int,
    remote_event_sink: RemoteEventSink | None,
) -> RemoteTaskSnapshot:
    if (
        use_subscription
        and isinstance(transport, _SubscribingTransport)
        and callable(transport.subscribe_task)
    ):
        try:
            snapshot = await _follow_subscription(
                transport,
                ctx=ctx,
                agent_url=agent_url,
                task_id=task_id,
                skill=skill,
                stream_id=stream_id,
                chunk_channel=chunk_channel,
                remote_event_sink=remote_event_sink,
            )
        except RuntimeError:
            # Some agents expose task polling but reject subscription when
            # streaming is disabled.
            snapshot = None
        if snapshot is not None:
            return snapshot
    return await _poll_task(
        transport,
        ctx=ctx,
        agent_url=agent_url,
        task_id=task_id,
        poll_interval_s=poll_interval_s,
        max_poll_attempts=max_poll_attempts,
        remote_event_sink=remote_event_sink,
    )


def _merge_present(
    target: dict[str, Any], items: Iterable[tuple[str, object]]
) -> None:
    """Copy the items whose value is not ``None`` into ``target``."""
    for key, value in items:
        if value is not None:
            target[key] = value


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
    _merge_present(
        merged,
        (
            (key, ctx.tool_context.get(key))
            for key in include_tool_context_keys
            if key not in merged
        ),
    )
    if override:
        _merge_present(merged, override.items())
    if builder is not None:
        extra = builder(args, ctx)
        if extra:
            _merge_present(merged, extra.items())
    return merged or None


@dataclass(slots=True)
class A2AAgentToolset:
    """Create planner-callable tool specs backed by an A2A agent."""

    agent_url: str
    transport: RemoteTransport
    agent_card: Mapping[str, Any] | None = None

    default_timeout_s: float | None = None
    default_metadata: Mapping[str, Any] | None = None
    include_tool_context_keys: Sequence[str] = (
        "tenant",
        "session_id",
        "task_id",
        "user_id",
    )

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
        remote_event_sink: RemoteEventSink | None = None,
    ) -> NodeSpec:
        """Build a :class:`~penguiflow.catalog.NodeSpec` calling the agent."""
        remote_tool = _RemoteTool(
            toolset=self,
            name=name,
            skill=skill,
            out_model=out_model,
            streaming=streaming,
            timeout_s=timeout_s,
            metadata=metadata,
            metadata_builder=metadata_builder,
            payload_builder=payload_builder,
            cancel_on_cancel=cancel_on_cancel,
            chunk_channel=chunk_channel,
            execution_mode=execution_mode,
            use_subscription=use_subscription,
            poll_interval_s=poll_interval_s,
            max_poll_attempts=max_poll_attempts,
            remote_event_sink=remote_event_sink,
        )

        node = Node(
            remote_tool.run, name=name, policy=NodePolicy(validate="none")
        )
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


@dataclass(frozen=True, slots=True)
class _RemoteTool:
    """One remote skill exposed as a planner tool (see ``tool()``)."""

    toolset: A2AAgentToolset
    name: str
    skill: str
    out_model: type[BaseModel]
    streaming: bool
    timeout_s: float | None
    metadata: Mapping[str, Any] | None
    metadata_builder: MetadataBuilder | None
    payload_builder: PayloadBuilder | None
    cancel_on_cancel: bool
    chunk_channel: str
    execution_mode: ExecutionMode
    use_subscription: bool
    poll_interval_s: float
    max_poll_attempts: int
    remote_event_sink: RemoteEventSink | None

    async def run(self, args: BaseModel, ctx: ToolContext) -> object:
        """Call the remote skill with ``args``; return its result."""
        request = await self._request(args, ctx)
        task_transport = _task_transport(self.toolset.transport)
        execution = self.execution_mode
        if execution == "auto":
            execution = "stream" if self.streaming else "blocking"
        if execution == "task" and task_transport is not None:
            return await self._run_task(task_transport, request, ctx)
        if execution in {"blocking", "task"}:
            return await self._run_blocking(request, ctx)
        return await self._run_stream(request, ctx)

    async def _request(
        self, args: BaseModel, ctx: ToolContext
    ) -> RemoteCallRequest:
        """Build the remote call, continuing a bound conversation."""
        toolset = self.toolset
        payload = (
            self.payload_builder(args, ctx)
            if self.payload_builder is not None
            else args.model_dump(mode="json")
        )
        headers = Headers(tenant=_resolve_tenant(ctx))
        merged_metadata = _merge_metadata(
            base=toolset.default_metadata,
            ctx=ctx,
            include_tool_context_keys=toolset.include_tool_context_keys,
            override=self.metadata,
            builder=self.metadata_builder,
            args=args,
        )
        existing_binding = await _find_conversation_binding(
            ctx, agent_url=toolset.agent_url, skill=self.skill
        )
        existing_task_id = (
            existing_binding.task_id
            if existing_binding is not None
            and _should_continue_existing_task(existing_binding)
            else None
        )
        return RemoteCallRequest(
            message=Message(payload=payload, headers=headers),
            skill=self.skill,
            agent_url=toolset.agent_url,
            agent_card=toolset.agent_card,
            metadata=merged_metadata,
            timeout_s=self.timeout_s or toolset.default_timeout_s,
            context_id=existing_binding.context_id
            if existing_binding is not None
            else None,
            task_id=existing_task_id,
        )

    async def _save_task_binding(
        self,
        ctx: ToolContext,
        snapshot: RemoteTaskSnapshot,
        request: RemoteCallRequest,
        *,
        awaiting_flags: bool,
    ) -> None:
        """Remember the remote task for the conversation."""
        state = snapshot.status.state
        metadata: dict[str, Any] = {
            "remote_task_state": state.value,
            "remote_task_terminal": snapshot.is_terminal,
        }
        if awaiting_flags:
            metadata["awaiting_remote_input"] = (
                state is RemoteTaskState.INPUT_REQUIRED
            )
            metadata["awaiting_remote_auth"] = (
                state is RemoteTaskState.AUTH_REQUIRED
            )
        await _save_conversation_binding(
            ctx,
            agent_url=snapshot.agent_url or self.toolset.agent_url,
            skill=self.skill,
            context_id=snapshot.context_id or request.context_id,
            task_id=snapshot.task_id,
            is_terminal=False,
            metadata=metadata,
        )

    async def _pause_if_waiting(
        self, ctx: ToolContext, snapshot: RemoteTaskSnapshot
    ) -> object | None:
        """Pause when the remote task needs input or auth; else ``None``."""
        state = snapshot.status.state
        if state is RemoteTaskState.INPUT_REQUIRED:
            reason: Literal["await_input", "approval_required"] = "await_input"
        elif state is RemoteTaskState.AUTH_REQUIRED:
            reason = "approval_required"
        else:
            return None
        return await _pause_for_remote_state(
            ctx,
            snapshot=snapshot,
            skill=self.skill,
            agent_url=snapshot.agent_url or self.toolset.agent_url,
            reason=reason,
        )

    async def _run_task(
        self,
        transport: _TaskTransport,
        request: RemoteCallRequest,
        ctx: ToolContext,
    ) -> object:
        """Run the call as a remote task, waiting for it to finish."""
        snapshot = await transport.send_task(request, blocking=False)
        await _emit_remote_task_event(
            ctx,
            self.remote_event_sink,
            _event_from_snapshot(
                snapshot, kind="task", agent_url=self.toolset.agent_url
            ),
        )
        _raise_for_failed_task(snapshot)
        await self._save_task_binding(
            ctx, snapshot, request, awaiting_flags=True
        )
        paused = await self._pause_if_waiting(ctx, snapshot)
        if paused is not None:
            return paused
        if not snapshot.is_terminal:
            snapshot = await _wait_for_task_completion(
                transport=transport,
                ctx=ctx,
                agent_url=snapshot.agent_url or self.toolset.agent_url,
                task_id=snapshot.task_id,
                skill=self.skill,
                stream_id=f"a2a:{self.name}:{uuid.uuid4().hex}",
                chunk_channel=self.chunk_channel,
                use_subscription=self.use_subscription,
                poll_interval_s=self.poll_interval_s,
                max_poll_attempts=self.max_poll_attempts,
                remote_event_sink=self.remote_event_sink,
            )
            _raise_for_failed_task(snapshot)
            await self._save_task_binding(
                ctx, snapshot, request, awaiting_flags=False
            )
        paused = await self._pause_if_waiting(ctx, snapshot)
        if paused is not None:
            return paused
        return _coerce_text_result(snapshot.result, self.out_model)

    async def _run_blocking(
        self, request: RemoteCallRequest, ctx: ToolContext
    ) -> object:
        """Run the call and wait for its result."""
        try:
            result = await self.toolset.transport.send(request)
        except (RemoteTaskInputRequired, RemoteTaskAuthRequired) as exc:
            return await _handle_remote_pause_exception(
                ctx,
                exc=exc,
                skill=self.skill,
                agent_url=self.toolset.agent_url,
            )
        await _save_conversation_binding(
            ctx,
            agent_url=result.agent_url or self.toolset.agent_url,
            skill=self.skill,
            context_id=result.context_id or request.context_id,
            task_id=result.task_id,
            is_terminal=False,
            metadata=dict(result.meta or {}),
        )
        return _coerce_text_result(result.result, self.out_model)

    async def _run_stream(
        self, request: RemoteCallRequest, ctx: ToolContext
    ) -> object:
        """Run the call streaming its text as chunks."""
        stream = _RemoteStream(
            tool=self,
            ctx=ctx,
            stream_id=f"a2a:{self.name}:{uuid.uuid4().hex}",
            context_id=request.context_id,
            agent_url=self.toolset.agent_url,
        )
        try:
            async for event in self.toolset.transport.stream(request):
                await stream.handle(event)
                if event.result is not None:
                    await stream.save_binding()
                    return _coerce_text_result(event.result, self.out_model)
            await stream.save_binding()
        except (RemoteTaskInputRequired, RemoteTaskAuthRequired) as exc:
            return await _handle_remote_pause_exception(
                ctx,
                exc=exc,
                skill=self.skill,
                agent_url=stream.agent_url,
            )
        except asyncio.CancelledError:
            if self.cancel_on_cancel and stream.task_id is not None:
                # Cancellation is best-effort; preserve local cancellation.
                with contextlib.suppress(Exception):
                    await self.toolset.transport.cancel(
                        agent_url=self.toolset.agent_url,
                        task_id=stream.task_id,
                    )
            raise
        return None


@dataclass(slots=True)
class _RemoteStream:
    """Tracks a streamed remote call and forwards its text chunks."""

    tool: _RemoteTool
    ctx: ToolContext
    stream_id: str
    context_id: str | None
    agent_url: str
    task_id: str | None = None
    seq: int = 0

    async def handle(self, event: RemoteStreamEvent) -> None:
        """Track the event's task and emit its text as a chunk."""
        if event.task_id is not None:
            self.task_id = event.task_id
        if event.context_id is not None:
            self.context_id = event.context_id
        if event.agent_url is not None:
            self.agent_url = event.agent_url
        if event.text is None:
            return
        meta: dict[str, Any] = {
            "channel": self.tool.chunk_channel,
            "remote_agent_url": self.agent_url,
            "remote_task_id": self.task_id,
            "remote_context_id": self.context_id,
            "remote_skill": self.tool.skill,
        }
        if event.meta:
            meta.update(dict(event.meta))
        await self.ctx.emit_chunk(
            stream_id=self.stream_id,
            seq=self.seq,
            text=event.text,
            done=event.done,
            meta=meta,
        )
        self.seq += 1

    async def save_binding(self) -> None:
        """Remember the remote conversation for follow-up calls."""
        await _save_conversation_binding(
            self.ctx,
            agent_url=self.agent_url,
            skill=self.tool.skill,
            context_id=self.context_id,
            task_id=self.task_id,
            is_terminal=False,
        )


__all__ = ["A2AAgentToolset", "RemoteEventSink"]
