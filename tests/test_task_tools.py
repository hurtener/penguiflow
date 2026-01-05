from __future__ import annotations

import pytest

from penguiflow.sessions.models import TaskStatus
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
                task_type="BACKGROUND",
                priority=0,
            )
        ]

    async def get(self, *, session_id, task_id, include_result=False):  # type: ignore[no-untyped-def]
        self.get_calls.append((session_id, task_id, include_result))
        return TaskDetails(
            task_id=task_id,
            session_id=session_id,
            status=TaskStatus.PENDING,
            task_type="BACKGROUND",
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
    ):
        _ = tool_args, parent_task_id, priority, merge_strategy, propagate_on_cancel, notify_on_complete, task_id
        self.job_calls.append((session_id, tool_name))
        return TaskSpawnResult(task_id="t_job", session_id=session_id, status=TaskStatus.PENDING)


@pytest.mark.asyncio
async def test_task_tools_specs_build() -> None:
    specs = build_task_tool_specs()
    names = {spec.name for spec in specs}
    assert "tasks.spawn" in names
    assert "tasks.list" in names


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
        TasksApplyPatchArgs,
        TasksCancelArgs,
        TasksGetArgs,
        TasksListArgs,
        TasksPrioritizeArgs,
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
