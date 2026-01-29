from __future__ import annotations

import pytest

from penguiflow.sessions.models import TaskStatus, TaskType
from penguiflow.sessions.task_service import TaskDetails, TaskService, TaskSpawnResult, TaskSummary
from penguiflow.sessions.task_tools import SUBAGENT_FLAG_KEY, TASK_SERVICE_KEY, build_task_tool_specs


class DummyContext:
    def __init__(self, tool_context):
        self._tool_context = tool_context

    @property
    def llm_context(self):
        return {}

    @property
    def tool_context(self):
        return self._tool_context

    @property
    def meta(self):
        return {}

    @property
    def artifacts(self):
        raise RuntimeError("not_used")

    async def pause(self, reason, payload=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("not_used")

    async def emit_chunk(self, stream_id, seq, text, *, done=False, meta=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("not_used")

    async def emit_artifact(self, stream_id, chunk, *, done=False, artifact_type=None, meta=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("not_used")


class DummyService(TaskService):
    def __init__(self) -> None:
        self.spawn_calls: list[tuple[str, str]] = []
        self.job_calls: list[tuple[str, str]] = []
        self.list_calls: list[str] = []
        self.get_calls: list[tuple[str, str, bool]] = []
        self.ack_calls: list[tuple[str, list[str]]] = []

    async def spawn(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        query,
        parent_task_id=None,
        priority=0,
        merge_strategy=None,
        propagate_on_cancel="cascade",
        notify_on_complete=True,
        context_depth="full",
        task_id=None,
        idempotency_key=None,
        context_tool_context=None,
        # Group parameters
        group=None,
        group_id=None,
        group_sealed=False,
        retain_turn=False,
        group_merge_strategy=None,
        group_report=None,
        turn_id=None,
    ):
        _ = (
            parent_task_id,
            priority,
            merge_strategy,
            propagate_on_cancel,
            notify_on_complete,
            context_depth,
            task_id,
            idempotency_key,
            context_tool_context,
            group,
            group_id,
            group_sealed,
            retain_turn,
            group_merge_strategy,
            group_report,
            turn_id,
        )
        self.spawn_calls.append((session_id, query))
        return TaskSpawnResult(task_id="t1", session_id=session_id, status=TaskStatus.PENDING)

    async def list(self, *, session_id, status=None):  # type: ignore[no-untyped-def]
        _ = status
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

    async def get(self, *, session_id, task_id, include_result=False):  # type: ignore[no-untyped-def]
        self.get_calls.append((session_id, task_id, include_result))
        return TaskDetails(
            task_id=task_id,
            session_id=session_id,
            status=TaskStatus.PENDING,
            task_type=TaskType.BACKGROUND,
            priority=0,
        )

    async def cancel(self, *, session_id, task_id, reason=None):  # type: ignore[no-untyped-def]
        _ = session_id, task_id, reason
        return True

    async def prioritize(self, *, session_id, task_id, priority):  # type: ignore[no-untyped-def]
        _ = session_id, task_id, priority
        return True

    async def apply_patch(self, *, session_id, patch_id, action, strategy=None):  # type: ignore[no-untyped-def]
        _ = session_id, patch_id, action, strategy
        return True

    async def acknowledge_background(self, *, session_id, task_ids):  # type: ignore[no-untyped-def]
        self.ack_calls.append((session_id, list(task_ids)))
        return len(task_ids)

    async def spawn_tool_job(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        tool_name,
        tool_args,
        parent_task_id=None,
        priority=0,
        merge_strategy=None,
        propagate_on_cancel="cascade",
        notify_on_complete=True,
        task_id=None,
        context_tool_context=None,
        # Group parameters
        group=None,
        group_id=None,
        group_sealed=False,
        retain_turn=False,
        group_merge_strategy=None,
        group_report=None,
        turn_id=None,
    ):
        _ = (
            tool_args,
            parent_task_id,
            priority,
            merge_strategy,
            propagate_on_cancel,
            notify_on_complete,
            task_id,
            context_tool_context,
            group,
            group_id,
            group_sealed,
            retain_turn,
            group_merge_strategy,
            group_report,
            turn_id,
        )
        self.job_calls.append((session_id, tool_name))
        return TaskSpawnResult(task_id="t_job", session_id=session_id, status=TaskStatus.PENDING)

    # Task Group Methods
    async def seal_group(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        group_id=None,
        group_name=None,
        turn_id=None,
    ):
        _ = group_name, turn_id
        return {"ok": True, "group_id": group_id or "g1", "sealed_task_count": 2}

    async def cancel_group(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        group_id,
        reason=None,
        propagate_on_cancel="cascade",
    ):
        _ = session_id, reason, propagate_on_cancel
        return {"ok": True, "cancelled_task_count": 1}

    async def apply_group(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        group_id,
        action="apply",
        strategy=None,
    ):
        _ = session_id, strategy
        return {
            "ok": True,
            "action": action,
            "applied_patch_count": 2 if action == "apply" else 0,
        }

    async def list_groups(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        status=None,
    ):
        _ = status
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

    async def get_group(  # type: ignore[no-untyped-def]
        self,
        *,
        session_id,
        group_id=None,
        group_name=None,
        turn_id=None,
    ):
        _ = group_name, turn_id
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
    specs = build_task_tool_specs()
    names = {spec.name for spec in specs}
    assert "tasks.spawn" in names
    assert "tasks.list" in names
    assert "tasks.acknowledge_background" in names


@pytest.mark.asyncio
async def test_task_tools_reject_subagent() -> None:
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
        await tasks_spawn(TasksSpawnArgs(query="hi"), ctx)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_task_tools_require_task_service() -> None:
    from penguiflow.sessions.task_tools import TasksSpawnArgs, tasks_spawn

    ctx = DummyContext(
        {
            "session_id": "s1",
            SUBAGENT_FLAG_KEY: False,
        }
    )
    with pytest.raises(RuntimeError, match="task_service_unavailable"):
        await tasks_spawn(TasksSpawnArgs(query="hi"), ctx)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_task_spawn_calls_service() -> None:
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
    result = await tasks_spawn(TasksSpawnArgs(query="do it"), ctx)  # type: ignore[arg-type]
    assert result.task_id == "t1"
    assert service.spawn_calls == [("s1", "do it")]


@pytest.mark.asyncio
async def test_task_tools_other_methods() -> None:
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
    listed = await tasks_list(TasksListArgs(status=None), ctx)  # type: ignore[arg-type]
    assert listed.tasks and listed.tasks[0].task_id == "t1"
    assert service.list_calls == ["s1"]

    got = await tasks_get(TasksGetArgs(task_id="t1", include_result=True), ctx)  # type: ignore[arg-type]
    assert got.task_id == "t1"
    assert service.get_calls == [("s1", "t1", True)]

    cancelled = await tasks_cancel(TasksCancelArgs(task_id="t1", reason="stop"), ctx)  # type: ignore[arg-type]
    assert cancelled.ok is True

    prioritized = await tasks_prioritize(TasksPrioritizeArgs(task_id="t1", priority=5), ctx)  # type: ignore[arg-type]
    assert prioritized.ok is True

    applied = await tasks_apply_patch(TasksApplyPatchArgs(patch_id="p1", action="reject"), ctx)  # type: ignore[arg-type]
    assert applied.ok is True
    assert applied.action == "reject"

    acknowledged = await tasks_acknowledge_background(
        TasksAcknowledgeBackgroundArgs(task_ids=["t1", "t2"]),
        ctx,  # type: ignore[arg-type]
    )
    assert acknowledged.acknowledged == ["t1", "t2"]
    assert acknowledged.cleaned_up == 2
    assert service.ack_calls == [("s1", ["t1", "t2"])]


@pytest.mark.asyncio
async def test_task_tools_missing_service_or_session_id_errors() -> None:
    from penguiflow.sessions.task_tools import TasksListArgs, tasks_list

    ctx = DummyContext({"session_id": "s1", SUBAGENT_FLAG_KEY: False})
    with pytest.raises(RuntimeError):
        await tasks_list(TasksListArgs(status=None), ctx)  # type: ignore[arg-type]

    service = DummyService()
    ctx2 = DummyContext({TASK_SERVICE_KEY: service, SUBAGENT_FLAG_KEY: False})
    with pytest.raises(RuntimeError):
        await tasks_list(TasksListArgs(status=None), ctx2)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_task_spawn_job_mode_calls_spawn_tool_job() -> None:
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
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
        ctx,  # type: ignore[arg-type]
    )
    assert result.group_id == "g1"
    assert result.name == "test-group"
    assert result.status == "open"
