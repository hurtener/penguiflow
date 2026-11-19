"""Task management service contracts used by planner-facing meta-tools.

This module intentionally separates:
- the planner-facing tool surface (tasks.* tools)
- the underlying session/task orchestration (StreamingSession)

Downstream products can provide custom TaskService implementations that map to
distributed queues, databases, or multi-tenant governance layers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, Field
from typing_extensions import override

from .models import (
    GroupReportStrategy,
    GroupStatus,
    MergeStrategy,
    TaskGroup,
    TaskStatus,
    TaskType,
)
from .planner import PlannerFactory, PlannerTaskPipeline
from .session import SessionManager, TaskPipeline, _json_snapshot_dict

if TYPE_CHECKING:
    import builtins
    from collections.abc import Callable

    from penguiflow.planner.models import BackgroundTasksConfig

    from .models import TaskContextSnapshot
    from .session import StreamingSession

ContextDepth = Literal["full", "summary", "none"]
SpawnMode = Literal["subagent", "job"]


class TaskSummary(BaseModel):
    """A short view of a task for listings."""

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
    """A task with its result digest and lineage."""

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
    """Outcome of a spawn: the task, its group and any retained result."""

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
    """A spawn request presented to the :class:`SpawnGuard`."""

    session_id: str
    parent_task_id: str | None = None
    mode: SpawnMode = "subagent"
    query: str | None = None
    tool_name: str | None = None
    priority: int = 0
    merge_strategy: MergeStrategy = MergeStrategy.HUMAN_GATED


class SpawnDecision(BaseModel):
    """A :class:`SpawnGuard` verdict on a spawn request."""

    allowed: bool = True
    reason: str | None = None
    estimated_cost: int | None = None


class SpawnGuard(Protocol):
    """Extension point that governs/vetoes background task and job spawns.

    Implement this protocol to enforce tenant-specific policy (quotas, cost
    limits, allow/deny lists, etc.) before a background subagent or tool job is
    spawned. ``InProcessTaskService`` calls ``decide`` on every
    ``spawn``/``spawn_tool_job`` request before creating the task.
    """

    async def decide(self, request: SpawnRequest) -> SpawnDecision:
        """Decide whether a spawn request should be allowed.

        Args:
            request: Details of the spawn being requested (session, mode,
                query/tool name, priority, merge strategy).

        Returns:
            A :class:`SpawnDecision` indicating whether the spawn is allowed,
            with an optional reason (used as the error message when denied) and
            estimated cost.
        """
        ...


class NoOpSpawnGuard(SpawnGuard):
    """Spawn guard that allows every spawn."""

    @override
    async def decide(self, request: SpawnRequest) -> SpawnDecision:
        return SpawnDecision(allowed=True)


class TaskService(Protocol):
    """Background task orchestration service for tasks.* meta-tools.

    This protocol is the documented extension contract between the
    planner-facing ``tasks.*`` meta-tools and whatever backs task orchestration
    at runtime. The default implementation, :class:`InProcessTaskService`,
    delegates to an in-process
    :class:`SessionManager`/:class:`StreamingSession`. Downstream products may
    provide their own implementation to route spawns through distributed
    queues, external job schedulers, databases, or multi-tenant governance
    layers, as long as they honor the method contracts below.
    """

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
        """Spawn a background subagent to answer ``query`` for the session.

        Implementations should be idempotent when ``idempotency_key`` or
        ``task_id`` is supplied: a repeated call with the same key/ID for the
        same session
            should return
        the existing task's result rather than spawning a duplicate. If
        ``group``/``group_id`` is given, the new task should be resolved/joined
        into that task group (creating it if needed), optionally sealing it
        (``group_sealed``) and, if ``retain_turn`` is set, waiting for the
        group to complete before returning.

        Args:
            session_id: The session the task belongs to.
            query: The natural-language query the subagent should answer.
            parent_task_id: The task that triggered this spawn, if any.
            priority: Scheduling priority hint.
            merge_strategy: Strategy used to merge the task's resulting context
                patch back into the session.
            propagate_on_cancel: Whether cancelling this task cascades to its
                children.
            notify_on_complete: Whether to emit a default completion
                notification.
            context_depth: How much of the session context to snapshot for the
                subagent: ``"full"``, ``"summary"``, or ``"none"``.
            task_id: Explicit task ID; a new one is generated if omitted.
            idempotency_key: Key used to deduplicate repeated spawn requests.
            context_tool_context: Extra tool-context entries to merge into the
                task's snapshot.
            group: Name of the task group to join/create (turn-scoped
                resolution).
            group_id: Explicit task group ID to join.
            group_sealed: Whether to seal the group immediately after joining.
            retain_turn: Whether to keep the foreground turn open, waiting for
                the (sealed) group to complete before returning.
            group_merge_strategy: Merge strategy to use for the group as a
                whole.
            group_report: Reporting strategy for the group's aggregate report.
            turn_id: The current turn ID, used for turn-scoped group name
                resolution.

        Returns:
            A :class:`TaskSpawnResult` describing the spawned task (and, if
            applicable, the group it joined and its completion state).

        Raises:
            RuntimeError: If the spawn is blocked by a :class:`SpawnGuard`, or
                if background subagent support is unavailable.
        """
        ...

    async def list(
        self,
        *,
        session_id: str,
        status: TaskStatus | None = None,
    ) -> builtins.list[TaskSummary]:
        """List tasks for a session, optionally filtered by status.

        Args:
            session_id: The session whose tasks should be listed.
            status: If provided, only include tasks with this status.

        Returns:
            A list of :class:`TaskSummary` objects for the matching tasks.
        """
        ...

    async def get(
        self,
        *,
        session_id: str,
        task_id: str,
        include_result: bool = False,
    ) -> TaskDetails | None:
        """Fetch detailed information about a single task.

        Args:
            session_id: The session the task belongs to.
            task_id: The task to fetch.
            include_result: Whether to populate ``result_digest`` from the
                task's result.

        Returns:
            A :class:`TaskDetails` for the task, or ``None`` if no such task
            exists.
        """
        ...

    async def cancel(
        self,
        *,
        session_id: str,
        task_id: str,
        reason: str | None = None,
    ) -> bool:
        """Request cancellation of a running or pending task.

        Args:
            session_id: The session the task belongs to.
            task_id: The task to cancel.
            reason: Optional human-readable reason recorded on the
                cancellation.

        Returns:
            ``True`` if the cancellation was accepted, ``False`` otherwise.
        """
        ...

    async def prioritize(
        self,
        *,
        session_id: str,
        task_id: str,
        priority: int,
    ) -> bool:
        """Change the scheduling priority of an existing task.

        Args:
            session_id: The session the task belongs to.
            task_id: The task whose priority should change.
            priority: The new priority value.

        Returns:
            ``True`` if the priority change was accepted, ``False`` otherwise.
        """
        ...

    async def apply_patch(
        self,
        *,
        session_id: str,
        patch_id: str,
        action: Literal["apply", "reject"],
        strategy: MergeStrategy | None = None,
    ) -> bool:
        """Approve or reject a pending, human-gated context patch.

        Args:
            session_id: The session the patch belongs to.
            patch_id: The ID of the pending patch (as previously surfaced to
                the user).
            action: ``"apply"`` to merge the patch into the session context, or
                ``"reject"`` to discard it.
            strategy: Merge strategy to use when applying; implementations may
                fall back to a default (e.g. ``APPEND``) when omitted.

        Returns:
            ``True`` if the patch was found and the requested action was
            performed, ``False`` otherwise.
        """
        ...

    async def acknowledge_background(
        self,
        *,
        session_id: str,
        task_ids: builtins.list[str],
    ) -> int:
        """Mark background results as consumed so they stop showing as pending.

        Args:
            session_id: The session the tasks belong to.
            task_ids: The task IDs whose background results should be
                acknowledged.

        Returns:
            The number of background results actually removed/acknowledged.
        """
        ...

    async def spawn_tool_job(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: object,
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
        """Spawn a background job that runs one tool call, not a subagent.

        Semantics mirror :meth:`spawn` (idempotency, task groups,
        ``retain_turn``), but the task's work is a single tool invocation
        (``tool_name``/``tool_args``) instead of an open-ended subagent query.

        Args:
            session_id: The session the job belongs to.
            tool_name: The name of the tool to invoke in the background.
            tool_args: The arguments to pass to the tool.
            parent_task_id: The task that triggered this spawn, if any.
            priority: Scheduling priority hint.
            merge_strategy: Strategy used to merge the job's resulting context
                patch back into the session.
            propagate_on_cancel: Whether cancelling this job cascades to its
                children.
            notify_on_complete: Whether to emit a default completion
                notification.
            task_id: Explicit task ID; a new one is generated if omitted.
            context_tool_context: Extra tool-context entries to merge into the
                job's snapshot.
            group: Name of the task group to join/create (turn-scoped
                resolution).
            group_id: Explicit task group ID to join.
            group_sealed: Whether to seal the group immediately after joining.
            retain_turn: Whether to keep the foreground turn open, waiting for
                the (sealed) group to complete before returning.
            group_merge_strategy: Merge strategy to use for the group as a
                whole.
            group_report: Reporting strategy for the group's aggregate report.
            turn_id: The current turn ID, used for turn-scoped group name
                resolution.

        Returns:
            A :class:`TaskSpawnResult` describing the spawned job (and, if
            applicable, the group it joined and its completion state).

        Raises:
            RuntimeError: If the spawn is blocked by a :class:`SpawnGuard`, or
                if background tool-job support is unavailable.
        """
        ...

    # Task Group Methods

    async def seal_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """Seal a task group so no further tasks may join it.

        Args:
            session_id: The session the group belongs to.
            group_id: Direct lookup of the group by ID.
            group_name: Name-based lookup of the group (see ``turn_id``).
            turn_id: Turn ID used to disambiguate name-based lookup.

        Returns:
            A result dict with at least ``"ok"``; on success it includes
            ``"group_id"``, ``"status"``, and ``"sealed_task_count"``. On
            failure it includes ``"error"`` (e.g. ``"group_not_found"``).
        """
        ...

    async def cancel_group(
        self,
        *,
        session_id: str,
        group_id: str,
        reason: str | None = None,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
    ) -> dict[str, Any]:
        """Cancel a task group, optionally cancelling its member tasks.

        Args:
            session_id: The session the group belongs to.
            group_id: The group to cancel.
            reason: Optional human-readable reason recorded on the
                cancellation.
            propagate_on_cancel: ``"cascade"`` to also cancel the group's
                pending tasks, ``"isolate"`` to leave them running.

        Returns:
            A result dict with at least ``"ok"``; on success it includes
            ``"group_id"``, ``"cancelled_task_count"``, and ``"total_tasks"``.
            On failure it includes ``"error"`` (e.g. ``"group_not_found"``).
        """
        ...

    async def apply_group(
        self,
        *,
        session_id: str,
        group_id: str,
        action: Literal["apply", "reject"] = "apply",
        strategy: MergeStrategy | None = None,
    ) -> dict[str, Any]:
        """Apply or reject the pending human-gated patches of a done group.

        Args:
            session_id: The session the group belongs to.
            group_id: The group whose patches should be applied/rejected.
            action: ``"apply"`` to merge all pending patches, ``"reject"`` to
                discard them.
            strategy: Merge strategy to use when applying; implementations may
                fall back to a default (e.g. ``APPEND``) when omitted.

        Returns:
            A result dict with at least ``"ok"``; on success it includes
            ``"group_id"``, ``"action"``, ``"applied_patch_count"``,
            ``"rejected_patch_count"``, and ``"total_patches"``. On failure it
            includes ``"error"`` (e.g. ``"group_not_found"`` or
            ``"group_not_complete"``).
        """
        ...

    async def list_groups(
        self,
        *,
        session_id: str,
        status: GroupStatus | None = None,
    ) -> builtins.list[TaskGroup]:
        """List task groups for a session, optionally filtered by status.

        Args:
            session_id: The session whose groups should be listed.
            status: If provided, only include groups with this status.

        Returns:
            A list of matching :class:`TaskGroup` objects.
        """
        ...

    async def get_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> TaskGroup | None:
        """Look up a task group by ID or name (optionally within a turn).

        Args:
            session_id: The session the group belongs to.
            group_id: Direct lookup of the group by ID.
            group_name: Name-based lookup of the group (see ``turn_id``).
            turn_id: Turn ID used to disambiguate name-based lookup.

        Returns:
            The matching :class:`TaskGroup`, or ``None`` if no such group
            exists.
        """
        ...


def _digest_from_result(result: object) -> list[str]:
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


def _spawn_snapshot(
    session: StreamingSession,
    *,
    task_id: str | None,
    parent_task_id: str | None,
    spawn_reason: str,
    query: str | None,
    propagate_on_cancel: Literal["cascade", "isolate"],
    notify_on_complete: bool,
    context_depth: ContextDepth,
    context_tool_context: dict[str, Any] | None,
) -> TaskContextSnapshot:
    """Snapshot the session context a spawned task starts from.

    ``context_depth`` trims the LLM context: ``"none"`` drops it and
    ``"summary"`` keeps only the conversation summary.
    """
    from .models import TaskContextSnapshot as Snapshot

    llm_context, tool_context = session.get_context()
    if context_depth == "none":
        llm_context = {}
    elif context_depth == "summary":
        llm_context = {
            "summary": llm_context.get("summary")
            or llm_context.get("conversation_summary")
            or "",
        }
    tool_context = dict(tool_context)
    if context_tool_context:
        tool_context.update(context_tool_context)
    return Snapshot(
        session_id=session.session_id,
        task_id=task_id or "pending",
        spawned_from_task_id=parent_task_id or "foreground",
        spawn_reason=spawn_reason,
        query=query,
        propagate_on_cancel=propagate_on_cancel,
        notify_on_complete=notify_on_complete,
        llm_context=_json_snapshot_dict(dict(llm_context)),
        tool_context=_json_snapshot_dict(tool_context),
        context_version=session.context_version,
        context_hash=session.context_hash,
    )


async def _join_group(
    session: StreamingSession,
    group: TaskGroup,
    task_id: str,
    *,
    group_sealed: bool,
    retain_turn: bool,
) -> TaskGroup:
    """Add a task to its group, sealing it when asked or retaining the turn."""
    await session.add_task_to_group(group.group_id, task_id)
    if group_sealed:
        await session.seal_group(group.group_id)
    if retain_turn and group.status == "open":
        await session.seal_group(group.group_id)
    group = await session.get_group(group_id=group.group_id) or group
    if retain_turn and group.status == "open":
        await session.seal_group(group.group_id)
    return await session.get_group(group_id=group.group_id) or group


async def _retain_turn(
    session: StreamingSession,
    group: TaskGroup | None,
    *,
    retain_turn: bool,
) -> tuple[bool, GroupCompletionResult | None]:
    """Wait for a sealed group when the caller retains the turn.

    HUMAN_GATED groups are never retained: their results need approval.

    Returns:
        Whether the turn was retained, and the group outcome if it completed.
    """
    if (
        not retain_turn
        or group is None
        or group.status != "sealed"
        or group.merge_strategy == MergeStrategy.HUMAN_GATED
    ):
        return False, None
    config = session.get_background_task_config()
    timeout_s_raw = float(config.retain_turn_timeout_s)
    timeout_s = timeout_s_raw if timeout_s_raw > 0 else None
    completed_group, timed_out = await session.wait_for_group_completion(
        group.group_id, timeout_s=timeout_s
    )
    group_completion: GroupCompletionResult | None = None
    if completed_group is not None:
        group_completion = GroupCompletionResult(
            group_id=completed_group.group_id,
            group_name=completed_group.name,
            status=completed_group.status,
            task_count=len(completed_group.task_ids),
            completed_task_ids=list(completed_group.completed_task_ids),
            failed_task_ids=list(completed_group.failed_task_ids),
            results=await session.get_group_results(group.group_id),
            timed_out=timed_out,
        )
    if completed_group is not None and not timed_out:
        task_ids = list(completed_group.task_ids)
        session.suppress_proactive_reports(
            task_ids=task_ids, group_id=completed_group.group_id
        )
        await session.mark_background_consumed(task_ids=task_ids)
    elif timed_out:
        session.schedule_group_continuation(
            group_id=group.group_id,
            timeout_s=timeout_s,
            max_hops=int(config.background_continuation_max_hops or 0),
            cooldown_s=float(config.background_continuation_cooldown_s or 0.0),
        )
    return True, group_completion


async def _spawn_result(
    session: StreamingSession,
    task_id: str,
    *,
    group: TaskGroup | None,
    retained: bool,
    group_completion: GroupCompletionResult | None,
) -> TaskSpawnResult:
    """Describe a spawned task, with its group and retained-turn outcome."""
    task = await session.get_task(task_id)
    return TaskSpawnResult(
        task_id=task_id,
        session_id=session.session_id,
        status=task.status if task else TaskStatus.PENDING,
        group_id=group.group_id if group else None,
        group=group.name if group else None,
        retained=retained,
        group_completion=group_completion,
    )


class InProcessTaskService(TaskService):
    """Default TaskService backed by an in-process SessionManager."""

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
        """Configure the sessions and factories used to spawn tasks."""
        self._sessions = sessions
        self._planner_factory = planner_factory
        self._subagent_factory = subagent_planner_factory or planner_factory
        self._tool_job_factory = tool_job_factory
        self._spawn_guard = spawn_guard or NoOpSpawnGuard()
        self._background_config = background_config
        self._lock = asyncio.Lock()
        self._idempotency: dict[tuple[str, str], str] = {}

    @property
    def sessions(self) -> SessionManager:
        """The session manager this service resolves sessions from."""
        return self._sessions

    def _apply_background_config(self, session: StreamingSession) -> None:
        if self._background_config is not None:
            session.configure_background_tasks(self._background_config)

    @override
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
            msg = "background_tasks_unavailable"
            raise RuntimeError(msg)
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
        existing = await self._existing_spawn(
            session, idempotency_key=idempotency_key, task_id=task_id
        )
        if existing is not None:
            return existing

        snapshot = None
        if context_depth != "full" or context_tool_context:
            snapshot = _spawn_snapshot(
                session,
                task_id=task_id,
                parent_task_id=parent_task_id,
                spawn_reason="tasks.spawn",
                query=query,
                propagate_on_cancel=propagate_on_cancel,
                notify_on_complete=notify_on_complete,
                context_depth=context_depth,
                context_tool_context=context_tool_context,
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
            resolved_group = await _join_group(
                session,
                resolved_group,
                created_id,
                group_sealed=group_sealed,
                retain_turn=retain_turn,
            )

        if idempotency_key is not None:
            async with self._lock:
                self._idempotency[(session_id, idempotency_key)] = created_id

        retained, group_completion = await _retain_turn(
            session, resolved_group, retain_turn=retain_turn
        )
        return await _spawn_result(
            session,
            created_id,
            group=resolved_group,
            retained=retained,
            group_completion=group_completion,
        )

    async def _existing_spawn(
        self,
        session: StreamingSession,
        *,
        idempotency_key: str | None,
        task_id: str | None,
    ) -> TaskSpawnResult | None:
        """Return the task an idempotent or explicit-id spawn already made."""
        if idempotency_key is not None:
            async with self._lock:
                existing = self._idempotency.get(
                    (session.session_id, idempotency_key)
                )
            if existing is not None:
                task = await session.get_task(existing)
                if task is not None:
                    return TaskSpawnResult(
                        task_id=existing,
                        session_id=session.session_id,
                        status=task.status,
                    )
        if task_id is not None:
            existing_task = await session.get_task(task_id)
            if existing_task is not None:
                return TaskSpawnResult(
                    task_id=task_id,
                    session_id=session.session_id,
                    status=existing_task.status,
                )
        return None

    @override
    async def list(
        self, *, session_id: str, status: TaskStatus | None = None
    ) -> builtins.list[TaskSummary]:
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

    @override
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
            spawned_from_task_id=snapshot.spawned_from_task_id
            if snapshot
            else None,
            spawned_from_event_id=snapshot.spawned_from_event_id
            if snapshot
            else None,
        )

    @override
    async def cancel(
        self, *, session_id: str, task_id: str, reason: str | None = None
    ) -> bool:
        session = await self._sessions.get_or_create(session_id)
        return await session.cancel_task(task_id, reason=reason)

    @override
    async def prioritize(
        self, *, session_id: str, task_id: str, priority: int
    ) -> bool:
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

    @override
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
        return await session.apply_pending_patch(
            patch_id=patch_id, strategy=strategy
        )

    @override
    async def acknowledge_background(
        self, *, session_id: str, task_ids: builtins.list[str]
    ) -> int:
        session = await self._sessions.get_or_create(session_id)
        return await session.mark_background_consumed(task_ids=task_ids)

    @override
    async def spawn_tool_job(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: object,
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
            msg = "tool_background_unavailable"
            raise RuntimeError(msg)
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
            snapshot = _spawn_snapshot(
                session,
                task_id=task_id,
                parent_task_id=parent_task_id,
                spawn_reason=f"tool:{tool_name}",
                query=None,
                propagate_on_cancel=propagate_on_cancel,
                notify_on_complete=notify_on_complete,
                context_depth="full",
                context_tool_context=context_tool_context,
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
            await session.add_task_to_group(
                resolved_group.group_id, created_id
            )
            if group_sealed:
                await session.seal_group(resolved_group.group_id)

        retained, group_completion = await _retain_turn(
            session, resolved_group, retain_turn=retain_turn
        )
        return await _spawn_result(
            session,
            created_id,
            group=resolved_group,
            retained=retained,
            group_completion=group_completion,
        )

    # Task Group Methods

    @override
    async def seal_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        session = await self._sessions.get_or_create(session_id)
        group = await session.get_group(
            group_id=group_id, group_name=group_name, turn_id=turn_id
        )
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

    @override
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
        await session.cancel_group(
            group_id, reason=reason, propagate=propagate
        )
        cancelled_count = len(group.pending_task_ids) if propagate else 0
        return {
            "ok": True,
            "group_id": group_id,
            "cancelled_task_count": cancelled_count,
            "total_tasks": len(group.task_ids),
        }

    @override
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
                if await session.apply_pending_patch(
                    patch_id=patch_id, strategy=strategy
                ):
                    applied_count += 1
            else:
                from penguiflow.steering import (
                    SteeringEvent,
                    SteeringEventType,
                )

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
            "rejected_patch_count": rejected_count
            if action == "reject"
            else 0,
            "total_patches": len(group.patches),
        }

    @override
    async def list_groups(
        self,
        *,
        session_id: str,
        status: GroupStatus | None = None,
    ) -> builtins.list[TaskGroup]:
        session = await self._sessions.get_or_create(session_id)
        return await session.list_groups(status=status)

    @override
    async def get_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> TaskGroup | None:
        session = await self._sessions.get_or_create(session_id)
        return await session.get_group(
            group_id=group_id, group_name=group_name, turn_id=turn_id
        )


__all__ = [
    "ContextDepth",
    "InProcessTaskService",
    "NoOpSpawnGuard",
    "SpawnDecision",
    "SpawnGuard",
    "SpawnMode",
    "SpawnRequest",
    "TaskDetails",
    "TaskService",
    "TaskSpawnResult",
    "TaskSummary",
]
