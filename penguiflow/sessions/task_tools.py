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

from .models import GroupReportStrategy, MergeStrategy, TaskGroup, TaskStatus
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
    """Arguments for spawning a background task, optionally into a task group.

    Task Group Support:
    - `group`: Display name for the group. Turn-scoped resolution:
      joins an OPEN group with this name created earlier in the same foreground turn,
      otherwise creates a new group.
    - `group_id`: Optional stable group identifier. If provided, joins that exact group.
    - `group_sealed`: If True, seals the group after this spawn.
    - `retain_turn`: If True, foreground agent waits for task/group instead of yielding.
    - `group_merge_strategy`: Merge strategy for the group (only on creation).
    - `group_report`: When to generate proactive report (only on creation).
    """

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

    # Task Group Support
    group: str | None = Field(
        default=None,
        description="Group display name. Turn-scoped: joins OPEN group with this name "
        "created earlier in same turn, or creates new group.",
    )
    group_id: str | None = Field(
        default=None,
        description="Stable group identifier. If provided, joins that exact group.",
    )
    group_sealed: bool = Field(
        default=False,
        description="If True, seal group after this spawn (no more tasks can join).",
    )
    retain_turn: bool = Field(
        default=False,
        description="If True, foreground agent waits for task/group instead of yielding. "
        "Requires group_merge_strategy in {APPEND, REPLACE}, not HUMAN_GATED.",
    )
    group_merge_strategy: MergeStrategy | None = Field(
        default=None,
        description="Merge strategy for the group. Uses config default if not specified. "
        "Only applies on group creation.",
    )
    group_report: GroupReportStrategy | None = Field(
        default=None,
        description="When to generate proactive report: 'all' (default for groups), "
        "'any' (each task), 'none' (agent polls). Only applies on group creation.",
    )

    @model_validator(mode="after")
    def _validate_mode(self) -> TasksSpawnArgs:
        if self.mode == "subagent":
            if not isinstance(self.query, str) or not self.query.strip():
                raise ValueError("query is required for subagent mode")
        else:
            if not self.tool_name or not isinstance(self.tool_name, str):
                raise ValueError("tool_name is required for job mode")
            if not isinstance(self.tool_args, dict):
                raise ValueError("tool_args must be an object for job mode")
        # Validate retain_turn constraints
        if self.retain_turn:
            effective_strategy = self.group_merge_strategy or self.merge_strategy
            if effective_strategy == MergeStrategy.HUMAN_GATED:
                raise ValueError(
                    "retain_turn=True requires group_merge_strategy in {APPEND, REPLACE}, "
                    "not HUMAN_GATED (results must auto-merge for inline injection)"
                )
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


# Task Group Tools


class TasksSealGroupArgs(BaseModel):
    """Arguments for sealing a task group."""

    group_id: str | None = Field(
        default=None,
        description="Stable group identifier. If provided, seals that exact group.",
    )
    group: str | None = Field(
        default=None,
        description="Group display name. Resolves to current OPEN group with this name.",
    )


class TasksSealGroupResult(BaseModel):
    ok: bool
    group_id: str | None = None
    sealed_task_count: int = 0


class TasksCancelGroupArgs(BaseModel):
    """Arguments for cancelling a task group."""

    group_id: str
    reason: str | None = None
    propagate_on_cancel: Literal["cascade", "isolate"] = "cascade"


class TasksCancelGroupResult(BaseModel):
    ok: bool
    cancelled_task_count: int = 0


class TasksApplyGroupArgs(BaseModel):
    """Arguments for applying or rejecting all patches from a task group."""

    group_id: str
    action: Literal["apply", "reject"] = "apply"
    strategy: MergeStrategy | None = None


class TasksApplyGroupResult(BaseModel):
    ok: bool
    action: Literal["apply", "reject"]
    applied_patch_count: int = 0


class TasksListGroupsArgs(BaseModel):
    """Arguments for listing task groups."""

    status: Literal["open", "sealed", "complete", "failed"] | None = None


class TasksListGroupsResult(BaseModel):
    groups: list[TaskGroup] = Field(default_factory=list)


class TasksGetGroupArgs(BaseModel):
    """Arguments for getting a specific task group."""

    group_id: str | None = None
    group: str | None = None


@tool(
    desc="Spawn a background subagent for long-running work. Returns immediately with a task_id. "
    "Use group parameter to coordinate multiple related tasks for cohesive reporting.",
    tags=["tasks", "background"],
    side_effects="stateful",
)
async def tasks_spawn(args: TasksSpawnArgs, ctx: ToolContext) -> TaskSpawnResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    parent_task_id = ctx.tool_context.get("task_id")
    turn_id = ctx.tool_context.get("turn_id")
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
            # Group parameters
            group=args.group,
            group_id=args.group_id,
            group_sealed=args.group_sealed,
            retain_turn=args.retain_turn,
            group_merge_strategy=args.group_merge_strategy,
            group_report=args.group_report,
            turn_id=str(turn_id) if turn_id else None,
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
        # Group parameters
        group=args.group,
        group_id=args.group_id,
        group_sealed=args.group_sealed,
        retain_turn=args.retain_turn,
        group_merge_strategy=args.group_merge_strategy,
        group_report=args.group_report,
        turn_id=str(turn_id) if turn_id else None,
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


# ─────────────────────────────────────────────────────────────
# Task Group Tools
# ─────────────────────────────────────────────────────────────


