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
    """Recurrence rule for a scheduled job.

    A one-shot job sets only `next_run_at`; a recurring job also sets `interval_s`
    so `next_after` can compute the following run time after each tick.
    """

    interval_s: int | None = Field(
        default=None, description="Seconds between runs. None means the job does not repeat."
    )
    next_run_at: datetime | None = Field(
        default=None, description="Next timestamp at which the job is due to run."
    )
    timezone: str | None = Field(
        default=None, description="IANA timezone name used for interpreting schedule times, if any."
    )

    def next_after(self, when: datetime) -> datetime | None:
        """Compute the next run time after a given timestamp.

        Args:
            when: The reference timestamp (typically the current run time) to offset from.

        Returns:
            The next run timestamp, or None if `interval_s` is not set (one-shot job).
        """
        if self.interval_s is None:
            return None
        return when + timedelta(seconds=self.interval_s)


class JobDefinition(BaseModel):
    """A scheduled background job: what to run, for which session, and when."""

    job_id: str = Field(
        default_factory=lambda: secrets.token_hex(8), description="Unique identifier for this job."
    )
    session_id: str = Field(description="Session this job belongs to.")
    task_payload: dict[str, Any] = Field(
        default_factory=dict, description="Payload passed to the spawn callback when the job fires."
    )
    schedule: ScheduleConfig = Field(description="Recurrence rule controlling when the job is due.")
    delivery_policy: dict[str, Any] = Field(
        default_factory=dict, description="Policy controlling how results from this job are delivered."
    )
    enabled: bool = Field(default=True, description="Whether the job is eligible to run; disabled jobs are skipped.")
    created_at: datetime = Field(default_factory=_utc_now, description="When the job was created.")
    updated_at: datetime = Field(default_factory=_utc_now, description="When the job was last modified.")


class JobRunRecord(BaseModel):
    """A record of a single execution of a scheduled job."""

    job_id: str = Field(description="Identifier of the job this run belongs to.")
    run_id: str = Field(default_factory=lambda: secrets.token_hex(8), description="Unique identifier for this run.")
    started_at: datetime = Field(default_factory=_utc_now, description="When this run started.")
    completed_at: datetime | None = Field(default=None, description="When this run completed, if it has finished.")
    status: str | None = Field(default=None, description="Terminal status of the run, if known.")
    result: dict[str, Any] | None = Field(default=None, description="Result payload produced by the run, if any.")


class JobStore(Protocol):
    """Persistence contract for scheduled jobs and their run history.

    Implementations back `JobScheduler`'s polling loop; `InMemoryJobStore` is the
    default in-process implementation.
    """

    async def save_job(self, job: JobDefinition) -> None:
        """Create or update a job definition.

        Args:
            job: The job definition to persist.
        """
        ...

    async def list_jobs(self, session_id: str | None = None) -> list[JobDefinition]:
        """List known jobs, optionally filtered by session.

        Args:
            session_id: If given, only return jobs belonging to this session.

        Returns:
            The matching job definitions.
        """
        ...

    async def list_due(self, now: datetime) -> list[JobDefinition]:
        """List enabled jobs whose next run time has arrived.

        Args:
            now: The current timestamp to compare against each job's schedule.

        Returns:
            Jobs that are enabled and due to run at or before `now`.
        """
        ...

    async def record_run(self, run: JobRunRecord) -> None:
        """Persist a record of a job run.

        Args:
            run: The run record to store.
        """
        ...


class InMemoryJobStore(JobStore):
    """A process-local `JobStore` backed by in-memory dicts/lists, guarded by a lock.

    Intended for tests and single-process deployments; state is lost on restart.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobDefinition] = {}
        self._runs: list[JobRunRecord] = []
        self._lock = asyncio.Lock()

    async def save_job(self, job: JobDefinition) -> None:
        """Create or update a job definition, refreshing its `updated_at` timestamp.

        Args:
            job: The job definition to persist.
        """
        async with self._lock:
            job.updated_at = _utc_now()
            self._jobs[job.job_id] = job

    async def list_jobs(self, session_id: str | None = None) -> list[JobDefinition]:
        """List known jobs, optionally filtered by session.

        Args:
            session_id: If given, only return jobs belonging to this session.

        Returns:
            The matching job definitions.
        """
        async with self._lock:
            jobs = list(self._jobs.values())
        if session_id is None:
            return jobs
        return [job for job in jobs if job.session_id == session_id]

    async def list_due(self, now: datetime) -> list[JobDefinition]:
        """List enabled jobs whose next run time has arrived.

        Args:
            now: The current timestamp to compare against each job's schedule.

        Returns:
            Jobs that are enabled and have a `next_run_at` at or before `now`.
        """
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
        """Append a run record to the in-memory history.

        Args:
            run: The run record to store.
        """
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
        """Run one scheduling pass: spawn all due jobs and advance recurring schedules.

        For each job returned by `JobStore.list_due`, records a `JobRunRecord`, invokes
        the `spawn` callback, and if the job repeats, computes and persists its next
        run time.
        """
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
        """Start the background polling loop, if not already running.

        Idempotent: calling `start` while a loop task is already active is a no-op.
        """
        if self._task is not None and not self._task.done():
            return

        async def _loop() -> None:
            while True:
                await self._scheduler.tick()
                await asyncio.sleep(self._poll_interval_s)

        self._task = asyncio.create_task(_loop(), name="job-scheduler-loop")

    async def stop(self) -> None:
        """Cancel the background polling loop and wait for it to finish.

        Safe to call when no loop is running.
        """
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
