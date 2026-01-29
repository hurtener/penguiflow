"""Task management service contracts used by planner-facing meta-tools.

This module intentionally separates:
- the planner-facing tool surface (tasks.* tools)
- the underlying session/task orchestration (StreamingSession)

Downstream products can provide custom TaskService implementations that map to
distributed queues, databases, or multi-tenant governance layers.
"""

from __future__ import annotations

import asyncio
import builtins
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from penguiflow.planner.models import BackgroundTasksConfig

from .models import GroupReportStrategy, GroupStatus, MergeStrategy, TaskGroup, TaskStatus, TaskType
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


class GroupCompletionResult(BaseModel):
    """Result of a completed group when using retain_turn."""

    group_id: str
    group_name: str
    status: GroupStatus
    task_count: int
    completed_task_ids: list[str]
    failed_task_ids: list[str]
    results: list[dict[str, Any]] = Field(default_factory=list)
    """Aggregated results/digests from completed tasks."""
    timed_out: bool = False
    """True if the wait timed out and foreground was force-yielded."""


class TaskSpawnResult(BaseModel):
    task_id: str
    session_id: str
    status: TaskStatus
    group_id: str | None = None
    group: str | None = None
    retained: bool = False
    """True if the foreground was retained and waited for completion."""
    group_completion: GroupCompletionResult | None = None
    """Populated when retain_turn=True and group completed (or timed out)."""


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
        context_tool_context: dict[str, Any] | None = None,
        # Group parameters
        group: str | None = None,
        group_id: str | None = None,
        group_sealed: bool = False,
        retain_turn: bool = False,
        group_merge_strategy: MergeStrategy | None = None,
        group_report: GroupReportStrategy | None = None,
        turn_id: str | None = None,
    ) -> TaskSpawnResult: ...

    async def list(
        self,
        *,
        session_id: str,
        status: TaskStatus | None = None,
    ) -> builtins.list[TaskSummary]: ...

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

    async def acknowledge_background(
        self,
        *,
        session_id: str,
        task_ids: builtins.list[str],
    ) -> int: ...

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
        context_tool_context: dict[str, Any] | None = None,
        # Group parameters
        group: str | None = None,
        group_id: str | None = None,
        group_sealed: bool = False,
        retain_turn: bool = False,
        group_merge_strategy: MergeStrategy | None = None,
        group_report: GroupReportStrategy | None = None,
        turn_id: str | None = None,
    ) -> TaskSpawnResult: ...

    # Task Group Methods

    async def seal_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def cancel_group(
        self,
        *,
        session_id: str,
        group_id: str,
        reason: str | None = None,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
    ) -> dict[str, Any]: ...

    async def apply_group(
        self,
        *,
        session_id: str,
        group_id: str,
        action: Literal["apply", "reject"] = "apply",
        strategy: MergeStrategy | None = None,
    ) -> dict[str, Any]: ...

    async def list_groups(
        self,
        *,
        session_id: str,
        status: GroupStatus | None = None,
    ) -> builtins.list[TaskGroup]: ...

    async def get_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> TaskGroup | None: ...


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
        background_config: BackgroundTasksConfig | None = None,
    ) -> None:
        self._sessions = sessions
        self._planner_factory = planner_factory
        self._subagent_factory = subagent_planner_factory or planner_factory
        self._tool_job_factory = tool_job_factory
        self._spawn_guard = spawn_guard or NoOpSpawnGuard()
        self._background_config = background_config
        self._lock = asyncio.Lock()
        self._idempotency: dict[tuple[str, str], str] = {}

    def _apply_background_config(self, session: Any) -> None:
        if self._background_config is None:
            return
        if hasattr(session, "configure_background_tasks"):
            session.configure_background_tasks(self._background_config)

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
        context_tool_context: dict[str, Any] | None = None,
        # Group parameters
        group: str | None = None,
        group_id: str | None = None,
        group_sealed: bool = False,
        retain_turn: bool = False,
        group_merge_strategy: MergeStrategy | None = None,
        group_report: GroupReportStrategy | None = None,
        turn_id: str | None = None,
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
        self._apply_background_config(session)
        self._apply_background_config(session)
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
        if context_depth != "full" or context_tool_context:
            llm_context, tool_context = session.get_context()
            if context_depth == "none":
                llm_context = {}
            elif context_depth == "summary":
                llm_context = {
                    "summary": llm_context.get("summary") or llm_context.get("conversation_summary") or "",
                }
            tool_context = dict(tool_context)
            if context_tool_context:
                tool_context.update(context_tool_context)
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
                tool_context=tool_context,
                context_version=session.context_version,
                context_hash=session.context_hash,
            )

        # Resolve/create task group if group parameters provided
        resolved_group: TaskGroup | None = None
        if group or group_id:
            resolved_group = await session.resolve_or_create_group(
                group_name=group,
                group_id=group_id,
                turn_id=turn_id,
                merge_strategy=group_merge_strategy,
                report_strategy=group_report,
                retain_turn=retain_turn,
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
            group_id=resolved_group.group_id if resolved_group else None,
        )

        # Add task to group and optionally seal
        if resolved_group:
            await session.add_task_to_group(resolved_group.group_id, created_id)
            if group_sealed:
                await session.seal_group(resolved_group.group_id)
            if retain_turn and resolved_group.status == "open":
                await session.seal_group(resolved_group.group_id)
            resolved_group = await session.get_group(group_id=resolved_group.group_id) or resolved_group
            if retain_turn and resolved_group.status == "open":
                await session.seal_group(resolved_group.group_id)
            resolved_group = await session.get_group(group_id=resolved_group.group_id) or resolved_group

        if idempotency_key is not None:
            async with self._lock:
                self._idempotency[(session_id, idempotency_key)] = created_id

        # Handle retained turn - wait for group completion if group is sealed
        group_completion: GroupCompletionResult | None = None
        retained = False
        if retain_turn and resolved_group and resolved_group.status == "sealed":
            # Validate retain_turn constraints (no HUMAN_GATED)
            if resolved_group.merge_strategy == MergeStrategy.HUMAN_GATED:
                # Cannot retain turn with HUMAN_GATED - results need approval
                pass  # Fall through to normal return
            else:
                retained = True
                config = session.get_background_task_config()
                timeout_s_raw = float(config.retain_turn_timeout_s)
                timeout_s: float | None = timeout_s_raw if timeout_s_raw > 0 else None
                completed_group, timed_out = await session.wait_for_group_completion(
                    resolved_group.group_id,
                    timeout_s=timeout_s,
                )
                if completed_group is not None:
                    results = await session.get_group_results(resolved_group.group_id)
                    group_completion = GroupCompletionResult(
                        group_id=completed_group.group_id,
                        group_name=completed_group.name,
                        status=completed_group.status,
                        task_count=len(completed_group.task_ids),
                        completed_task_ids=list(completed_group.completed_task_ids),
                        failed_task_ids=list(completed_group.failed_task_ids),
                        results=results,
                        timed_out=timed_out,
                    )
                if completed_group is not None and not timed_out:
                    task_ids = list(completed_group.task_ids)
                    session.suppress_proactive_reports(task_ids=task_ids, group_id=completed_group.group_id)
                    await session.mark_background_consumed(task_ids=task_ids)
                elif timed_out:
                    config = session.get_background_task_config()
                    continuation_hops = int(config.background_continuation_max_hops or 0)
                    cooldown_s = float(config.background_continuation_cooldown_s or 0.0)
                    session.schedule_group_continuation(
                        group_id=resolved_group.group_id,
                        timeout_s=timeout_s,
                        max_hops=continuation_hops,
                        cooldown_s=cooldown_s,
                    )

        task = await session.get_task(created_id)
        status = task.status if task else TaskStatus.PENDING
        return TaskSpawnResult(
            task_id=created_id,
            session_id=session_id,
            status=status,
            group_id=resolved_group.group_id if resolved_group else None,
            group=resolved_group.name if resolved_group else None,
            retained=retained,
            group_completion=group_completion,
        )

    async def list(self, *, session_id: str, status: TaskStatus | None = None) -> builtins.list[TaskSummary]:
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

    async def acknowledge_background(self, *, session_id: str, task_ids: builtins.list[str]) -> int:
        session = await self._sessions.get_or_create(session_id)
        return await session.mark_background_consumed(task_ids=task_ids)

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
        context_tool_context: dict[str, Any] | None = None,
        # Group parameters
        group: str | None = None,
        group_id: str | None = None,
        group_sealed: bool = False,
        retain_turn: bool = False,
        group_merge_strategy: MergeStrategy | None = None,
        group_report: GroupReportStrategy | None = None,
        turn_id: str | None = None,
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
        self._apply_background_config(session)

        # Resolve/create task group if group parameters provided
        resolved_group: TaskGroup | None = None
        if group or group_id:
            resolved_group = await session.resolve_or_create_group(
                group_name=group,
                group_id=group_id,
                turn_id=turn_id,
                merge_strategy=group_merge_strategy,
                report_strategy=group_report,
                retain_turn=retain_turn,
            )

        snapshot = None
        if context_tool_context:
            llm_context, tool_context = session.get_context()
            tool_context = dict(tool_context)
            tool_context.update(context_tool_context)
            from .models import TaskContextSnapshot

            snapshot = TaskContextSnapshot(
                session_id=session_id,
                task_id=task_id or "pending",
                spawned_from_task_id=parent_task_id or "foreground",
                spawn_reason=f"tool:{tool_name}",
                query=None,
                propagate_on_cancel=propagate_on_cancel,
                notify_on_complete=notify_on_complete,
                llm_context=dict(llm_context),
                tool_context=tool_context,
                context_version=session.context_version,
                context_hash=session.context_hash,
            )
        pipeline = self._tool_job_factory(tool_name, tool_args)
        created_id = await session.spawn_task(
            pipeline,
            task_type=TaskType.BACKGROUND,
            priority=priority,
            context_snapshot=snapshot,
            spawn_reason=f"tool:{tool_name}",
            description=f"{tool_name} (background)",
            query=None,
            parent_task_id=parent_task_id,
            task_id=task_id,
            merge_strategy=merge_strategy,
            propagate_on_cancel=propagate_on_cancel,
            notify_on_complete=notify_on_complete,
            group_id=resolved_group.group_id if resolved_group else None,
        )

        # Add task to group and optionally seal
        if resolved_group:
            await session.add_task_to_group(resolved_group.group_id, created_id)
            if group_sealed:
                await session.seal_group(resolved_group.group_id)

        # Handle retained turn - wait for group completion if group is sealed
        group_completion: GroupCompletionResult | None = None
        retained = False
        if retain_turn and resolved_group and resolved_group.status == "sealed":
            # Validate retain_turn constraints (no HUMAN_GATED)
            if resolved_group.merge_strategy == MergeStrategy.HUMAN_GATED:
                # Cannot retain turn with HUMAN_GATED - results need approval
                pass  # Fall through to normal return
            else:
                retained = True
                config = session.get_background_task_config()
                timeout_s_raw = float(config.retain_turn_timeout_s)
                timeout_s: float | None = timeout_s_raw if timeout_s_raw > 0 else None
                completed_group, timed_out = await session.wait_for_group_completion(
                    resolved_group.group_id,
                    timeout_s=timeout_s,
                )
                if completed_group is not None:
                    results = await session.get_group_results(resolved_group.group_id)
                    group_completion = GroupCompletionResult(
                        group_id=completed_group.group_id,
                        group_name=completed_group.name,
                        status=completed_group.status,
                        task_count=len(completed_group.task_ids),
                        completed_task_ids=list(completed_group.completed_task_ids),
                        failed_task_ids=list(completed_group.failed_task_ids),
                        results=results,
                        timed_out=timed_out,
                    )
                if completed_group is not None and not timed_out:
                    task_ids = list(completed_group.task_ids)
                    session.suppress_proactive_reports(task_ids=task_ids, group_id=completed_group.group_id)
                    await session.mark_background_consumed(task_ids=task_ids)
                elif timed_out:
                    config = session.get_background_task_config()
                    continuation_hops = int(config.background_continuation_max_hops or 0)
                    cooldown_s = float(config.background_continuation_cooldown_s or 0.0)
                    session.schedule_group_continuation(
                        group_id=resolved_group.group_id,
                        timeout_s=timeout_s,
                        max_hops=continuation_hops,
                        cooldown_s=cooldown_s,
                    )

        task = await session.get_task(created_id)
        status = task.status if task else TaskStatus.PENDING
        return TaskSpawnResult(
            task_id=created_id,
            session_id=session_id,
            status=status,
            group_id=resolved_group.group_id if resolved_group else None,
            group=resolved_group.name if resolved_group else None,
            retained=retained,
            group_completion=group_completion,
        )

    # Task Group Methods

    async def seal_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        session = await self._sessions.get_or_create(session_id)
        group = await session.get_group(group_id=group_id, group_name=group_name, turn_id=turn_id)
        if group is None:
            return {"ok": False, "error": "group_not_found"}
        if group.status != "open":
            return {
                "ok": False,
                "error": "group_not_open",
                "status": group.status,
            }
        await session.seal_group(group.group_id)
        updated = await session.get_group(group_id=group.group_id)
        return {
            "ok": True,
            "group_id": group.group_id,
            "status": updated.status if updated else "sealed",
            "sealed_task_count": len(group.task_ids),
        }

    async def cancel_group(
        self,
        *,
        session_id: str,
        group_id: str,
        reason: str | None = None,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
    ) -> dict[str, Any]:
        session = await self._sessions.get_or_create(session_id)
        group = await session.get_group(group_id=group_id)
        if group is None:
            return {"ok": False, "error": "group_not_found"}
        # Cancel group and optionally propagate to tasks
        propagate = propagate_on_cancel == "cascade"
        await session.cancel_group(group_id, reason=reason, propagate=propagate)
        cancelled_count = len(group.pending_task_ids) if propagate else 0
        return {
            "ok": True,
            "group_id": group_id,
            "cancelled_task_count": cancelled_count,
            "total_tasks": len(group.task_ids),
        }

    async def apply_group(
        self,
        *,
        session_id: str,
        group_id: str,
        action: Literal["apply", "reject"] = "apply",
        strategy: MergeStrategy | None = None,
    ) -> dict[str, Any]:
        session = await self._sessions.get_or_create(session_id)
        group = await session.get_group(group_id=group_id)
        if group is None:
            return {"ok": False, "error": "group_not_found"}
        if not group.is_complete:
            return {
                "ok": False,
                "error": "group_not_complete",
                "pending_tasks": group.pending_task_ids,
            }
        applied_count = 0
        rejected_count = 0
        for patch_id in group.patches:
            if action == "apply":
                if await session.apply_pending_patch(patch_id=patch_id, strategy=strategy):
                    applied_count += 1
            else:
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
                rejected_count += 1
        return {
            "ok": True,
            "group_id": group_id,
            "action": action,
            "applied_patch_count": applied_count if action == "apply" else 0,
            "rejected_patch_count": rejected_count if action == "reject" else 0,
            "total_patches": len(group.patches),
        }

    async def list_groups(
        self,
        *,
        session_id: str,
        status: GroupStatus | None = None,
    ) -> builtins.list[TaskGroup]:
        session = await self._sessions.get_or_create(session_id)
        return await session.list_groups(status=status)

    async def get_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> TaskGroup | None:
        session = await self._sessions.get_or_create(session_id)
        return await session.get_group(group_id=group_id, group_name=group_name, turn_id=turn_id)


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
