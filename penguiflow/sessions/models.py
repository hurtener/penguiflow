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
    """Structured summary of a completed background task's contribution to context.

    Produced when a background task finishes and needs to hand its results back to
    the foreground agent's context, either immediately (APPEND/REPLACE) or pending
    human approval (HUMAN_GATED).
    """

    task_id: str = Field(description="Identifier of the task that produced this patch.")
    spawned_from_event_id: str | None = Field(
        default=None, description="Event ID of the foreground turn that spawned the originating task, if any."
    )
    source_context_version: int | None = Field(
        default=None, description="Context version the task branched from, for divergence detection."
    )
    source_context_hash: str | None = Field(
        default=None, description="Hash of the context the task branched from, for divergence detection."
    )
    context_diverged: bool = Field(
        default=False, description="Whether the foreground context changed since the task was spawned."
    )
    completed_at: datetime = Field(default_factory=_utc_now, description="Timestamp when the task completed.")
    digest: list[str] = Field(default_factory=list, description="Short human-readable summary lines of the result.")
    facts: dict[str, Any] = Field(default_factory=dict, description="Structured facts extracted from the task run.")
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list, description="Artifacts (files, links, structured payloads) produced by the task."
    )
    sources: list[dict[str, Any]] = Field(
        default_factory=list, description="Source references (documents, URLs) consulted during the task."
    )
    recommended_next_steps: list[str] = Field(
        default_factory=list, description="Suggested follow-up actions surfaced by the task."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Assumptions the task made while producing its result."
    )


class MergeStrategy(str, Enum):
    """How a completed task's `ContextPatch` should be merged into foreground context.

    Attributes:
        APPEND: Add the patch to context alongside existing content.
        REPLACE: Replace the relevant portion of context with the patch.
        HUMAN_GATED: Hold the patch for human approval before merging.
    """

    APPEND = "append"
    REPLACE = "replace"
    HUMAN_GATED = "human_gated"


class NotificationAction(BaseModel):
    """A single actionable button/option attached to a `NotificationPayload`."""

    id: str = Field(description="Stable identifier for this action, used when the user selects it.")
    label: str = Field(description="Human-readable label shown to the user.")
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary data returned to the caller when this action is selected."
    )


class NotificationPayload(BaseModel):
    """Content of a user-facing notification, optionally with selectable actions."""

    severity: Literal["info", "warning", "error"] = Field(
        default="info", description="Notification severity used for display styling."
    )
    title: str = Field(description="Short notification title.")
    body: str = Field(description="Notification body text.")
    actions: list[NotificationAction] = Field(
        default_factory=list, description="Actions the user can take in response to this notification."
    )


class ProactiveReportContext(BaseModel):
    """Context passed to the agent when generating a proactive report.

    Contains the completed task's results for the agent to summarize
    and potentially expand upon with artifacts.
    """

    task_id: str = Field(description="Identifier of the completed task this report is about.")
    task_description: str | None = Field(default=None, description="Human-readable description of the task.")
    digest: list[str] = Field(default_factory=list, description="Short human-readable summary lines of the result.")
    facts: dict[str, Any] = Field(default_factory=dict, description="Structured facts extracted from the task run.")
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list, description="Artifacts (files, links, structured payloads) produced by the task."
    )
    sources: list[dict[str, Any]] = Field(
        default_factory=list, description="Source references (documents, URLs) consulted during the task."
    )
    execution_time_ms: int | None = Field(default=None, description="Wall-clock task execution time in milliseconds.")
    context_diverged: bool = Field(
        default=False, description="Whether the foreground context changed since the task was spawned."
    )
    merge_strategy: str = Field(default="APPEND", description="How the task result should merge into context.")


class ProactiveReportRequest(BaseModel):
    """Request queued for proactive message generation after auto-merge.

    When a background task completes with APPEND/REPLACE merge strategy,
    this request is queued to trigger foreground agent report-back.
    """

    task_id: str = Field(description="Identifier of the completed task this report is about.")
    session_id: str = Field(description="Session the task belongs to.")
    trace_id: str | None = Field(default=None, description="Trace ID of the task run, for correlation.")
    task_description: str | None = Field(default=None, description="Human-readable description of the task.")
    execution_time_ms: int | None = Field(default=None, description="Wall-clock task execution time in milliseconds.")
    patch: ContextPatch = Field(description="Context patch produced by the completed task.")
    merge_strategy: MergeStrategy = Field(description="How the patch should merge into foreground context.")
    queued_at: datetime = Field(default_factory=_utc_now, description="When this report request was queued.")
    message_id: str = Field(
        default_factory=lambda: f"proactive_{secrets.token_hex(6)}",
        description="Unique identifier for this report message.",
    )
    group_id: str | None = Field(
        default=None, description="Task group ID this report belongs to, if the task was part of a group."
    )

    memory_summary: dict[str, Any] = Field(
        default_factory=dict, description="Summary of memory writes made during the task."
    )
    tool_context: dict[str, Any] = Field(
        default_factory=dict, description="Tool-facing context accumulated during the task."
    )
    context_version: int | None = Field(default=None, description="Context version at the time of task completion.")
    context_hash: str | None = Field(default=None, description="Context hash at the time of task completion.")
    proactive_hops_remaining: int | None = Field(
        default=None, description="Remaining proactive report hops allowed before reports are suppressed."
    )

    is_group_report: bool = Field(
        default=False, description="Whether this request represents a combined report for a task group."
    )
    group_task_ids: list[str] = Field(
        default_factory=list, description="Task IDs included in this group report, when `is_group_report` is True."
    )
    combined_patches: list[ContextPatch] = Field(
        default_factory=list, description="Context patches from all tasks in the group, when `is_group_report` is True."
    )


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

    group_id: str = Field(description="Identifier of the task group this report is about.")
    session_id: str = Field(description="Session the task group belongs to.")
    group_name: str = Field(description="Display name of the task group.")
    trace_id: str | None = Field(default=None, description="Trace ID for correlation, if available.")
    task_count: int = Field(description="Total number of tasks in the group.")
    completed_count: int = Field(description="Number of tasks that completed successfully.")
    failed_count: int = Field(description="Number of tasks that failed or were cancelled.")
    execution_time_ms: int | None = Field(
        default=None, description="Wall-clock execution time for the group in milliseconds."
    )
    combined_digest: list[str] = Field(
        default_factory=list, description="Combined summary lines from all tasks in the group."
    )
    combined_facts: dict[str, Any] = Field(
        default_factory=dict, description="Combined structured facts from all tasks in the group."
    )
    combined_artifacts: list[dict[str, Any]] = Field(
        default_factory=list, description="Combined artifacts produced by tasks in the group."
    )
    combined_sources: list[dict[str, Any]] = Field(
        default_factory=list, description="Combined source references consulted by tasks in the group."
    )
    merge_strategy: MergeStrategy = Field(description="How the combined results should merge into context.")
    queued_at: datetime = Field(default_factory=_utc_now, description="When this report request was queued.")
    message_id: str = Field(
        default_factory=lambda: f"group_report_{secrets.token_hex(6)}",
        description="Unique identifier for this report message.",
    )
    context_diverged: bool = Field(
        default=False, description="Whether the foreground context changed since the group was created."
    )
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
