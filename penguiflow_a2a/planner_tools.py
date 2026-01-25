"""Planner tool helpers for calling remote A2A agents.

The flow runtime supports agent-to-agent calls via :func:`penguiflow.remote.RemoteNode`.
ReactPlanner tool execution uses :class:`penguiflow.planner.context.ToolContext`, so it
cannot call :func:`penguiflow.remote.RemoteNode` directly.

This module provides a small wrapper that turns an A2A remote invocation into a regular
planner tool (a :class:`penguiflow.catalog.NodeSpec`).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from penguiflow.catalog import NodeSpec, SideEffect
from penguiflow.node import Node, NodePolicy
from penguiflow.planner.context import ToolContext
from penguiflow.remote import RemoteCallRequest, RemoteTransport
from penguiflow.types import Headers, Message

ArgsModelT = TypeVar("ArgsModelT", bound=BaseModel)

PayloadBuilder = Callable[[BaseModel, ToolContext], Any]
MetadataBuilder = Callable[[BaseModel, ToolContext], Mapping[str, Any] | None]


def _resolve_tenant(ctx: ToolContext) -> str:
    raw = ctx.tool_context.get("tenant")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "default"


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

            request = RemoteCallRequest(
                message=message,
                skill=skill,
                agent_url=self.agent_url,
                agent_card=self.agent_card,
                metadata=merged_metadata,
                timeout_s=timeout_s or self.default_timeout_s,
            )

            if not streaming:
                result = await self.transport.send(request)
                return result.result

            stream_id = f"a2a:{name}:{uuid.uuid4().hex}"
            seq = 0
            remote_task_id: str | None = None
            try:
                async for event in self.transport.stream(request):
                    if event.task_id is not None:
                        remote_task_id = event.task_id

                    if event.text is not None:
                        meta: dict[str, Any] = {
                            "channel": chunk_channel,
                            "remote_agent_url": event.agent_url or self.agent_url,
                            "remote_task_id": remote_task_id,
                            "remote_context_id": event.context_id,
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
                        return event.result
                return None
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
