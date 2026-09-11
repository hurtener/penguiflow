"""Manual/offline worker for persisted learning-control-plane evaluation jobs."""

from __future__ import annotations

from dataclasses import dataclass

from .control_plane import LearningControlPlane
from .evaluation import Metric, RunOne


@dataclass(frozen=True, slots=True)
class WorkerRun:
    """A summary of one bounded pass over durable draft evaluation jobs."""

    attempted_job_ids: tuple[str, ...]
    ready_for_review_job_ids: tuple[str, ...]
    rejected_job_ids: tuple[str, ...]
    failed_job_ids: tuple[str, ...]


class OfflineEvaluationWorker:
    """Execute persisted draft jobs without participating in agent request handling."""

    def __init__(self, control_plane: LearningControlPlane, *, run_one: RunOne, metric: Metric) -> None:
        self._control_plane = control_plane
        self._run_one = run_one
        self._metric = metric

    async def run_pending(self, *, max_jobs: int | None = None) -> WorkerRun:
        """Evaluate a bounded, deterministic batch of jobs currently in draft state."""

        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")

        pending_jobs = self._control_plane.list_jobs(state="draft")
        if max_jobs is not None:
            pending_jobs = pending_jobs[:max_jobs]

        ready_for_review: list[str] = []
        rejected: list[str] = []
        failed: list[str] = []
        for pending_job in pending_jobs:
            completed_job = await self._control_plane.run_job(
                pending_job.job_id,
                self._run_one,
                self._metric,
            )
            if completed_job.state == "ready_for_review":
                ready_for_review.append(completed_job.job_id)
            elif completed_job.state == "rejected":
                rejected.append(completed_job.job_id)
            else:
                failed.append(completed_job.job_id)

        return WorkerRun(
            attempted_job_ids=tuple(job.job_id for job in pending_jobs),
            ready_for_review_job_ids=tuple(ready_for_review),
            rejected_job_ids=tuple(rejected),
            failed_job_ids=tuple(failed),
        )


__all__ = ["OfflineEvaluationWorker", "WorkerRun"]
