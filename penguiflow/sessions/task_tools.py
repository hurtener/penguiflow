"""Planner tool nodes for background task management (tasks.*).

These tools are intended to be exposed only to the foreground agent.
Subagents should not receive these tools in their catalog.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from penguiflow.catalog import NodeSpec, build_catalog, tool
from penguiflow.node import Node
from penguiflow.planner import ToolContext
from penguiflow.registry import ModelRegistry

from .models import MergeStrategy, TaskStatus
from .task_service import ContextDepth, TaskDetails, TaskService, TaskSpawnResult, TaskSummary

TASK_SERVICE_KEY = "task_service"
SUBAGENT_FLAG_KEY = "is_subagent"


def _get_service(ctx: ToolContext) -> TaskService:
    service = ctx.tool_context.get(TASK_SERVICE_KEY)
    if service is None:
        raise RuntimeError("task_service_unavailable")
    return cast(TaskService, service)


def _ensure_foreground(ctx: ToolContext) -> None:
    if bool(ctx.tool_context.get(SUBAGENT_FLAG_KEY)):
        raise RuntimeError("subagent_task_management_disabled")


class TasksSpawnArgs(BaseModel):
    query: str | None = Field(default=None, min_length=1)
    mode: Literal["subagent", "job"] = "subagent"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    priority: int = 0
    merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"
    notify_on_complete: bool = True
    context_depth: ContextDepth = "full"
    task_id: str | None = None
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _validate_mode(self) -> TasksSpawnArgs:
        if self.mode == "subagent":
            if not isinstance(self.query, str) or not self.query.strip():
                raise ValueError("query is required for subagent mode")
            return self
        if not self.tool_name or not isinstance(self.tool_name, str):
            raise ValueError("tool_name is required for job mode")
        if not isinstance(self.tool_args, dict):
            raise ValueError("tool_args must be an object for job mode")
        return self


class TasksListArgs(BaseModel):
    status: TaskStatus | None = None


class TasksListResult(BaseModel):
    tasks: list[TaskSummary] = Field(default_factory=list)


class TasksGetArgs(BaseModel):
    task_id: str
    include_result: bool = False


class TasksCancelArgs(BaseModel):
    task_id: str
    reason: str | None = None


class TasksCancelResult(BaseModel):
    ok: bool


class TasksPrioritizeArgs(BaseModel):
    task_id: str
    priority: int


class TasksPrioritizeResult(BaseModel):
    ok: bool


class TasksApplyPatchArgs(BaseModel):
    patch_id: str
    action: Literal["apply", "reject"] = "apply"
    strategy: MergeStrategy | None = None


class TasksApplyPatchResult(BaseModel):
    ok: bool
    action: Literal["apply", "reject"]


@tool(
    desc="Spawn a background subagent for long-running work. Returns immediately with a task_id.",
    tags=["tasks", "background"],
    side_effects="stateful",
)
async def tasks_spawn(args: TasksSpawnArgs, ctx: ToolContext) -> TaskSpawnResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    parent_task_id = ctx.tool_context.get("task_id")
    if not session_id:
        raise RuntimeError("session_id_missing")
    if parent_task_id is not None and not isinstance(parent_task_id, str):
        parent_task_id = None
    if args.mode == "job":
        return await service.spawn_tool_job(
            session_id=session_id,
            tool_name=str(args.tool_name),
            tool_args=dict(args.tool_args or {}),
            parent_task_id=parent_task_id,
            priority=args.priority,
            merge_strategy=args.merge_strategy,
            propagate_on_cancel=args.propagate_on_cancel,
            notify_on_complete=args.notify_on_complete,
            task_id=args.task_id,
        )
    if not isinstance(args.query, str):
        raise RuntimeError("query_missing")
    return await service.spawn(
        session_id=session_id,
        query=args.query,
        parent_task_id=parent_task_id,
        priority=args.priority,
        merge_strategy=args.merge_strategy,
        propagate_on_cancel=args.propagate_on_cancel,
        notify_on_complete=args.notify_on_complete,
        context_depth=args.context_depth,
        task_id=args.task_id,
        idempotency_key=args.idempotency_key,
    )


@tool(desc="List tasks in the current session.", tags=["tasks", "background"], side_effects="read")
async def tasks_list(args: TasksListArgs, ctx: ToolContext) -> TasksListResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    tasks = await service.list(session_id=session_id, status=args.status)
    return TasksListResult(tasks=tasks)


@tool(desc="Get status/digest for a task by task_id.", tags=["tasks", "background"], side_effects="read")
async def tasks_get(args: TasksGetArgs, ctx: ToolContext) -> TaskDetails:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    task = await service.get(session_id=session_id, task_id=args.task_id, include_result=args.include_result)
    if task is None:
        raise RuntimeError("task_not_found")
    return task


@tool(desc="Cancel a task by task_id.", tags=["tasks", "background"], side_effects="stateful")
async def tasks_cancel(args: TasksCancelArgs, ctx: ToolContext) -> TasksCancelResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    ok = await service.cancel(session_id=session_id, task_id=args.task_id, reason=args.reason)
    return TasksCancelResult(ok=ok)


@tool(desc="Change a task priority.", tags=["tasks", "background"], side_effects="stateful")
async def tasks_prioritize(args: TasksPrioritizeArgs, ctx: ToolContext) -> TasksPrioritizeResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    ok = await service.prioritize(session_id=session_id, task_id=args.task_id, priority=args.priority)
    return TasksPrioritizeResult(ok=ok)


@tool(desc="Apply or reject a pending background context patch.", tags=["tasks", "background"], side_effects="stateful")
async def tasks_apply_patch(args: TasksApplyPatchArgs, ctx: ToolContext) -> TasksApplyPatchResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    ok = await service.apply_patch(
        session_id=session_id,
        patch_id=args.patch_id,
        action=args.action,
        strategy=args.strategy,
    )
    return TasksApplyPatchResult(ok=ok, action=args.action)


def build_task_tool_specs() -> list[NodeSpec]:
    """Return NodeSpec entries for tasks.* tools."""
    registry = ModelRegistry()
    registry.register("tasks.spawn", TasksSpawnArgs, TaskSpawnResult)
    registry.register("tasks.list", TasksListArgs, TasksListResult)
    registry.register("tasks.get", TasksGetArgs, TaskDetails)
    registry.register("tasks.cancel", TasksCancelArgs, TasksCancelResult)
    registry.register("tasks.prioritize", TasksPrioritizeArgs, TasksPrioritizeResult)
    registry.register("tasks.apply_patch", TasksApplyPatchArgs, TasksApplyPatchResult)
    nodes: Sequence[Node] = [
        Node(tasks_spawn, name="tasks.spawn"),
        Node(tasks_list, name="tasks.list"),
        Node(tasks_get, name="tasks.get"),
        Node(tasks_cancel, name="tasks.cancel"),
        Node(tasks_prioritize, name="tasks.prioritize"),
        Node(tasks_apply_patch, name="tasks.apply_patch"),
    ]
    return build_catalog(nodes, registry)


__all__ = [
    "SUBAGENT_FLAG_KEY",
    "TASK_SERVICE_KEY",
    "build_task_tool_specs",
]