@tool(
    desc="Seal a task group (no more tasks can join). Use when done adding tasks to a group.",
    tags=["tasks", "background", "groups"],
    side_effects="stateful",
)
async def tasks_seal_group(args: TasksSealGroupArgs, ctx: ToolContext) -> TasksSealGroupResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    turn_id = ctx.tool_context.get("turn_id")
    if not session_id:
        raise RuntimeError("session_id_missing")
    if not args.group_id and not args.group:
        raise RuntimeError("group_id or group name required")
    result = await service.seal_group(
        session_id=session_id,
        group_id=args.group_id,
        group_name=args.group,
        turn_id=str(turn_id) if turn_id else None,
    )
    return TasksSealGroupResult(
        ok=result.get("ok", False),
        group_id=result.get("group_id"),
        sealed_task_count=result.get("sealed_task_count", 0),
    )


@tool(
    desc="Cancel a task group by group_id, terminating all tasks in the group.",
    tags=["tasks", "background", "groups"],
    side_effects="stateful",
)
async def tasks_cancel_group(args: TasksCancelGroupArgs, ctx: ToolContext) -> TasksCancelGroupResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    result = await service.cancel_group(
        session_id=session_id,
        group_id=args.group_id,
        reason=args.reason,
        propagate_on_cancel=args.propagate_on_cancel,
    )
    return TasksCancelGroupResult(
        ok=result.get("ok", False),
        cancelled_task_count=result.get("cancelled_task_count", 0),
    )


@tool(
    desc="Apply or reject all pending patches for a task group (bundled approval for HUMAN_GATED).",
    tags=["tasks", "background", "groups"],
    side_effects="stateful",
)
async def tasks_apply_group(args: TasksApplyGroupArgs, ctx: ToolContext) -> TasksApplyGroupResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    result = await service.apply_group(
        session_id=session_id,
        group_id=args.group_id,
        action=args.action,
        strategy=args.strategy,
    )
    return TasksApplyGroupResult(
        ok=result.get("ok", False),
        action=args.action,
        applied_patch_count=result.get("applied_patch_count", 0),
    )


@tool(
    desc="List task groups in the current session.",
    tags=["tasks", "background", "groups"],
    side_effects="read",
)
async def tasks_list_groups(args: TasksListGroupsArgs, ctx: ToolContext) -> TasksListGroupsResult:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    if not session_id:
        raise RuntimeError("session_id_missing")
    groups = await service.list_groups(session_id=session_id, status=args.status)
    return TasksListGroupsResult(groups=groups)


@tool(
    desc="Get a specific task group by group_id or group name.",
    tags=["tasks", "background", "groups"],
    side_effects="read",
)
async def tasks_get_group(args: TasksGetGroupArgs, ctx: ToolContext) -> TaskGroup:
    _ensure_foreground(ctx)
    service = _get_service(ctx)
    session_id = str(ctx.tool_context.get("session_id") or "")
    turn_id = ctx.tool_context.get("turn_id")
    if not session_id:
        raise RuntimeError("session_id_missing")
    if not args.group_id and not args.group:
        raise RuntimeError("group_id or group name required")
    group = await service.get_group(
        session_id=session_id,
        group_id=args.group_id,
        group_name=args.group,
        turn_id=str(turn_id) if turn_id else None,
    )
    if group is None:
        raise RuntimeError("group_not_found")
    return group


def build_task_tool_specs() -> list[NodeSpec]:
    """Return NodeSpec entries for tasks.* tools."""
    registry = ModelRegistry()
    # Task tools
    registry.register("tasks.spawn", TasksSpawnArgs, TaskSpawnResult)
    registry.register("tasks.list", TasksListArgs, TasksListResult)
    registry.register("tasks.get", TasksGetArgs, TaskDetails)
    registry.register("tasks.cancel", TasksCancelArgs, TasksCancelResult)
    registry.register("tasks.prioritize", TasksPrioritizeArgs, TasksPrioritizeResult)
    registry.register("tasks.apply_patch", TasksApplyPatchArgs, TasksApplyPatchResult)
    # Group tools
    registry.register("tasks.seal_group", TasksSealGroupArgs, TasksSealGroupResult)
    registry.register("tasks.cancel_group", TasksCancelGroupArgs, TasksCancelGroupResult)
    registry.register("tasks.apply_group", TasksApplyGroupArgs, TasksApplyGroupResult)
    registry.register("tasks.list_groups", TasksListGroupsArgs, TasksListGroupsResult)
    registry.register("tasks.get_group", TasksGetGroupArgs, TaskGroup)
    nodes: Sequence[Node] = [
        # Task tools
        Node(tasks_spawn, name="tasks.spawn"),
        Node(tasks_list, name="tasks.list"),
        Node(tasks_get, name="tasks.get"),
        Node(tasks_cancel, name="tasks.cancel"),
        Node(tasks_prioritize, name="tasks.prioritize"),
        Node(tasks_apply_patch, name="tasks.apply_patch"),
        # Group tools
        Node(tasks_seal_group, name="tasks.seal_group"),
        Node(tasks_cancel_group, name="tasks.cancel_group"),
        Node(tasks_apply_group, name="tasks.apply_group"),
        Node(tasks_list_groups, name="tasks.list_groups"),
        Node(tasks_get_group, name="tasks.get_group"),
    ]
    return build_catalog(nodes, registry)


__all__ = [
    "SUBAGENT_FLAG_KEY",
    "TASK_SERVICE_KEY",
    "TasksSpawnArgs",
    "build_task_tool_specs",
]
