"""Tests for task tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest
from typing_extensions import override

from penguiflow.artifacts import NoOpArtifactStore
from penguiflow.sessions.models import (
    GroupReportStrategy,
    GroupStatus,
    MergeStrategy,
    TaskGroup,
    TaskStatus,
    TaskType,
)
from penguiflow.sessions.task_service import (
    ContextDepth,
    TaskDetails,
    TaskService,
    TaskSpawnResult,
    TaskSummary,
)
from penguiflow.sessions.task_tools import (
    SUBAGENT_FLAG_KEY,
    TASK_SERVICE_KEY,
    build_task_tool_specs,
)

from .tool_context_doubles import FakeToolContext

if TYPE_CHECKING:
    import builtins


class DummyContext(FakeToolContext):
    """Tool context over ``tool_context`` with artifacts going nowhere."""

    def __init__(self, tool_context: dict[str, Any]) -> None:
        """Use ``tool_context``; artifacts go to a no-op store."""
        super().__init__(
            tool_context=tool_context, artifact_store=NoOpArtifactStore()
        )


class DummyService(TaskService):
    """Task service that records each call."""

    def __init__(self) -> None:
        """Start with no recorded state."""
        self.spawn_calls: list[tuple[str, str]] = []
        self.job_calls: list[tuple[str, str]] = []
        self.list_calls: list[str] = []
        self.get_calls: list[tuple[str, str, bool]] = []
        self.ack_calls: list[tuple[str, list[str]]] = []

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
        self.spawn_calls.append((session_id, query))
        return TaskSpawnResult(
            task_id="t1", session_id=session_id, status=TaskStatus.PENDING
        )

    @override
    async def list(
        self, *, session_id: str, status: TaskStatus | None = None
    ) -> builtins.list[TaskSummary]:
        self.list_calls.append(session_id)
        return [
            TaskSummary(
                task_id="t1",
                session_id=session_id,
                status=TaskStatus.PENDING,
                task_type=TaskType.BACKGROUND,
                priority=0,
            )
        ]

    @override
    async def get(
        self, *, session_id: str, task_id: str, include_result: bool = False
    ) -> TaskDetails | None:
        self.get_calls.append((session_id, task_id, include_result))
        return TaskDetails(
            task_id=task_id,
            session_id=session_id,
            status=TaskStatus.PENDING,
            task_type=TaskType.BACKGROUND,
            priority=0,
        )

    @override
    async def cancel(
        self, *, session_id: str, task_id: str, reason: str | None = None
    ) -> bool:
        return True

    @override
    async def prioritize(
        self, *, session_id: str, task_id: str, priority: int
    ) -> bool:
        return True

    @override
    async def apply_patch(
        self,
        *,
        session_id: str,
        patch_id: str,
        action: Literal["apply", "reject"],
        strategy: MergeStrategy | None = None,
    ) -> bool:
        return True

    @override
    async def acknowledge_background(
        self, *, session_id: str, task_ids: builtins.list[str]
    ) -> int:
        self.ack_calls.append((session_id, list(task_ids)))
        return len(task_ids)

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
        self.job_calls.append((session_id, tool_name))
        return TaskSpawnResult(
            task_id="t_job", session_id=session_id, status=TaskStatus.PENDING
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
        return {
            "ok": True,
            "group_id": group_id or "g1",
            "sealed_task_count": 2,
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
        return {"ok": True, "cancelled_task_count": 1}

    @override
    async def apply_group(
        self,
        *,
        session_id: str,
        group_id: str,
        action: Literal["apply", "reject"] = "apply",
        strategy: MergeStrategy | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "action": action,
            "applied_patch_count": 2 if action == "apply" else 0,
        }

    @override
    async def list_groups(
        self,
        *,
        session_id: str,
        status: GroupStatus | None = None,
    ) -> builtins.list[TaskGroup]:
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        return [
            TaskGroup(
                group_id="g1",
                name="test-group",
                session_id=session_id,
                status="open",
                merge_strategy=MergeStrategy.APPEND,
                report_strategy="all",
            )
        ]

    @override
    async def get_group(
        self,
        *,
        session_id: str,
        group_id: str | None = None,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> TaskGroup | None:
        from penguiflow.sessions.models import MergeStrategy, TaskGroup

        return TaskGroup(
            group_id=group_id or "g1",
            name="test-group",
            session_id=session_id,
            status="open",
            merge_strategy=MergeStrategy.APPEND,
            report_strategy="all",
        )


@pytest.mark.asyncio
async def test_task_tools_specs_build() -> None:
    """Task tools specs build."""
    specs = build_task_tool_specs()
    names = {spec.name for spec in specs}
    assert "tasks.spawn" in names
    assert "tasks.list" in names
    assert "tasks.acknowledge_background" in names


@pytest.mark.asyncio
async def test_task_tools_reject_subagent() -> None:
    """Task tools reject subagent."""
    from penguiflow.sessions.task_tools import TasksSpawnArgs, tasks_spawn

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: True,
        }
    )
    with pytest.raises(RuntimeError):
        await tasks_spawn(TasksSpawnArgs(query="hi"), ctx)


@pytest.mark.asyncio
async def test_task_tools_require_task_service() -> None:
    """Task tools require task service."""
    from penguiflow.sessions.task_tools import TasksSpawnArgs, tasks_spawn

    ctx = DummyContext(
        {
            "session_id": "s1",
            SUBAGENT_FLAG_KEY: False,
        }
    )
    with pytest.raises(RuntimeError, match="task_service_unavailable"):
        await tasks_spawn(TasksSpawnArgs(query="hi"), ctx)


@pytest.mark.asyncio
async def test_task_spawn_calls_service() -> None:
    """Task spawn calls service."""
    from penguiflow.sessions.task_tools import TasksSpawnArgs, tasks_spawn

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_spawn(TasksSpawnArgs(query="do it"), ctx)
    assert result.task_id == "t1"
    assert service.spawn_calls == [("s1", "do it")]


@pytest.mark.asyncio
async def test_task_tools_other_methods() -> None:
    """Task tools other methods."""
    from penguiflow.sessions.task_tools import (
        TasksAcknowledgeBackgroundArgs,
        TasksApplyPatchArgs,
        TasksCancelArgs,
        TasksGetArgs,
        TasksListArgs,
        TasksPrioritizeArgs,
        tasks_acknowledge_background,
        tasks_apply_patch,
        tasks_cancel,
        tasks_get,
        tasks_list,
        tasks_prioritize,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    listed = await tasks_list(TasksListArgs(status=None), ctx)
    assert listed.tasks and listed.tasks[0].task_id == "t1"
    assert service.list_calls == ["s1"]

    got = await tasks_get(TasksGetArgs(task_id="t1", include_result=True), ctx)
    assert got.task_id == "t1"
    assert service.get_calls == [("s1", "t1", True)]

    cancelled = await tasks_cancel(
        TasksCancelArgs(task_id="t1", reason="stop"), ctx
    )
    assert cancelled.ok is True

    prioritized = await tasks_prioritize(
        TasksPrioritizeArgs(task_id="t1", priority=5), ctx
    )
    assert prioritized.ok is True

    applied = await tasks_apply_patch(
        TasksApplyPatchArgs(patch_id="p1", action="reject"), ctx
    )
    assert applied.ok is True
    assert applied.action == "reject"

    acknowledged = await tasks_acknowledge_background(
        TasksAcknowledgeBackgroundArgs(task_ids=["t1", "t2"]),
        ctx,
    )
    assert acknowledged.acknowledged == ["t1", "t2"]
    assert acknowledged.cleaned_up == 2
    assert service.ack_calls == [("s1", ["t1", "t2"])]


@pytest.mark.asyncio
async def test_task_tools_missing_service_or_session_id_errors() -> None:
    """Task tools missing service or session ID errors."""
    from penguiflow.sessions.task_tools import TasksListArgs, tasks_list

    ctx = DummyContext({"session_id": "s1", SUBAGENT_FLAG_KEY: False})
    with pytest.raises(RuntimeError):
        await tasks_list(TasksListArgs(status=None), ctx)

    service = DummyService()
    ctx2 = DummyContext({TASK_SERVICE_KEY: service, SUBAGENT_FLAG_KEY: False})
    with pytest.raises(RuntimeError):
        await tasks_list(TasksListArgs(status=None), ctx2)


@pytest.mark.asyncio
async def test_task_spawn_job_mode_calls_spawn_tool_job() -> None:
    """Task spawn job mode calls spawn tool job."""
    from penguiflow.sessions.task_tools import TasksSpawnArgs, tasks_spawn

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_spawn(
        TasksSpawnArgs(mode="job", tool_name="t", tool_args={"x": 1}),
        ctx,
    )
    assert result.task_id == "t_job"
    assert service.job_calls == [("s1", "t")]


# ============== Task Group Tool Tests ==============


@pytest.mark.asyncio
async def test_task_spawn_with_group() -> None:
    """Test spawning a task with group parameters."""
    from penguiflow.sessions.task_tools import TasksSpawnArgs, tasks_spawn

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            "turn_id": "turn-1",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_spawn(
        TasksSpawnArgs(query="analyze data", group="analysis"),
        ctx,
    )
    assert result.task_id == "t1"
    assert service.spawn_calls == [("s1", "analyze data")]


@pytest.mark.asyncio
async def test_tasks_seal_group() -> None:
    """Test sealing a task group."""
    from penguiflow.sessions.task_tools import (
        TasksSealGroupArgs,
        tasks_seal_group,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_seal_group(
        TasksSealGroupArgs(group_id="g1"),
        ctx,
    )
    assert result.ok is True
    assert result.group_id == "g1"


@pytest.mark.asyncio
async def test_tasks_cancel_group() -> None:
    """Test cancelling a task group."""
    from penguiflow.sessions.task_tools import (
        TasksCancelGroupArgs,
        tasks_cancel_group,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_cancel_group(
        TasksCancelGroupArgs(group_id="g1", reason="cancelled by user"),
        ctx,
    )
    assert result.ok is True


@pytest.mark.asyncio
async def test_tasks_apply_group() -> None:
    """Test applying patches for a task group."""
    from penguiflow.sessions.task_tools import (
        TasksApplyGroupArgs,
        tasks_apply_group,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_apply_group(
        TasksApplyGroupArgs(group_id="g1", action="apply"),
        ctx,
    )
    assert result.ok is True
    assert result.action == "apply"


@pytest.mark.asyncio
async def test_tasks_apply_group_reject() -> None:
    """Test rejecting patches for a task group."""
    from penguiflow.sessions.task_tools import (
        TasksApplyGroupArgs,
        tasks_apply_group,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_apply_group(
        TasksApplyGroupArgs(group_id="g1", action="reject"),
        ctx,
    )
    assert result.ok is True
    assert result.action == "reject"


@pytest.mark.asyncio
async def test_tasks_list_groups() -> None:
    """Test listing task groups."""
    from penguiflow.sessions.task_tools import (
        TasksListGroupsArgs,
        tasks_list_groups,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_list_groups(
        TasksListGroupsArgs(status=None),
        ctx,
    )
    assert len(result.groups) == 1
    assert result.groups[0].group_id == "g1"
    assert result.groups[0].name == "test-group"


@pytest.mark.asyncio
async def test_tasks_get_group() -> None:
    """Test getting a specific task group."""
    from penguiflow.sessions.task_tools import (
        TasksGetGroupArgs,
        tasks_get_group,
    )

    service = DummyService()
    ctx = DummyContext(
        {
            "session_id": "s1",
            "task_id": "foreground",
            TASK_SERVICE_KEY: service,
            SUBAGENT_FLAG_KEY: False,
        }
    )
    result = await tasks_get_group(
        TasksGetGroupArgs(group_id="g1"),
        ctx,
    )
    assert result.group_id == "g1"
    assert result.name == "test-group"
    assert result.status == "open"
