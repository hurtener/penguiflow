"""Scheduled job contracts and a lightweight scheduler loop."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ScheduleConfig(BaseModel):
    interval_s: int | None = None
    next_run_at: datetime | None = None
    timezone: str | None = None

    def next_after(self, when: datetime) -> datetime | None:
        if self.interval_s is None:
            return None
        return when + timedelta(seconds=self.interval_s)


class JobDefinition(BaseModel):
    job_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    session_id: str
    task_payload: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleConfig
    delivery_policy: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class JobRunRecord(BaseModel):
    job_id: str
    run_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    started_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None
    status: str | None = None
    result: dict[str, Any] | None = None


class JobStore(Protocol):
    async def save_job(self, job: JobDefinition) -> None: ...

    async def list_jobs(self, session_id: str | None = None) -> list[JobDefinition]: ...

    async def list_due(self, now: datetime) -> list[JobDefinition]: ...

    async def record_run(self, run: JobRunRecord) -> None: ...


class InMemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: dict[str, JobDefinition] = {}
        self._runs: list[JobRunRecord] = []
        self._lock = asyncio.Lock()

    async def save_job(self, job: JobDefinition) -> None:
        async with self._lock:
            job.updated_at = _utc_now()
            self._jobs[job.job_id] = job

    async def list_jobs(self, session_id: str | None = None) -> list[JobDefinition]:
        async with self._lock:
            jobs = list(self._jobs.values())
        if session_id is None:
            return jobs
        return [job for job in jobs if job.session_id == session_id]

    async def list_due(self, now: datetime) -> list[JobDefinition]:
        async with self._lock:
            jobs = list(self._jobs.values())
        due: list[JobDefinition] = []
        for job in jobs:
            if not job.enabled:
                continue
            next_run = job.schedule.next_run_at
            if next_run is not None and next_run <= now:
                due.append(job)
        return due

    async def record_run(self, run: JobRunRecord) -> None:
        async with self._lock:
            self._runs.append(run)


class JobScheduler:
    """Polls due jobs and triggers task creation through a callback."""

    def __init__(
        self,
        *,
        store: JobStore,
        spawn: Callable[[JobDefinition], Awaitable[str]],
    ) -> None:
        self._store = store
        self._spawn = spawn

    async def tick(self) -> None:
        now = _utc_now()
        due_jobs = await self._store.list_due(now)
        for job in due_jobs:
            run = JobRunRecord(job_id=job.job_id)
            await self._store.record_run(run)
            await self._spawn(job)
            if job.schedule.interval_s is not None:
                next_run = job.schedule.next_after(now)
                job.schedule.next_run_at = next_run
                await self._store.save_job(job)


class JobSchedulerRunner:
    """Background loop that ticks the scheduler on an interval."""

    def __init__(self, scheduler: JobScheduler, *, poll_interval_s: float = 5.0) -> None:
        self._scheduler = scheduler
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        async def _loop() -> None:
            while True:
                await self._scheduler.tick()
                await asyncio.sleep(self._poll_interval_s)

        self._task = asyncio.create_task(_loop(), name="job-scheduler-loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None


__all__ = [
    "InMemoryJobStore",
    "JobDefinition",
    "JobScheduler",
    "JobSchedulerRunner",
    "JobStore",
    "ScheduleConfig",
]
