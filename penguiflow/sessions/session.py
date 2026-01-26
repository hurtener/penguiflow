"""Streaming session orchestration for bidirectional tasks."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from penguiflow.planner.trajectory import BackgroundTaskResult, extract_background_results
from penguiflow.state import StateStore
from penguiflow.steering import (
    MAX_STEERING_PAYLOAD_BYTES,
    SteeringCancelled,
    SteeringEvent,
    SteeringEventType,
    SteeringInbox,
    SteeringValidationError,
    sanitize_steering_event,
    validate_steering_event,
)

from .broker import UpdateBroker
from .models import (
    ContextPatch,
    GroupReportStrategy,
    GroupStatus,
    MergeStrategy,
    NotificationAction,
    NotificationPayload,
    ProactiveReportRequest,
    StateUpdate,
    TaskContextSnapshot,
    TaskGroup,
    TaskState,
    TaskStateModel,
    TaskStatus,
    TaskType,
    UpdateType,
    _utc_now,
)
from .persistence import InMemorySessionStateStore, SessionStateStore, StateStoreSessionAdapter
from .policy import ControlPolicy
from .registry import TaskRegistry
from .telemetry import NoOpTaskTelemetrySink, TaskTelemetryEvent, TaskTelemetrySink
from .transport import SessionConnection, Transport

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionContext:
    llm_context: dict[str, Any] = field(default_factory=dict)
    tool_context: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    background_results: dict[str, BackgroundTaskResult] = field(default_factory=dict)
    version: int = 0
    context_hash: str | None = None


@dataclass(slots=True)
class SessionLimits:
    max_tasks_per_session: int = 8
    max_background_tasks: int = 4
    max_concurrent_tasks: int = 3
    max_task_runtime_s: float | None = 900
    update_queue_size: int = 500
    steering_queue_size: int = 200
    max_pending_patches: int = 32
    max_steering_payload_bytes: int = MAX_STEERING_PAYLOAD_BYTES
    max_steering_events_per_task: int = 512


@dataclass(slots=True)
class PendingContextPatch:
    patch_id: str
    task_id: str
    patch: ContextPatch
    strategy: MergeStrategy
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class TaskResult:
    payload: Any | None = None
    context_patch: ContextPatch | None = None
    digest: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    notification: NotificationPayload | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _safe_deepcopy(value: Any) -> Any:
    if isinstance(value, dict):
        return _deepcopy_dict(value)
    if isinstance(value, list):
        return _deepcopy_list(value)
    if isinstance(value, tuple):
        return tuple(_safe_deepcopy(item) for item in value)
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _deepcopy_dict(value: dict[Any, Any]) -> dict[Any, Any]:
    return {key: _safe_deepcopy(item) for key, item in value.items()}


def _deepcopy_list(value: list[Any]) -> list[Any]:
    return [_safe_deepcopy(item) for item in value]


class TaskRuntime:
    """Runtime helpers exposed to task pipelines."""

    def __init__(
        self,
        *,
        session: StreamingSession,
        state: TaskState,
        steering: SteeringInbox,
        context_snapshot: TaskContextSnapshot,
    ) -> None:
        self.session = session
        self.state = state
        self.steering = steering
        self.context_snapshot = context_snapshot

    def emit_update(
        self,
        update_type: UpdateType,
        content: Any,
        *,
        step_index: int | None = None,
        total_steps: int | None = None,
    ) -> StateUpdate:
        if update_type == UpdateType.PROGRESS and isinstance(content, dict):
            self.state.progress = dict(content)
            asyncio.create_task(self.session.registry.update_task(self.state.task_id, progress=self.state.progress))
        update = StateUpdate(
            session_id=self.state.session_id,
            task_id=self.state.task_id,
            trace_id=self.state.trace_id,
            update_type=update_type,
            content=content,
            step_index=step_index,
            total_steps=total_steps,
        )
        self.session._publish(update)
        return update

    def notify(self, payload: NotificationPayload) -> None:
        self.emit_update(UpdateType.NOTIFICATION, payload.model_dump(mode="json"))


TaskPipeline = Callable[[TaskRuntime], Awaitable[TaskResult | Any]]


def _hash_context(payload: dict[str, Any]) -> str | None:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_PROACTIVE_HOPS_KEY = "proactive_hops_remaining"


def _coerce_proactive_hops(tool_context: dict[str, Any] | None) -> int | None:
    if not tool_context:
        return None
    value = tool_context.get(_PROACTIVE_HOPS_KEY)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _background_result_from_patch(
    patch: ContextPatch,
    *,
    payload: Any | None,
    group_id: str | None,
) -> BackgroundTaskResult:
    summary = " ".join(patch.digest) if patch.digest else None
    completed_at = None
    if hasattr(patch, "completed_at") and patch.completed_at is not None:
        try:
            completed_at = patch.completed_at.timestamp()
        except Exception:
            completed_at = None
    return BackgroundTaskResult(
        task_id=patch.task_id,
        group_id=group_id,
        status="completed",
        summary=summary,
        payload=payload,
        facts=dict(patch.facts or {}),
        artifacts=[dict(item) for item in patch.artifacts or []],
        consumed=False,
        completed_at=completed_at if completed_at is not None else time.time(),
    )


def _background_results_payload(results: dict[str, BackgroundTaskResult]) -> list[dict[str, Any]]:
    return [result.to_payload() for result in results.values()]


class StreamingSession:
    """Manages bidirectional communication and task lifecycle."""

    def __init__(
        self,
        session_id: str,
        *,
        control_policy: ControlPolicy | None = None,
        limits: SessionLimits | None = None,
        state_store: SessionStateStore | StateStore | None = None,
        telemetry_sink: TaskTelemetrySink | None = None,
    ) -> None:
        self.session_id = session_id
        self._limits = limits or SessionLimits()
        self._state_store: SessionStateStore
        if state_store is None:
            self._state_store = InMemorySessionStateStore()
        elif hasattr(state_store, "save_task"):
            self._state_store = cast(SessionStateStore, state_store)
        elif hasattr(state_store, "save_event") and hasattr(state_store, "load_history"):
            self._state_store = StateStoreSessionAdapter(cast(StateStore, state_store))
        else:  # pragma: no cover - defensive
            raise TypeError("state_store must implement SessionStateStore or StateStore")
        self._registry = TaskRegistry(persist_task=self._state_store.save_task)
        self._broker = UpdateBroker(max_queue_size=self._limits.update_queue_size)
        self._steering_inboxes: dict[str, SteeringInbox] = {}
        self._task_handles: dict[str, asyncio.Task[None]] = {}
        self._pending_controls: dict[str, SteeringEvent] = {}
        self._pending_patches: dict[str, PendingContextPatch] = {}
        self._seen_event_ids: dict[str, deque[str]] = {}
        self._concurrency_semaphore = (
            asyncio.Semaphore(self._limits.max_concurrent_tasks) if self._limits.max_concurrent_tasks > 0 else None
        )
        self._control_policy = control_policy or ControlPolicy()
        self._telemetry = telemetry_sink or NoOpTaskTelemetrySink()
        self._context = SessionContext()
        self._foreground_task_id: str | None = None
        self._hydrated = False
        # Proactive report-back infrastructure
        self._proactive_queue: asyncio.Queue[ProactiveReportRequest] = asyncio.Queue()
        self._group_report_queue: asyncio.Queue[ProactiveReportRequest] = asyncio.Queue()
        self._foreground_busy = asyncio.Event()
        self._foreground_busy.set()  # Initially idle
        self._proactive_task: asyncio.Task[None] | None = None
        self._proactive_generator: Callable[[ProactiveReportRequest], Awaitable[None]] | None = None
        self._group_report_generator: Callable[[ProactiveReportRequest], Awaitable[None]] | None = None
        self._proactive_config: dict[str, Any] | None = None
        # Task group state
        self._groups: dict[str, TaskGroup] = {}
        self._current_turn_id: str | None = None
        self._group_completion_events: dict[str, asyncio.Event] = {}

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    @property
    def limits(self) -> SessionLimits:
        return self._limits

    @property
    def pending_patches(self) -> dict[str, PendingContextPatch]:
        return dict(self._pending_patches)

    @property
    def context_version(self) -> int:
        return self._context.version

    @property
    def context_hash(self) -> str | None:
        return self._context.context_hash

    def get_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return dict(self._context.llm_context), dict(self._context.tool_context)

    def get_background_results(self) -> dict[str, BackgroundTaskResult]:
        return dict(self._context.background_results)

    async def connect(self, transport: Transport) -> SessionConnection:
        return SessionConnection(self, transport)

    async def hydrate(self) -> None:
        if self._hydrated:
            return
        tasks = await self._state_store.list_tasks(self.session_id)
        if tasks:
            await self._registry.seed_tasks(list(tasks))
            foreground = [task for task in tasks if task.task_type == TaskType.FOREGROUND]
            if foreground:
                self._foreground_task_id = foreground[-1].task_id
        self._hydrated = True

    def _publish(self, update: StateUpdate) -> None:
        self._broker.publish(update)
        asyncio.create_task(self._state_store.save_update(update))

    def update_context(
        self,
        *,
        llm_context: dict[str, Any] | None = None,
        tool_context: dict[str, Any] | None = None,
    ) -> None:
        if llm_context is not None:
            cleaned, extracted = extract_background_results(llm_context)
            self._context.llm_context = dict(cleaned or {})
            if extracted:
                self._context.background_results.update(extracted)
        if tool_context is not None:
            self._context.tool_context = dict(tool_context)
        self._context.version += 1
        self._context.context_hash = _hash_context(self._context.llm_context)

    def _migrate_legacy_background_results(self) -> None:
        cleaned, extracted = extract_background_results(self._context.llm_context)
        if not extracted:
            return
        self._context.background_results.update(extracted)
        self._context.llm_context = dict(cleaned or {})
        self._context.version += 1
        self._context.context_hash = _hash_context(self._context.llm_context)

    def configure_proactive_reporting(
        self,
        *,
        generator: Callable[[ProactiveReportRequest], Awaitable[None]],
        enabled: bool = False,
        strategies: list[str] | None = None,
        max_queued: int = 5,
        timeout_s: float = 30.0,
        max_hops: int = 2,
        fallback_notification: bool = True,
    ) -> None:
        """Configure proactive report-back for background task completions.

        Args:
            generator: Async callable to generate proactive messages.
            enabled: Master switch for proactive reporting.
            strategies: Merge strategies that trigger reports (default: APPEND, REPLACE).
            max_queued: Maximum queued reports before dropping oldest.
            timeout_s: Timeout for proactive message generation.
            max_hops: Maximum proactive recursion hops before disabling background spawning.
            fallback_notification: Fall back to notification panel if generation fails.
        """
        self._proactive_generator = generator
        self._proactive_config = {
            "enabled": enabled,
            "strategies": strategies or ["APPEND", "REPLACE"],
            "max_queued": max_queued,
            "timeout_s": timeout_s,
            "max_hops": max_hops,
            "fallback_notification": fallback_notification,
        }
        if enabled and self._proactive_task is None:
            self._proactive_task = asyncio.create_task(
                self._run_proactive_reporter(),
                name=f"proactive_reporter:{self.session_id}",
            )

    def _enqueue_proactive_report(
        self,
        *,
        task_id: str,
        trace_id: str | None,
        description: str | None,
        execution_time_ms: int,
        patch: ContextPatch,
        merge_strategy: MergeStrategy,
        group_id: str | None = None,
        is_group_report: bool = False,
        group_task_ids: list[str] | None = None,
        combined_patches: list[ContextPatch] | None = None,
        proactive_hops_remaining: int | None = None,
    ) -> None:
        """Queue a proactive report request for generation when foreground is idle."""
        config = self._proactive_config
        if config is None or not config.get("enabled"):
            return
        # Check if merge strategy triggers proactive reporting
        strategy_name = merge_strategy.value.upper()
        allowed_strategies = config.get("strategies", ["APPEND", "REPLACE"])
        if strategy_name not in allowed_strategies:
            return
        max_hops = int(config.get("max_hops", 2) or 0)
        hops_remaining = int(proactive_hops_remaining) if proactive_hops_remaining is not None else max_hops
        if hops_remaining <= 0:
            return
        # Enforce queue size limit (drop oldest if full)
        max_queued = config.get("max_queued", 5)
        while self._proactive_queue.qsize() >= max_queued:
            try:
                self._proactive_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        request = ProactiveReportRequest(
            task_id=task_id,
            session_id=self.session_id,
            trace_id=trace_id,
            task_description=description,
            execution_time_ms=execution_time_ms,
            patch=patch,
            merge_strategy=merge_strategy,
            group_id=group_id,
            memory_summary=dict(self._context.memory),
            tool_context=dict(self._context.tool_context),
            context_version=self._context.version,
            context_hash=self._context.context_hash,
            proactive_hops_remaining=hops_remaining,
            is_group_report=is_group_report,
            group_task_ids=list(group_task_ids or []),
            combined_patches=list(combined_patches or []),
        )
        self._proactive_queue.put_nowait(request)

    async def _run_proactive_reporter(self) -> None:
        """Background task that processes proactive reports when foreground is idle."""
        while True:
            try:
                # Wait for a report request
                request = await self._proactive_queue.get()
                # Wait for foreground to be idle
                await self._foreground_busy.wait()
                # Generate proactive message
                await self._generate_proactive_message(request)
            except asyncio.CancelledError:
                break
            except Exception:
                # Log error and continue processing
                pass

    async def _generate_proactive_message(self, request: ProactiveReportRequest) -> None:
        """Generate a proactive message for a completed background task."""
        config = self._proactive_config
        if config is None or self._proactive_generator is None:
            return
        timeout_s = config.get("timeout_s", 30.0)
        fallback = config.get("fallback_notification", True)
        try:
            await asyncio.wait_for(
                self._proactive_generator(request),
                timeout=timeout_s,
            )
        except TimeoutError:
            if fallback:
                self._publish(
                    StateUpdate(
                        session_id=self.session_id,
                        task_id=request.task_id,
                        trace_id=request.trace_id,
                        update_type=UpdateType.NOTIFICATION,
                        content={
                            "severity": "info",
                            "title": "Background task complete",
                            "body": "Task completed but proactive report timed out.",
                            "task_id": request.task_id,
                        },
                    )
                )
        except Exception:
            if fallback:
                self._publish(
                    StateUpdate(
                        session_id=self.session_id,
                        task_id=request.task_id,
                        trace_id=request.trace_id,
                        update_type=UpdateType.NOTIFICATION,
                        content={
                            "severity": "warning",
                            "title": "Background task complete",
                            "body": "Task completed but failed to generate proactive report.",
                            "task_id": request.task_id,
                        },
                    )
                )

    async def _ensure_limits(self, task_type: TaskType) -> None:
        tasks = await self._registry.list_tasks(self.session_id)
        # Only count active tasks (not COMPLETE, FAILED, CANCELLED) towards limits
        terminal_statuses = {TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.CANCELLED}
        active_tasks = [t for t in tasks if t.status not in terminal_statuses]
        if self._limits.max_tasks_per_session and len(active_tasks) >= self._limits.max_tasks_per_session:
            raise RuntimeError("task_limit_exceeded")
        if task_type == TaskType.BACKGROUND and self._limits.max_background_tasks:
            background_count = len([t for t in active_tasks if t.task_type == TaskType.BACKGROUND])
            if background_count >= self._limits.max_background_tasks:
                raise RuntimeError("background_task_limit_exceeded")

    async def ensure_capacity(self, task_type: TaskType) -> None:
        await self._ensure_limits(task_type)

    async def spawn_task(
        self,
        pipeline: TaskPipeline,
        *,
        task_type: TaskType = TaskType.FOREGROUND,
        priority: int = 0,
        context_snapshot: TaskContextSnapshot | None = None,
        description: str | None = None,
        spawn_reason: str | None = None,
        parent_task_id: str | None = None,
        spawned_from_event_id: str | None = None,
        query: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        merge_strategy: MergeStrategy = MergeStrategy.APPEND,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
        group_id: str | None = None,
    ) -> str:
        await self._ensure_limits(task_type)
        task_id = task_id or secrets.token_hex(8)
        snapshot = context_snapshot or self._build_snapshot(
            task_id=task_id,
            trace_id=trace_id,
            spawn_reason=spawn_reason,
            task_type=task_type,
            query=query,
            parent_task_id=parent_task_id,
            spawned_from_event_id=spawned_from_event_id,
            propagate_on_cancel=propagate_on_cancel,
            notify_on_complete=notify_on_complete,
        )
        if context_snapshot is not None:
            snapshot = context_snapshot.model_copy(
                update={
                    "session_id": self.session_id,
                    "task_id": task_id,
                    "propagate_on_cancel": propagate_on_cancel,
                    "notify_on_complete": notify_on_complete,
                }
            )
        state = await self._registry.create_task(
            session_id=self.session_id,
            task_type=task_type,
            priority=priority,
            context_snapshot=snapshot,
            description=description,
            trace_id=trace_id,
            task_id=task_id,
        )
        self._emit_status_change(state, reason="created")
        asyncio.create_task(
            self._telemetry.emit(
                TaskTelemetryEvent(
                    event_type="task_spawned",
                    outcome="spawned",
                    session_id=self.session_id,
                    task_id=state.task_id,
                    parent_task_id=state.context_snapshot.spawned_from_task_id,
                    trace_id=state.trace_id,
                    task_type=state.task_type,
                    status=state.status,
                    mode="subagent" if state.task_type == TaskType.BACKGROUND else "foreground",
                    spawn_reason=state.context_snapshot.spawn_reason,
                )
            )
        )
        if task_type == TaskType.FOREGROUND:
            self._foreground_task_id = task_id
            self._foreground_busy.clear()  # Mark foreground as busy
        steering = SteeringInbox(maxsize=self._limits.steering_queue_size)
        self._steering_inboxes[task_id] = steering
        handle = asyncio.create_task(
            self._run_task(
                pipeline=pipeline,
                state=state,
                steering=steering,
                merge_strategy=merge_strategy,
                group_id=group_id,
            ),
            name=f"task:{task_id}",
        )
        self._task_handles[task_id] = handle
        return task_id

    async def run_task(
        self,
        pipeline: TaskPipeline,
        *,
        task_type: TaskType = TaskType.FOREGROUND,
        priority: int = 0,
        context_snapshot: TaskContextSnapshot | None = None,
        description: str | None = None,
        spawn_reason: str | None = None,
        parent_task_id: str | None = None,
        spawned_from_event_id: str | None = None,
        query: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        merge_strategy: MergeStrategy = MergeStrategy.APPEND,
        propagate_on_cancel: Literal["cascade", "isolate"] = "cascade",
        notify_on_complete: bool = True,
    ) -> TaskResult:
        await self._ensure_limits(task_type)
        task_id = task_id or secrets.token_hex(8)
        snapshot = context_snapshot or self._build_snapshot(
            task_id=task_id,
            trace_id=trace_id,
            spawn_reason=spawn_reason,
            task_type=task_type,
            query=query,
            parent_task_id=parent_task_id,
            spawned_from_event_id=spawned_from_event_id,
            propagate_on_cancel=propagate_on_cancel,
            notify_on_complete=notify_on_complete,
        )
        if context_snapshot is not None:
            snapshot = context_snapshot.model_copy(
                update={
                    "session_id": self.session_id,
                    "task_id": task_id,
                    "propagate_on_cancel": propagate_on_cancel,
                    "notify_on_complete": notify_on_complete,
                }
            )
        state = await self._registry.create_task(
            session_id=self.session_id,
            task_type=task_type,
            priority=priority,
            context_snapshot=snapshot,
            description=description,
            trace_id=trace_id,
            task_id=task_id,
        )
        self._emit_status_change(state, reason="created")
        asyncio.create_task(
            self._telemetry.emit(
                TaskTelemetryEvent(
                    event_type="task_spawned",
                    outcome="spawned",
                    session_id=self.session_id,
                    task_id=state.task_id,
                    parent_task_id=state.context_snapshot.spawned_from_task_id,
                    trace_id=state.trace_id,
                    task_type=state.task_type,
                    status=state.status,
                    mode="subagent" if state.task_type == TaskType.BACKGROUND else "foreground",
                    spawn_reason=state.context_snapshot.spawn_reason,
                )
            )
        )
        if task_type == TaskType.FOREGROUND:
            self._foreground_task_id = task_id
            self._foreground_busy.clear()  # Mark foreground as busy
        steering = SteeringInbox(maxsize=self._limits.steering_queue_size)
        self._steering_inboxes[task_id] = steering
        try:
            return await self._execute_task(
                pipeline=pipeline,
                state=state,
                steering=steering,
                merge_strategy=merge_strategy,
            )
        finally:
            self._steering_inboxes.pop(task_id, None)
            if task_type == TaskType.FOREGROUND:
                self._foreground_busy.set()  # Mark foreground as idle

    async def steer(self, event: SteeringEvent) -> bool:
        if event.session_id != self.session_id:
            return False
        event = sanitize_steering_event(event, max_payload_bytes=self._limits.max_steering_payload_bytes)
        try:
            validate_steering_event(event)
        except SteeringValidationError:
            return False
        if self._is_duplicate_event(event.task_id, event.event_id):
            return False
        await self._state_store.save_steering(event)

        if event.event_type in {SteeringEventType.APPROVE, SteeringEventType.REJECT}:
            patch_id = event.payload.get("patch_id")
            if isinstance(patch_id, str) and patch_id in self._pending_patches:
                if event.event_type == SteeringEventType.REJECT:
                    await self._reject_pending_patch(patch_id, event)
                    return True
                await self.apply_pending_patch(patch_id=patch_id)
                return True

        if event.event_type in {SteeringEventType.APPROVE, SteeringEventType.REJECT}:
            token = event.payload.get("resume_token") or event.payload.get("event_id")
            if not isinstance(token, str):
                return False
            pending = self._pending_controls.pop(token, None)
            if pending is not None:
                if event.event_type == SteeringEventType.REJECT:
                    self._publish(
                        StateUpdate(
                            session_id=self.session_id,
                            task_id=pending.task_id,
                            trace_id=pending.trace_id,
                            update_type=UpdateType.NOTIFICATION,
                            content={
                                "severity": "warning",
                                "title": "Action rejected",
                                "body": f"Rejected {pending.event_type.value.lower()} request.",
                            },
                        )
                    )
                    return False
                confirmed_payload = dict(pending.payload)
                confirmed_payload["confirmed"] = True
                pending = pending.model_copy(update={"payload": confirmed_payload, "source": "user"})
                return await self.steer(pending)

        if self._control_policy.requires_confirmation(event):
            self._pending_controls[event.event_id] = event
            checkpoint = StateUpdate(
                session_id=self.session_id,
                task_id=event.task_id,
                trace_id=event.trace_id,
                update_type=UpdateType.CHECKPOINT,
                content={
                    "kind": "approval_required",
                    "resume_token": event.event_id,
                    "prompt": f"Confirm {event.event_type.value.lower()}?",
                    "options": ["approve", "reject"],
                },
            )
            self._publish(checkpoint)
            return False

        task = await self._registry.get_task(event.task_id)
        if task is None:
            return False

        if event.event_type == SteeringEventType.PRIORITIZE:
            priority_val = event.payload.get("priority")
            if isinstance(priority_val, int):
                await self._registry.update_priority(event.task_id, priority_val)
                self._emit_status_change(task, reason="priority_changed")
            return True

        handled = False
        if event.event_type == SteeringEventType.PAUSE:
            await self._registry.update_status(event.task_id, TaskStatus.PAUSED)
            self._emit_status_change(task, reason="paused")
            handled = True

        if event.event_type == SteeringEventType.RESUME:
            await self._registry.update_status(event.task_id, TaskStatus.RUNNING)
            self._emit_status_change(task, reason="resumed")
            handled = True

        if event.event_type == SteeringEventType.CANCEL:
            await self._registry.update_status(event.task_id, TaskStatus.CANCELLED)
            self._emit_status_change(task, reason="cancel_requested")
            handle = self._task_handles.get(event.task_id)
            if handle is not None and not handle.done():
                handle.cancel()
            await self._cascade_cancel_children(
                parent_task_id=event.task_id,
                reason=str(event.payload.get("reason") or "parent_cancelled"),
            )
            handled = True

        inbox = self._steering_inboxes.get(event.task_id)
        if inbox is None:
            return handled
        accepted = await inbox.push(event)
        if accepted:
            self._publish(
                StateUpdate(
                    session_id=self.session_id,
                    task_id=event.task_id,
                    trace_id=event.trace_id,
                    update_type=UpdateType.STATUS_CHANGE,
                    content={
                        "status": "STEERING_RECEIVED",
                        "event_type": event.event_type.value,
                        "event_id": event.event_id,
                    },
                )
            )
        return accepted

    async def get_task(self, task_id: str) -> TaskState | None:
        return await self._registry.get_task(task_id)

    async def list_tasks(self, *, status: TaskStatus | None = None) -> list[TaskState]:
        return await self._registry.list_tasks(self.session_id, status=status)

    async def cancel_task(self, task_id: str, *, reason: str | None = None) -> bool:
        return await self.steer(
            SteeringEvent(
                session_id=self.session_id,
                task_id=task_id,
                event_type=SteeringEventType.CANCEL,
                payload={"reason": reason} if reason else {},
                source="user",
            )
        )

    async def broadcast_steer(
        self,
        *,
        event_type: SteeringEventType,
        payload: dict[str, Any] | None = None,
        source: str = "user",
    ) -> int:
        tasks = await self._registry.list_active(self.session_id)
        count = 0
        for task in tasks:
            ok = await self.steer(
                SteeringEvent(
                    session_id=self.session_id,
                    task_id=task.task_id,
                    event_type=event_type,
                    payload=dict(payload or {}),
                    source=source,
                )
            )
            if ok:
                count += 1
        return count

    async def list_updates(
        self,
        *,
        task_id: str | None = None,
        since_id: str | None = None,
        limit: int = 500,
    ) -> list[StateUpdate]:
        updates = await self._state_store.list_updates(
            self.session_id,
            task_id=task_id,
            since_id=since_id,
            limit=limit,
        )
        return list(updates)

    def _is_duplicate_event(self, task_id: str, event_id: str) -> bool:
        seen = self._seen_event_ids.get(task_id)
        if seen is None:
            seen = deque(maxlen=self._limits.max_steering_events_per_task)
            self._seen_event_ids[task_id] = seen
        if event_id in seen:
            return True
        seen.append(event_id)
        return False

    async def subscribe(
        self,
        *,
        task_ids: list[str] | None = None,
        update_types: list[UpdateType] | None = None,
        since_id: str | None = None,
    ) -> AsyncIterator[StateUpdate]:
        queue, unsubscribe = await self._broker.subscribe(
            task_ids=task_ids,
            update_types=update_types,
        )

        task_id_set = set(task_ids) if task_ids else None
        update_type_set = set(update_types) if update_types else None

        def _matches_filter(update: StateUpdate) -> bool:
            if task_id_set is not None and update.task_id not in task_id_set:
                return False
            if update_type_set is not None and update.update_type not in update_type_set:
                return False
            return True

        async def _iterator() -> AsyncIterator[StateUpdate]:
            try:
                if since_id is not None:
                    replay = await self._state_store.list_updates(
                        self.session_id,
                        task_id=task_ids[0] if task_ids and len(task_ids) == 1 else None,
                        since_id=since_id,
                    )
                    for update in replay:
                        if _matches_filter(update):
                            yield update
                while True:
                    update = await queue.get()
                    if _matches_filter(update):
                        yield update
            finally:
                await unsubscribe()

        return _iterator()

    async def apply_context_patch(
        self,
        *,
        patch: ContextPatch,
        strategy: MergeStrategy = MergeStrategy.APPEND,
        payload: Any | None = None,
        group_id: str | None = None,
    ) -> str | None:
        diverged = False
        if patch.source_context_version is not None and patch.source_context_version != self._context.version:
            diverged = True
        if patch.source_context_hash and patch.source_context_hash != self._context.context_hash:
            diverged = True
        if diverged and not patch.context_diverged:
            patch = patch.model_copy(update={"context_diverged": True})

        if strategy == MergeStrategy.HUMAN_GATED:
            if len(self._pending_patches) >= self._limits.max_pending_patches:
                raise RuntimeError("pending_patch_limit_exceeded")
            patch_id = secrets.token_hex(8)
            self._pending_patches[patch_id] = PendingContextPatch(
                patch_id=patch_id,
                task_id=patch.task_id,
                patch=patch,
                strategy=strategy,
            )
            self._publish(
                StateUpdate(
                    session_id=self.session_id,
                    task_id=patch.task_id,
                    trace_id=None,
                    update_type=UpdateType.CHECKPOINT,
                    content={
                        "kind": "context_patch",
                        "patch_id": patch_id,
                        "task_id": patch.task_id,
                        "digest": patch.digest,
                        "diverged": patch.context_diverged,
                        "prompt": "Apply background results to the conversation?",
                        "options": ["approve", "reject"],
                    },
                )
            )
            return patch_id

        if strategy in {MergeStrategy.APPEND, MergeStrategy.REPLACE}:
            result = _background_result_from_patch(patch, payload=payload, group_id=group_id)
            self._context.background_results[result.task_id] = result
        if diverged:
            self._publish(
                StateUpdate(
                    session_id=self.session_id,
                    task_id=patch.task_id,
                    trace_id=None,
                    update_type=UpdateType.NOTIFICATION,
                    content={
                        "severity": "warning",
                        "title": "Context changed",
                        "body": "Background results were produced from an older context.",
                    },
                )
            )
        return None

    async def mark_background_consumed(self, *, task_ids: list[str]) -> int:
        """Remove background task results from session context by task_id."""
        if not task_ids:
            return 0
        task_id_set = {task_id for task_id in task_ids if task_id}
        if not task_id_set:
            return 0
        removed = 0
        legacy_removed = False
        for task_id in list(task_id_set):
            if task_id in self._context.background_results:
                del self._context.background_results[task_id]
                removed += 1
        background_results = self._context.llm_context.get("background_results")
        if isinstance(background_results, list):
            original_len = len(background_results)
            remaining = [
                item
                for item in background_results
                if not (isinstance(item, dict) and item.get("task_id") in task_id_set)
            ]
            removed += original_len - len(remaining)
            if remaining:
                self._context.llm_context["background_results"] = remaining
            else:
                self._context.llm_context.pop("background_results", None)
            legacy_removed = legacy_removed or (original_len != len(remaining))
        background_result = self._context.llm_context.get("background_result")
        if isinstance(background_result, dict) and background_result.get("task_id") in task_id_set:
            self._context.llm_context.pop("background_result", None)
            removed += 1
            legacy_removed = True
        if legacy_removed:
            self._context.version += 1
            self._context.context_hash = _hash_context(self._context.llm_context)
        return removed

    async def apply_pending_patch(
        self,
        *,
        patch_id: str,
        strategy: MergeStrategy | None = None,
    ) -> bool:
        pending = self._pending_patches.pop(patch_id, None)
        if pending is None:
            return False
        await self.apply_context_patch(
            patch=pending.patch,
            strategy=strategy or MergeStrategy.APPEND,
        )
        self._publish(
            StateUpdate(
                session_id=self.session_id,
                task_id=pending.task_id,
                trace_id=None,
                update_type=UpdateType.NOTIFICATION,
                content={
                    "severity": "info",
                    "title": "Context updated",
                    "body": "Background results applied to the conversation.",
                },
            )
        )
        return True

    async def _reject_pending_patch(self, patch_id: str, event: SteeringEvent) -> None:
        pending = self._pending_patches.pop(patch_id, None)
        if pending is None:
            return
        self._publish(
            StateUpdate(
                session_id=self.session_id,
                task_id=pending.task_id,
                trace_id=event.trace_id,
                update_type=UpdateType.NOTIFICATION,
                content={
                    "severity": "warning",
                    "title": "Context patch rejected",
                    "body": "Background results were not applied.",
                },
            )
        )

    async def _run_task(
        self,
        *,
        pipeline: TaskPipeline,
        state: TaskState,
        steering: SteeringInbox,
        merge_strategy: MergeStrategy,
        group_id: str | None = None,
    ) -> None:
        try:
            await self._execute_task(
                pipeline=pipeline,
                state=state,
                steering=steering,
                merge_strategy=merge_strategy,
                group_id=group_id,
            )
        except asyncio.CancelledError:
            await self._registry.update_task(state.task_id, status=TaskStatus.CANCELLED, error="cancelled")
            self._emit_status_change(state, reason="cancelled")
            # Mark task as failed in group if applicable
            if group_id is not None:
                await self.mark_task_complete_in_group(group_id, state.task_id, success=False)
            asyncio.create_task(
                self._telemetry.emit(
                    TaskTelemetryEvent(
                        event_type="task_cancelled",
                        outcome="cancelled",
                        session_id=self.session_id,
                        task_id=state.task_id,
                        parent_task_id=state.context_snapshot.spawned_from_task_id,
                        trace_id=state.trace_id,
                        task_type=state.task_type,
                        status=TaskStatus.CANCELLED,
                        mode="job" if (state.context_snapshot.spawn_reason or "").startswith("tool:") else "subagent",
                        spawn_reason=state.context_snapshot.spawn_reason,
                        extra={"reason": "cancelled"},
                    )
                )
            )
            raise
        finally:
            self._steering_inboxes.pop(state.task_id, None)
            handle = self._task_handles.pop(state.task_id, None)
            current = asyncio.current_task()
            if handle is not None and handle is not current and not handle.done():
                handle.cancel()

    async def close(self) -> None:
        # Cancel proactive reporter task
        if self._proactive_task is not None and not self._proactive_task.done():
            self._proactive_task.cancel()
            self._proactive_task = None
        for task_id, handle in list(self._task_handles.items()):
            if not handle.done():
                handle.cancel()
            await self._registry.update_task(task_id, status=TaskStatus.CANCELLED, error="session_closed")
        self._task_handles.clear()
        self._steering_inboxes.clear()

    # -------------------------------------------------------------------------
    # Task Group Methods
    # -------------------------------------------------------------------------

    async def resolve_or_create_group(
        self,
        *,
        group_name: str | None = None,
        group_id: str | None = None,
        turn_id: str | None = None,
        merge_strategy: MergeStrategy | None = None,
        report_strategy: GroupReportStrategy | None = None,
        retain_turn: bool = False,
    ) -> TaskGroup:
        """Resolve an existing group or create a new one based on name/ID.

        Turn-scoped name resolution:
        - If group_id provided, join that exact group (error if not found/joinable)
        - If group_name provided, resolve to OPEN group with that name created
          in the same turn, or create new group if none exists
        """
        # If explicit group_id provided, look it up directly
        if group_id is not None:
            existing = self._groups.get(group_id)
            if existing is None:
                raise RuntimeError(f"group_not_found: {group_id}")
            if existing.status != "open":
                raise RuntimeError(f"group_not_joinable: {group_id} is {existing.status}")
            return existing

        # Turn-scoped name resolution: find OPEN group with matching name + turn_id
        if group_name is not None:
            for grp in self._groups.values():
                if grp.name == group_name and grp.status == "open" and grp.turn_id == turn_id:
                    return grp

        # Create new group
        new_group = TaskGroup(
            name=group_name or f"group_{secrets.token_hex(4)}",
            session_id=self.session_id,
            merge_strategy=merge_strategy or MergeStrategy.APPEND,
            report_strategy=report_strategy or "all",
            retain_turn=retain_turn,
            turn_id=turn_id,
        )
        self._groups[new_group.group_id] = new_group
        return new_group

    async def add_task_to_group(self, group_id: str, task_id: str) -> bool:
        """Add a task to a group. Returns False if group not found or not open."""
        group = self._groups.get(group_id)
        if group is None:
            return False
        if group.status != "open":
            return False
        if task_id not in group.task_ids:
            group.task_ids.append(task_id)
        return True

    async def seal_group(self, group_id: str) -> bool:
        """Seal a group (no more tasks can join). Returns False if already sealed."""
        group = self._groups.get(group_id)
        if group is None:
            return False
        if group.status != "open":
            return False
        group.status = "sealed"
        group.sealed_at = _utc_now()
        # Check if already complete (all tasks finished before seal)
        await self._check_group_completion(group_id)
        return True

    async def get_group(
        self,
        group_id: str | None = None,
        *,
        group_name: str | None = None,
        turn_id: str | None = None,
    ) -> TaskGroup | None:
        """Get a task group by ID or by name+turn resolution.

        Args:
            group_id: Direct lookup by group ID
            group_name: Name-based lookup (requires turn_id for disambiguation)
            turn_id: Turn ID for name-based lookup
        """
        # Direct lookup by ID
        if group_id is not None:
            return self._groups.get(group_id)
        # Name-based lookup
        if group_name is not None:
            for grp in self._groups.values():
                if grp.name == group_name:
                    # If turn_id specified, match it; otherwise return first match
                    if turn_id is None or grp.turn_id == turn_id:
                        return grp
        return None

    async def list_groups(self, *, status: GroupStatus | None = None) -> list[TaskGroup]:
        """List all task groups, optionally filtered by status."""
        groups = list(self._groups.values())
        if status is not None:
            groups = [g for g in groups if g.status == status]
        return groups

    async def mark_task_complete_in_group(
        self,
        group_id: str,
        task_id: str,
        *,
        success: bool,
        patch: ContextPatch | None = None,
        patch_id: str | None = None,
    ) -> None:
        """Mark a task as complete (success or failure) within its group."""
        group = self._groups.get(group_id)
        if group is None:
            return
        if success:
            if task_id not in group.completed_task_ids:
                group.completed_task_ids.append(task_id)
        else:
            if task_id not in group.failed_task_ids:
                group.failed_task_ids.append(task_id)
        # Track patch for bundled approval if HUMAN_GATED
        if patch_id is not None and patch_id not in group.patches:
            group.patches.append(patch_id)
        # Check completion
        await self._check_group_completion(group_id)

    async def _check_group_completion(self, group_id: str) -> bool:
        """Check if a group is complete and trigger reporting if so."""
        group = self._groups.get(group_id)
        if group is None:
            return False
        if not group.is_complete:
            return False
        # Already complete/failed - don't re-process
        if group.status in ("complete", "failed"):
            return True
        # Mark as complete or failed
        if group.failed_task_ids:
            group.status = "failed"
        else:
            group.status = "complete"
        group.completed_at = _utc_now()
        # Emit telemetry event
        asyncio.create_task(
            self._telemetry.emit(
                TaskTelemetryEvent(
                    event_type="task_group_completed" if group.status == "complete" else "task_group_failed",
                    outcome="completed" if group.status == "complete" else "failed",
                    session_id=self.session_id,
                    task_id=group.group_id,
                    task_type=TaskType.BACKGROUND,  # Groups contain background tasks
                    status=TaskStatus.COMPLETE if group.status == "complete" else TaskStatus.FAILED,
                    extra={
                        "group_name": group.name,
                        "task_count": len(group.task_ids),
                        "completed_count": len(group.completed_task_ids),
                        "failed_count": len(group.failed_task_ids),
                    },
                )
            )
        )
        # Queue group report if configured
        if group.report_strategy == "all" and not group.report_queued:
            group.report_queued = True
            await self._enqueue_group_report(group)
        # Signal any waiters that the group is complete
        event = self._group_completion_events.get(group_id)
        if event is not None:
            event.set()
        return True

    async def wait_for_group_completion(
        self,
        group_id: str,
        *,
        timeout_s: float | None = None,
    ) -> tuple[TaskGroup | None, bool]:
        """Wait for a group to complete.

        Args:
            group_id: The group to wait for.
            timeout_s: Maximum time to wait in seconds. If None, wait indefinitely.

        Returns:
            A tuple of (group, timed_out). If timed_out is True, the group may
            not be complete yet. If group is None, the group was not found.
        """
        group = self._groups.get(group_id)
        if group is None:
            return None, False
        # Already complete - return immediately
        if group.status in ("complete", "failed"):
            return group, False
        # Create event if not exists
        if group_id not in self._group_completion_events:
            self._group_completion_events[group_id] = asyncio.Event()
        event = self._group_completion_events[group_id]
        try:
            if timeout_s is not None:
                await asyncio.wait_for(event.wait(), timeout=timeout_s)
            else:
                await event.wait()
            # Re-fetch group after wait (state may have changed)
            group = self._groups.get(group_id)
            return group, False
        except TimeoutError:
            # Re-fetch group to get current state
            group = self._groups.get(group_id)
            return group, True

    async def get_group_results(self, group_id: str) -> list[dict[str, Any]]:
        """Get aggregated results/digests from a completed group's tasks."""
        group = self._groups.get(group_id)
        if group is None:
            return []
        results: list[dict[str, Any]] = []
        for task_id in group.completed_task_ids:
            state = await self._registry.get_task(task_id)
            if state is None:
                continue
            # Extract result digest if available
            result = state.result
            if result is not None:
                results.append(
                    {
                        "task_id": task_id,
                        "digest": result.get("digest") if isinstance(result, dict) else str(result),
                        "facts": result.get("facts") if isinstance(result, dict) else {},
                    }
                )
        return results

    async def _enqueue_group_report(self, group: TaskGroup) -> None:
        """Queue a proactive report for a completed task group."""
        config = self._proactive_config
        if config is None or not config.get("enabled"):
            return
        # For HUMAN_GATED groups, don't generate synthesis from unapproved results
        if group.merge_strategy == MergeStrategy.HUMAN_GATED:
            # Just emit a notification that group is ready for approval
            self._publish(
                StateUpdate(
                    session_id=self.session_id,
                    task_id=group.group_id,
                    trace_id=None,
                    update_type=UpdateType.CHECKPOINT,
                    content={
                        "kind": "group_approval",
                        "group_id": group.group_id,
                        "group_name": group.name,
                        "task_count": len(group.task_ids),
                        "completed_count": len(group.completed_task_ids),
                        "failed_count": len(group.failed_task_ids),
                        "prompt": f"Task group '{group.name}' complete. Apply results?",
                        "options": ["approve", "reject"],
                    },
                )
            )
            return
        # Build combined report request
        combined_digest: list[str] = []
        combined_facts: dict[str, Any] = {}
        combined_artifacts: list[dict[str, Any]] = []
        combined_sources: list[dict[str, Any]] = []
        failed_summaries: list[dict[str, Any]] = []
        combined_patches: list[ContextPatch] = []
        context_diverged = False
        # Collect from pending patches or merged context
        for task_id in group.task_ids:
            task = await self._registry.get_task(task_id)
            if task is None:
                continue
            # Check for pending patch
            for patch_id in group.patches:
                pending = self._pending_patches.get(patch_id)
                if pending is not None and pending.task_id == task_id:
                    patch = pending.patch
                    combined_digest.extend(patch.digest or [])
                    combined_facts.update(patch.facts or {})
                    combined_artifacts.extend(patch.artifacts or [])
                    combined_sources.extend(patch.sources or [])
                    combined_patches.append(patch)
                    if patch.context_diverged:
                        context_diverged = True
            if task_id in group.failed_task_ids:
                failed_summaries.append(
                    {
                        "task_id": task_id,
                        "description": task.description,
                        "error": task.error,
                    }
                )
        if failed_summaries:
            combined_facts = dict(combined_facts)
            combined_facts["failed_tasks"] = failed_summaries
        hops_candidates = []
        for task_id in group.task_ids:
            task = await self._registry.get_task(task_id)
            if task is None:
                continue
            hops = _coerce_proactive_hops(task.context_snapshot.tool_context)
            if hops is not None:
                hops_candidates.append(hops)
        hops_remaining = min(hops_candidates) if hops_candidates else None
        combined_patch = ContextPatch(
            task_id=f"group_{group.group_id}",
            digest=combined_digest,
            facts=combined_facts,
            artifacts=combined_artifacts,
            sources=combined_sources,
            context_diverged=context_diverged,
        )
        self._enqueue_proactive_report(
            task_id=combined_patch.task_id,
            trace_id=None,
            description=f"Task group '{group.name}' ({len(group.task_ids)} tasks)",
            execution_time_ms=0,
            patch=combined_patch,
            merge_strategy=group.merge_strategy,
            group_id=group.group_id,
            is_group_report=True,
            group_task_ids=list(group.task_ids),
            combined_patches=combined_patches,
            proactive_hops_remaining=hops_remaining,
        )

    async def cancel_group(
        self,
        group_id: str,
        *,
        reason: str | None = None,
        propagate: bool = True,
    ) -> bool:
        """Cancel a task group and optionally all its tasks."""
        group = self._groups.get(group_id)
        if group is None:
            return False
        if group.status in ("complete", "failed"):
            return False
        group.status = "failed"
        group.completed_at = _utc_now()
        if propagate:
            for task_id in group.pending_task_ids:
                await self.cancel_task(task_id, reason=reason or "group_cancelled")
        return True

    async def apply_group_patches(
        self,
        group_id: str,
        *,
        action: Literal["apply", "reject"] = "apply",
        strategy: MergeStrategy | None = None,
    ) -> bool:
        """Apply or reject all pending patches for a task group."""
        group = self._groups.get(group_id)
        if group is None:
            return False
        for patch_id in list(group.patches):
            pending = self._pending_patches.get(patch_id)
            if pending is None:
                continue
            if action == "apply":
                await self.apply_pending_patch(
                    patch_id=patch_id,
                    strategy=strategy or MergeStrategy.APPEND,
                )
            else:
                self._pending_patches.pop(patch_id, None)
        group.patches.clear()
        # Now that patches are applied, we can generate the synthesis report
        if action == "apply" and group.merge_strategy == MergeStrategy.HUMAN_GATED:
            # Re-queue for synthesis now that results are approved
            group.merge_strategy = MergeStrategy.APPEND  # Switch for synthesis
            group.report_queued = False
            await self._enqueue_group_report(group)
        return True

    def set_turn_id(self, turn_id: str | None) -> None:
        """Set the current foreground turn ID for group name resolution."""
        self._current_turn_id = turn_id

    async def auto_seal_open_groups(self, turn_id: str | None = None) -> int:
        """Auto-seal all OPEN groups created in the given turn (or current turn).

        Called when foreground yields to ensure groups don't stay open.
        Returns count of groups sealed.
        """
        target_turn = turn_id or self._current_turn_id
        sealed_count = 0
        for group in list(self._groups.values()):
            if group.status == "open" and group.turn_id == target_turn:
                await self.seal_group(group.group_id)
                sealed_count += 1
        return sealed_count

    async def _execute_task(
        self,
        *,
        pipeline: TaskPipeline,
        state: TaskState,
        steering: SteeringInbox,
        merge_strategy: MergeStrategy,
        group_id: str | None = None,
    ) -> TaskResult:
        semaphore = self._concurrency_semaphore
        if semaphore is not None:
            if semaphore.locked():
                self._emit_status_change(state, reason="queued")
            await semaphore.acquire()
        await self._registry.update_status(state.task_id, TaskStatus.RUNNING)
        self._emit_status_change(state, reason="running")
        start_ts = time.monotonic()
        runtime = TaskRuntime(
            session=self,
            state=state,
            steering=steering,
            context_snapshot=state.context_snapshot,
        )
        try:
            if self._limits.max_task_runtime_s is not None:
                result = await asyncio.wait_for(
                    pipeline(runtime),
                    timeout=self._limits.max_task_runtime_s,
                )
            else:
                result = await pipeline(runtime)
            task_result = result if isinstance(result, TaskResult) else TaskResult(payload=result)
            await self._registry.update_task(
                state.task_id,
                status=TaskStatus.COMPLETE,
                result=task_result.payload,
            )
            patch_id: str | None = None
            if task_result.context_patch is not None:
                patch_id = await self.apply_context_patch(
                    patch=task_result.context_patch,
                    strategy=merge_strategy,
                    payload=task_result.payload,
                    group_id=group_id,
                )
                # Check if task is in a group
                task_group = self._groups.get(group_id) if group_id else None
                # Enqueue proactive report for auto-merged background tasks
                # Suppress per-task reports for grouped tasks with report_strategy="all"
                should_report = (
                    patch_id is None
                    and state.task_type == TaskType.BACKGROUND
                    and self._proactive_config is not None
                    and self._proactive_config.get("enabled")
                )
                if should_report and task_group is not None:
                    # Suppress per-task report if group uses "all" strategy
                    if task_group.report_strategy == "all":
                        should_report = False
                if should_report:
                    execution_time_ms = int((time.monotonic() - start_ts) * 1000)
                    hops_remaining = _coerce_proactive_hops(state.context_snapshot.tool_context)
                    self._enqueue_proactive_report(
                        task_id=state.task_id,
                        trace_id=state.trace_id,
                        description=state.description,
                        execution_time_ms=execution_time_ms,
                        patch=task_result.context_patch,
                        merge_strategy=merge_strategy,
                        proactive_hops_remaining=hops_remaining,
                    )
            # Mark task complete in group if applicable
            if group_id is not None:
                await self.mark_task_complete_in_group(
                    group_id,
                    state.task_id,
                    success=True,
                    patch=task_result.context_patch,
                    patch_id=patch_id,
                )
            content = {
                "digest": task_result.digest,
                "payload": task_result.payload,
                "artifacts": task_result.artifacts,
                "sources": task_result.sources,
                "task_patch": task_result.context_patch.model_dump(mode="json") if task_result.context_patch else None,
                "patch_id": patch_id,
            }
            self._publish(
                StateUpdate(
                    session_id=self.session_id,
                    task_id=state.task_id,
                    trace_id=state.trace_id,
                    update_type=UpdateType.RESULT,
                    content=content,
                )
            )
            if task_result.notification is not None:
                if patch_id is not None:
                    task_result.notification.actions.append(
                        NotificationAction(
                            id="apply_context_patch",
                            label="Apply to conversation",
                            payload={"patch_id": patch_id, "task_id": state.task_id},
                        )
                    )
                runtime.notify(task_result.notification)
            self._emit_status_change(state, reason="complete")
            if (
                state.task_type == TaskType.BACKGROUND
                and state.context_snapshot.notify_on_complete
                and task_result.notification is None
            ):
                runtime.notify(
                    NotificationPayload(
                        severity="info",
                        title="Background task complete",
                        body=f"Task {state.task_id} completed.",
                    )
                )
            asyncio.create_task(
                self._telemetry.emit(
                    TaskTelemetryEvent(
                        event_type="task_completed",
                        outcome="completed",
                        session_id=self.session_id,
                        task_id=state.task_id,
                        parent_task_id=state.context_snapshot.spawned_from_task_id,
                        trace_id=state.trace_id,
                        task_type=state.task_type,
                        status=TaskStatus.COMPLETE,
                        mode="job" if (state.context_snapshot.spawn_reason or "").startswith("tool:") else "subagent",
                        spawn_reason=state.context_snapshot.spawn_reason,
                        duration_ms=(time.monotonic() - start_ts) * 1000,
                    )
                )
            )
            return task_result
        except SteeringCancelled as exc:
            await self._registry.update_task(state.task_id, status=TaskStatus.CANCELLED, error=exc.reason)
            self._emit_status_change(state, reason="cancelled")
            # Mark task as failed in group if applicable
            if group_id is not None:
                await self.mark_task_complete_in_group(group_id, state.task_id, success=False)
            asyncio.create_task(
                self._telemetry.emit(
                    TaskTelemetryEvent(
                        event_type="task_cancelled",
                        outcome="cancelled",
                        session_id=self.session_id,
                        task_id=state.task_id,
                        parent_task_id=state.context_snapshot.spawned_from_task_id,
                        trace_id=state.trace_id,
                        task_type=state.task_type,
                        status=TaskStatus.CANCELLED,
                        mode="job" if (state.context_snapshot.spawn_reason or "").startswith("tool:") else "subagent",
                        spawn_reason=state.context_snapshot.spawn_reason,
                        duration_ms=(time.monotonic() - start_ts) * 1000,
                        extra={"reason": exc.reason},
                    )
                )
            )
            if state.task_type == TaskType.BACKGROUND:
                runtime.notify(
                    NotificationPayload(
                        severity="warning",
                        title="Background task cancelled",
                        body=exc.reason or "Task cancelled.",
                    )
                )
            raise
        except TimeoutError:
            await self._registry.update_task(state.task_id, status=TaskStatus.FAILED, error="timeout")
            self._emit_status_change(state, reason="timeout")
            # Mark task as failed in group if applicable
            if group_id is not None:
                await self.mark_task_complete_in_group(group_id, state.task_id, success=False)
            asyncio.create_task(
                self._telemetry.emit(
                    TaskTelemetryEvent(
                        event_type="task_failed",
                        outcome="failed",
                        session_id=self.session_id,
                        task_id=state.task_id,
                        parent_task_id=state.context_snapshot.spawned_from_task_id,
                        trace_id=state.trace_id,
                        task_type=state.task_type,
                        status=TaskStatus.FAILED,
                        mode="job" if (state.context_snapshot.spawn_reason or "").startswith("tool:") else "subagent",
                        spawn_reason=state.context_snapshot.spawn_reason,
                        duration_ms=(time.monotonic() - start_ts) * 1000,
                        extra={"error": "timeout"},
                    )
                )
            )
            raise
        except Exception as exc:
            # Log immediately for terminal visibility (telemetry emit is async/fire-and-forget)
            _LOGGER.exception(
                "Task %s failed with %s: %s",
                state.task_id,
                exc.__class__.__name__,
                exc,
            )
            await self._registry.update_task(state.task_id, status=TaskStatus.FAILED, error=str(exc))
            self._publish(
                StateUpdate(
                    session_id=self.session_id,
                    task_id=state.task_id,
                    trace_id=state.trace_id,
                    update_type=UpdateType.ERROR,
                    content={"error": str(exc), "error_type": exc.__class__.__name__},
                )
            )
            self._emit_status_change(state, reason="failed")
            # Mark task as failed in group if applicable
            if group_id is not None:
                await self.mark_task_complete_in_group(group_id, state.task_id, success=False)
            asyncio.create_task(
                self._telemetry.emit(
                    TaskTelemetryEvent(
                        event_type="task_failed",
                        outcome="failed",
                        session_id=self.session_id,
                        task_id=state.task_id,
                        parent_task_id=state.context_snapshot.spawned_from_task_id,
                        trace_id=state.trace_id,
                        task_type=state.task_type,
                        status=TaskStatus.FAILED,
                        mode="job" if (state.context_snapshot.spawn_reason or "").startswith("tool:") else "subagent",
                        spawn_reason=state.context_snapshot.spawn_reason,
                        duration_ms=(time.monotonic() - start_ts) * 1000,
                        extra={"error": str(exc), "error_type": exc.__class__.__name__},
                    )
                )
            )
            if state.task_type == TaskType.BACKGROUND:
                runtime.notify(
                    NotificationPayload(
                        severity="error",
                        title="Background task failed",
                        body=str(exc),
                    )
                )
            raise
        finally:
            if semaphore is not None:
                semaphore.release()

    def _emit_status_change(self, state: TaskState, *, reason: str | None = None) -> None:
        self._publish(
            StateUpdate(
                session_id=self.session_id,
                task_id=state.task_id,
                trace_id=state.trace_id,
                update_type=UpdateType.STATUS_CHANGE,
                content={
                    "status": state.status.value,
                    "reason": reason,
                    "priority": state.priority,
                    "task_type": state.task_type.value,
                    "progress": state.progress,
                },
            )
        )

    def _build_snapshot(
        self,
        *,
        task_id: str,
        trace_id: str | None,
        spawn_reason: str | None,
        task_type: TaskType,
        query: str | None,
        parent_task_id: str | None,
        spawned_from_event_id: str | None,
        propagate_on_cancel: Literal["cascade", "isolate"],
        notify_on_complete: bool,
    ) -> TaskContextSnapshot:
        self._migrate_legacy_background_results()
        if task_type == TaskType.BACKGROUND:
            llm_context = _deepcopy_dict(self._context.llm_context)
            tool_context = _deepcopy_dict(self._context.tool_context)
            memory = _deepcopy_dict(self._context.memory)
            artifacts = _deepcopy_list(self._context.artifacts)
        else:
            llm_context = dict(self._context.llm_context)
            tool_context = dict(self._context.tool_context)
            memory = dict(self._context.memory)
            artifacts = list(self._context.artifacts)
        if self._context.background_results:
            llm_context = dict(llm_context)
            llm_context["background_results"] = _background_results_payload(self._context.background_results)
        return TaskContextSnapshot(
            session_id=self.session_id,
            task_id=task_id,
            trace_id=trace_id,
            spawned_from_task_id=parent_task_id or (self._foreground_task_id or "foreground"),
            spawned_from_event_id=spawned_from_event_id,
            spawn_reason=spawn_reason,
            query=query,
            propagate_on_cancel=propagate_on_cancel,
            notify_on_complete=notify_on_complete,
            context_version=self._context.version,
            context_hash=self._context.context_hash,
            llm_context=llm_context,
            tool_context=tool_context,
            memory=memory,
            artifacts=artifacts,
        )

    async def _cascade_cancel_children(self, *, parent_task_id: str, reason: str) -> None:
        visited = {parent_task_id}
        queue = [parent_task_id]
        while queue:
            current = queue.pop()
            children = await self._registry.list_children(current)
            for child_id in children:
                if child_id in visited:
                    continue
                visited.add(child_id)
                child = await self._registry.get_task(child_id)
                if child is None:
                    continue
                snapshot = child.context_snapshot
                if snapshot and snapshot.propagate_on_cancel == "isolate":
                    continue
                if child.status in {TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                    continue

                propagated = sanitize_steering_event(
                    SteeringEvent(
                        session_id=self.session_id,
                        task_id=child_id,
                        event_type=SteeringEventType.CANCEL,
                        payload={
                            "reason": reason,
                            "confirmed": True,
                            "propagated_from": parent_task_id,
                        },
                        source="system",
                    ),
                    max_payload_bytes=self._limits.max_steering_payload_bytes,
                )
                await self._state_store.save_steering(propagated)

                inbox = self._steering_inboxes.get(child_id)
                if inbox is not None:
                    await inbox.push(propagated)

                updated = await self._registry.update_task(
                    child_id,
                    status=TaskStatus.CANCELLED,
                    error="cancelled",
                )
                if updated is not None:
                    self._emit_status_change(updated, reason="parent_cancelled")

                handle = self._task_handles.get(child_id)
                if handle is not None and not handle.done():
                    handle.cancel()

                queue.append(child_id)


class SessionManager:
    """Registry for StreamingSession instances by session_id."""

    def __init__(
        self,
        *,
        limits: SessionLimits | None = None,
        state_store: SessionStateStore | StateStore | None = None,
        control_policy: ControlPolicy | None = None,
        telemetry_sink: TaskTelemetrySink | None = None,
    ) -> None:
        self._sessions: dict[str, StreamingSession] = {}
        self._lock = asyncio.Lock()
        self._limits = limits
        self._state_store = state_store
        self._control_policy = control_policy
        self._telemetry_sink = telemetry_sink

    async def get_or_create(self, session_id: str) -> StreamingSession:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = StreamingSession(
                    session_id,
                    control_policy=self._control_policy,
                    limits=self._limits,
                    state_store=self._state_store,
                    telemetry_sink=self._telemetry_sink,
                )
                self._sessions[session_id] = session
        await session.hydrate()
        return session

    async def get(self, session_id: str) -> StreamingSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def drop(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()


__all__ = [
    "PendingContextPatch",
    "SessionManager",
    "SessionLimits",
    "StreamingSession",
    "TaskPipeline",
    "TaskResult",
    "TaskRuntime",
    "TaskStateModel",
]
