"""Task management service contracts used by planner-facing meta-tools.

This module intentionally separates:
- the planner-facing tool surface (tasks.* tools)
- the underlying session/task orchestration (StreamingSession)

Downstream products can provide custom TaskService implementations that map to
distributed queues, databases, or multi-tenant governance layers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .models import MergeStrategy, TaskStatus, TaskType
from .planner import PlannerFactory, PlannerTaskPipeline
from .session import SessionManager, TaskPipeline

ContextDepth = Literal["full", "summary", "none"]
SpawnMode = Literal["subagent", "job"]


class TaskSummary(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus
    task_type: TaskType
    priority: int
    description: str | None = None
    progress: dict[str, Any] | None = None
    error: str | None = None
    has_result: bool = False


class TaskDetails(TaskSummary):
    result_digest: list[str] = Field(default_factory=list)
    spawned_from_task_id: str | None = None
    spawned_from_event_id: str | None = None


class TaskSpawnResult(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus


class SpawnRequest(BaseModel):
    session_id: str
    parent_task_id: str | None = None
    mode: SpawnMode = "subagent"
    query: str | None = None
    tool_name: str | None = None
    priority: int = 0
    merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED


class SpawnDecision(BaseModel):
    allowed: bool = True
    reason: str | None = None
    estimated_cost: int | None = None


class SpawnGuard(Protocol):
    async def decide(self, request: SpawnRequest) -> SpawnDecision: ...


class NoOpSpawnGuard:
    async def decide(self, request: SpawnRequest) -> SpawnDecision:
        _ = request
        return SpawnDecision(allowed=True)


class TaskService(Protocol):
    """Background task orchestration service for tasks.* meta-tools."""

    async def spawn(
        self,
        *,
        session_id: str,
        query: str,
        parent_task_id: str | None = None,
        priority: int = 0,
        merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        context_depth: ContextDepth = "full",
        task_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TaskSpawnResult: ...

    async def list(
        self,
        *,
        session_id: str,
        status: TaskStatus | None = None,
    ) -> list[TaskSummary]: ...

    async def get(
        self,
        *,
        session_id: str,
        task_id: str,
        include_result: bool = False,
    ) -> TaskDetails | None: ...

    async def cancel(
        self,
        *,
        session_id: str,
        task_id: str,
        reason: str | None = None,
    ) -> bool: ...

    async def prioritize(
        self,
        *,
        session_id: str,
        task_id: str,
        priority: int,
    ) -> bool: ...

    async def apply_patch(
        self,
        *,
        session_id: str,
        patch_id: str,
        action: Literal["apply", "reject"],
        strategy: MergeStrategy | None = None,
    ) -> bool: ...

    async def spawn_tool_job(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: Any,
        parent_task_id: str | None = None,
        priority: int = 0,
        merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        task_id: str | None = None,
    ) -> TaskSpawnResult: ...


def _digest_from_result(result: Any) -> list[str]:
    if result is None:
        return []
    if isinstance(result, str):
        return [result[:5000]]
    if isinstance(result, dict):
        for key in ("raw_answer", "answer", "text", "content", "message"):
            value = result.get(key)
            if value is not None:
                return [str(value)[:5000]]
    return [str(result)[:5000]]


class InProcessTaskService:
    """Default TaskService implementation backed by an in-process SessionManager."""

    def __init__(
        self,
        *,
        sessions: SessionManager,
        planner_factory: PlannerFactory | None,
        subagent_planner_factory: PlannerFactory | None = None,
        tool_job_factory: Callable[[str, Any], TaskPipeline] | None = None,
        spawn_guard: SpawnGuard | None = None,
    ) -> None:
        self._sessions = sessions
        self._planner_factory = planner_factory
        self._subagent_factory = subagent_planner_factory or planner_factory
        self._tool_job_factory = tool_job_factory
        self._spawn_guard = spawn_guard or NoOpSpawnGuard()
        self._lock = asyncio.Lock()
        self._idempotency: dict[tuple[str, str], str] = {}

    async def spawn(
        self,
        *,
        session_id: str,
        query: str,
        parent_task_id: str | None = None,
        priority: int = 0,
        merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        context_depth: ContextDepth = "full",
        task_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> TaskSpawnResult:
        if self._subagent_factory is None:
            raise RuntimeError("background_tasks_unavailable")
        decision = await self._spawn_guard.decide(
            SpawnRequest(
                session_id=session_id,
                parent_task_id=parent_task_id,
                mode="subagent",
                query=query,
                priority=priority,
                merge_strategy=merge_strategy,
            )
        )
        if not decision.allowed:
            raise RuntimeError(decision.reason or "spawn_blocked")
        session = await self._sessions.get_or_create(session_id)
        if idempotency_key is not None:
            async with self._lock:
                existing = self._idempotency.get((session_id, idempotency_key))
            if existing is not None:
                task = await session.get_task(existing)
                if task is not None:
                    return TaskSpawnResult(task_id=existing, session_id=session_id, status=task.status)
        if task_id is not None:
            existing_task = await session.get_task(task_id)
            if existing_task is not None:
                return TaskSpawnResult(task_id=task_id, session_id=session_id, status=existing_task.status)

        snapshot = None
        if context_depth != "full":
            llm_context, tool_context = session.get_context()
            if context_depth == "none":
                llm_context = {}
            elif context_depth == "summary":
                llm_context = {
                    "summary": llm_context.get("summary")
                    or llm_context.get("conversation_summary")
                    or "",
                }
            from .models import TaskContextSnapshot

            snapshot = TaskContextSnapshot(
                session_id=session_id,
                task_id=task_id or "pending",
                spawned_from_task_id=parent_task_id or "foreground",
                spawn_reason="tasks.spawn",
                query=query,
                propagate_on_cancel=propagate_on_cancel,
                notify_on_complete=notify_on_complete,
                llm_context=dict(llm_context),
                tool_context=dict(tool_context),
                context_version=session.context_version,
                context_hash=session.context_hash,
            )

        pipeline = PlannerTaskPipeline(planner_factory=self._subagent_factory)
        created_id = await session.spawn_task(
            pipeline,
            task_type=TaskType.BACKGROUND,
            priority=priority,
            context_snapshot=snapshot,
            spawn_reason="tasks.spawn",
            description=query,
            query=query,
            parent_task_id=parent_task_id,
            task_id=task_id,
            merge_strategy=merge_strategy,
            propagate_on_cancel=propagate_on_cancel,
            notify_on_complete=notify_on_complete,
        )
        if idempotency_key is not None:
            async with self._lock:
                self._idempotency[(session_id, idempotency_key)] = created_id
        task = await session.get_task(created_id)
        status = task.status if task else TaskStatus.PENDING
        return TaskSpawnResult(
            task_id=created_id,
            session_id=session_id,
            status=status,
        )

    async def list(self, *, session_id: str, status: TaskStatus | None = None) -> list[TaskSummary]:
        session = await self._sessions.get_or_create(session_id)
        tasks = await session.list_tasks(status=status)
        summaries: list[TaskSummary] = []
        for task in tasks:
            summaries.append(
                TaskSummary(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    status=task.status,
                    task_type=task.task_type,
                    priority=task.priority,
                    description=task.description,
                    progress=task.progress,
                    error=task.error,
                    has_result=task.result is not None,
                )
            )
        return summaries

    async def get(
        self,
        *,
        session_id: str,
        task_id: str,
        include_result: bool = False,
    ) -> TaskDetails | None:
        session = await self._sessions.get_or_create(session_id)
        task = await session.get_task(task_id)
        if task is None:
            return None
        digest = _digest_from_result(task.result) if include_result else []
        snapshot = task.context_snapshot
        return TaskDetails(
            task_id=task.task_id,
            session_id=task.session_id,
            status=task.status,
            task_type=task.task_type,
            priority=task.priority,
            description=task.description,
            progress=task.progress,
            error=task.error,
            has_result=task.result is not None,
            result_digest=digest,
            spawned_from_task_id=snapshot.spawned_from_task_id if snapshot else None,
            spawned_from_event_id=snapshot.spawned_from_event_id if snapshot else None,
        )

    async def cancel(self, *, session_id: str, task_id: str, reason: str | None = None) -> bool:
        session = await self._sessions.get_or_create(session_id)
        return await session.cancel_task(task_id, reason=reason)

    async def prioritize(self, *, session_id: str, task_id: str, priority: int) -> bool:
        session = await self._sessions.get_or_create(session_id)
        from penguiflow.steering import SteeringEvent, SteeringEventType

        return await session.steer(
            SteeringEvent(
                session_id=session_id,
                task_id=task_id,
                event_type=SteeringEventType.PRIORITIZE,
                payload={"priority": priority},
                source="user",
            )
        )

    async def apply_patch(
        self,
        *,
        session_id: str,
        patch_id: str,
        action: Literal["apply", "reject"],
        strategy: MergeStrategy | None = None,
    ) -> bool:
        session = await self._sessions.get_or_create(session_id)
        if action == "reject":
            from penguiflow.steering import SteeringEvent, SteeringEventType

            await session.steer(
                SteeringEvent(
                    session_id=session_id,
                    task_id="context_patch",
                    event_type=SteeringEventType.REJECT,
                    payload={"patch_id": patch_id},
                    source="user",
                )
            )
            return True
        return await session.apply_pending_patch(patch_id=patch_id, strategy=strategy)

    async def spawn_tool_job(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: Any,
        parent_task_id: str | None = None,
        priority: int = 0,
        merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        task_id: str | None = None,
    ) -> TaskSpawnResult:
        if self._tool_job_factory is None:
            raise RuntimeError("tool_background_unavailable")
        decision = await self._spawn_guard.decide(
            SpawnRequest(
                session_id=session_id,
                parent_task_id=parent_task_id,
                mode="job",
                tool_name=tool_name,
                priority=priority,
                merge_strategy=merge_strategy,
            )
        )
        if not decision.allowed:
            raise RuntimeError(decision.reason or "spawn_blocked")
        session = await self._sessions.get_or_create(session_id)
        pipeline = self._tool_job_factory(tool_name, tool_args)
        created_id = await session.spawn_task(
            pipeline,
            task_type=TaskType.BACKGROUND,
            priority=priority,
            spawn_reason=f"tool:{tool_name}",
            description=f"{tool_name} (background)",
            query=None,
            parent_task_id=parent_task_id,
            task_id=task_id,
            merge_strategy=merge_strategy,
            propagate_on_cancel=propagate_on_cancel,
            notify_on_complete=notify_on_complete,
        )
        task = await session.get_task(created_id)
        status = task.status if task else TaskStatus.PENDING
        return TaskSpawnResult(
            task_id=created_id,
            session_id=session_id,
            status=status,
        )


__all__ = [
    "ContextDepth",
    "InProcessTaskService",
    "NoOpSpawnGuard",
    "SpawnMode",
    "SpawnDecision",
    "SpawnGuard",
    "SpawnRequest",
    "TaskDetails",
    "TaskService",
    "TaskSpawnResult",
    "TaskSummary",
]
