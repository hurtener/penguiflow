from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from penguiflow.sessions.scheduler import (
    InMemoryJobStore,
    JobDefinition,
    JobScheduler,
    JobSchedulerRunner,
    ScheduleConfig,
)


@pytest.mark.asyncio
async def test_schedule_config_next_after() -> None:
    now = datetime.now(UTC)
    cfg = ScheduleConfig(interval_s=None)
    assert cfg.next_after(now) is None
    cfg = ScheduleConfig(interval_s=60)
    assert cfg.next_after(now) == now + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_job_scheduler_ticks_due_jobs_and_updates_next_run() -> None:
    store = InMemoryJobStore()
    now = datetime.now(UTC)
    job = JobDefinition(
        session_id="s1",
        task_payload={"query": "report"},
        schedule=ScheduleConfig(interval_s=10, next_run_at=now - timedelta(seconds=1)),
    )
    await store.save_job(job)

    spawned: list[str] = []

    async def _spawn(defn: JobDefinition) -> str:
        spawned.append(defn.job_id)
        return "task-1"

    scheduler = JobScheduler(store=store, spawn=_spawn)
    await scheduler.tick()

    assert spawned == [job.job_id]
    jobs = await store.list_jobs("s1")
    assert jobs[0].schedule.next_run_at is not None
    assert jobs[0].schedule.next_run_at > now


@pytest.mark.asyncio
async def test_job_scheduler_skips_disabled_jobs() -> None:
    store = InMemoryJobStore()
    now = datetime.now(UTC)
    job = JobDefinition(
        session_id="s1",
        task_payload={"query": "report"},
        schedule=ScheduleConfig(interval_s=10, next_run_at=now - timedelta(seconds=1)),
        enabled=False,
    )
    await store.save_job(job)

    spawned: list[str] = []

    async def _spawn(defn: JobDefinition) -> str:
        spawned.append(defn.job_id)
        return "task-1"

    scheduler = JobScheduler(store=store, spawn=_spawn)
    await scheduler.tick()
    assert spawned == []


@pytest.mark.asyncio
async def test_job_scheduler_runner_start_stop() -> None:
    store = InMemoryJobStore()

    async def _spawn(_job: JobDefinition) -> str:
        return "task"

    scheduler = JobScheduler(store=store, spawn=_spawn)
    runner = JobSchedulerRunner(scheduler, poll_interval_s=0.01)
    await runner.start()
    await asyncio.sleep(0.03)
    await runner.stop()
    await runner.stop()

