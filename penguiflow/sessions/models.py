"""Session/task models for bidirectional streaming and background work.

Most persistence-facing task/steering models live in `penguiflow.state.models`.
This module re-exports them for backward compatibility.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from penguiflow.state.models import (
    StateUpdate,
    TaskContextSnapshot,
    TaskState,
    TaskStateModel,
    TaskStatus,
    TaskType,
    UpdateType,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ContextPatch(BaseModel):
    task_id: str
    spawned_from_event_id: str | None = None
    source_context_version: int | None = None
    source_context_hash: str | None = None
    context_diverged: bool = False
    completed_at: datetime = Field(default_factory=_utc_now)
    digest: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class MergeStrategy(str, Enum):
    APPEND = "append"
    REPLACE = "replace"
    HUMAN_GATED = "human_gated"


class NotificationAction(BaseModel):
    id: str
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class NotificationPayload(BaseModel):
    severity: Literal["info", "warning", "error"] = "info"
    title: str
    body: str
    actions: list[NotificationAction] = Field(default_factory=list)


class ProactiveReportContext(BaseModel):
    """Context passed to the agent when generating a proactive report.

    Contains the completed task's results for the agent to summarize
    and potentially expand upon with artifacts.
    """

    task_id: str
    task_description: str | None = None
    digest: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: int | None = None
    context_diverged: bool = False
    merge_strategy: str = "APPEND"


class ProactiveReportRequest(BaseModel):
    """Request queued for proactive message generation after auto-merge.

    When a background task completes with APPEND/REPLACE merge strategy,
    this request is queued to trigger foreground agent report-back.
    """

    task_id: str
    session_id: str
    trace_id: str | None = None
    task_description: str | None = None
    execution_time_ms: int | None = None
    patch: ContextPatch
    merge_strategy: MergeStrategy
    queued_at: datetime = Field(default_factory=_utc_now)
    message_id: str = Field(default_factory=lambda: f"proactive_{secrets.token_hex(6)}")
    group_id: str | None = None

    memory_summary: dict[str, Any] = Field(default_factory=dict)
    tool_context: dict[str, Any] = Field(default_factory=dict)
    context_version: int | None = None
    context_hash: str | None = None
    proactive_hops_remaining: int | None = None

    is_group_report: bool = False
    group_task_ids: list[str] = Field(default_factory=list)
    combined_patches: list[ContextPatch] = Field(default_factory=list)


GroupReportStrategy = Literal["all", "any", "none"]
"""When to generate proactive report for a task group:
- 'all': When all tasks in sealed group complete (default for groups)
- 'any': On each task completion (current behavior, default for non-grouped)
- 'none': No proactive report (agent polls manually)
"""

GroupStatus = Literal["open", "sealed", "complete", "failed"]
"""Task group lifecycle states:
- 'open': Accepting new tasks
- 'sealed': No more tasks can join; waiting for completion
- 'complete': All tasks reached terminal state
- 'failed': Group failed (partial_on_failure=False and task failed)
"""


class TaskGroup(BaseModel):
    """A named collection of related background tasks for coordinated reporting.

    Task groups allow multiple background tasks to complete independently but
    report together, enabling cohesive synthesis instead of fragmented updates.

    Key invariants:
    - Per-task report suppression: tasks in a group don't emit individual proactive
      reports unless group_report='any' is explicitly chosen.
    - Stable identity via group_id: `name` is a display label; the runtime assigns
      a stable `group_id` for storage, UI, and approvals.
    - Turn-scoped name resolution: `group="name"` only joins an OPEN group created
      earlier in the same foreground turn; across turns, name reuse creates a new group.
    - Auto-seal: groups seal automatically when foreground yields (configurable).
    """

    group_id: str = Field(default_factory=lambda: f"grp_{secrets.token_hex(6)}")
    """Stable unique identifier for this group."""

    name: str
    """Display name for the group (not unique across turns)."""

    session_id: str
    """Session this group belongs to."""

    status: GroupStatus = "open"
    """Current lifecycle state."""

    merge_strategy: MergeStrategy = MergeStrategy.APPEND
    """How group results merge into context when complete."""

    report_strategy: GroupReportStrategy = "all"
    """When to generate proactive report."""

    task_ids: list[str] = Field(default_factory=list)
    """All task IDs in this group."""

    completed_task_ids: list[str] = Field(default_factory=list)
    """Task IDs that completed successfully."""

    failed_task_ids: list[str] = Field(default_factory=list)
    """Task IDs that failed or were cancelled."""

    created_at: datetime = Field(default_factory=_utc_now)
    """When the group was created."""

    sealed_at: datetime | None = None
    """When the group was sealed (no more tasks can join)."""

    completed_at: datetime | None = None
    """When the group reached terminal state."""

    retain_turn: bool = False
    """If True, foreground agent waits for group completion instead of yielding."""

    patches: list[str] = Field(default_factory=list)
    """Patch IDs produced by tasks in this group (for HUMAN_GATED bundling)."""

    report_queued: bool = False
    """Flag to ensure exactly-once report emission (idempotency)."""

    turn_id: str | None = None
    """Foreground turn ID when this group was created (for name resolution)."""

    @property
    def is_complete(self) -> bool:
        """Check if all tasks have reached terminal state and group is sealed/complete."""
        # A group can be "sealed" (waiting for tasks), "complete", or "failed"
        if self.status in ("complete", "failed"):
            return True
        if self.status != "sealed":
            return False
        terminal_count = len(self.completed_task_ids) + len(self.failed_task_ids)
        return terminal_count >= len(self.task_ids)

    @property
    def pending_task_ids(self) -> list[str]:
        """Task IDs that haven't reached terminal state."""
        terminal = set(self.completed_task_ids) | set(self.failed_task_ids)
        return [tid for tid in self.task_ids if tid not in terminal]


class GroupProactiveReportRequest(BaseModel):
    """Request for proactive report generation for a completed task group.

    Similar to ProactiveReportRequest but contains combined results from
    all tasks in the group for cohesive synthesis.
    """

    group_id: str
    session_id: str
    group_name: str
    trace_id: str | None = None
    task_count: int
    completed_count: int
    failed_count: int
    execution_time_ms: int | None = None
    combined_digest: list[str] = Field(default_factory=list)
    combined_facts: dict[str, Any] = Field(default_factory=dict)
    combined_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    combined_sources: list[dict[str, Any]] = Field(default_factory=list)
    merge_strategy: MergeStrategy
    queued_at: datetime = Field(default_factory=_utc_now)
    message_id: str = Field(default_factory=lambda: f"group_report_{secrets.token_hex(6)}")
    context_diverged: bool = False
    failed_task_summaries: list[dict[str, Any]] = Field(default_factory=list)
    """Summary info for failed tasks (task_id, error, description)."""


__all__ = [
    "ContextPatch",
    "GroupProactiveReportRequest",
    "GroupReportStrategy",
    "GroupStatus",
    "MergeStrategy",
    "NotificationAction",
    "NotificationPayload",
    "ProactiveReportContext",
    "ProactiveReportRequest",
    "StateUpdate",
    "TaskContextSnapshot",
    "TaskGroup",
    "TaskState",
    "TaskStateModel",
    "TaskStatus",
    "TaskType",
    "UpdateType",
]
