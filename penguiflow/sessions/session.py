"""Streaming session orchestration for bidirectional tasks."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

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
    MergeStrategy,
    NotificationAction,
    NotificationPayload,
    StateUpdate,
    TaskContextSnapshot,
    TaskState,
    TaskStateModel,
    TaskStatus,
    TaskType,
    UpdateType,
)
from .persistence import InMemorySessionStateStore, SessionStateStore, StateStoreSessionAdapter
from .policy import ControlPolicy
from .registry import TaskRegistry
from .telemetry import NoOpTaskTelemetrySink, TaskTelemetryEvent, TaskTelemetrySink
from .transport import SessionConnection, Transport


@dataclass(slots=True)
class SessionContext:
    llm_context: dict[str, Any] = field(default_factory=dict)
    tool_context: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
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
            asyncio.create_task(
                self.session.registry.update_task(self.state.task_id, progress=self.state.progress)
            )
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


class StreamingSession:
    """Manages bidirectional communication and task lifecycle."""

    def __init__(
        self,
        session_id: str,
        *,
        control_policy: ControlPolicy | None = None,
        limits: SessionLimits | None = None,
        state_store: SessionStateStore | None = None,
        telemetry_sink: TaskTelemetrySink | None = None,
    ) -> None:
        self.session_id = session_id
        self._limits = limits or SessionLimits()
        self._state_store: SessionStateStore
        if state_store is None:
            self._state_store = InMemorySessionStateStore()
        elif hasattr(state_store, "save_task"):
            self._state_store = state_store
        else:
            self._state_store = StateStoreSessionAdapter(state_store)  # type: ignore[arg-type]
        self._registry = TaskRegistry(persist_task=self._state_store.save_task)
        self._broker = UpdateBroker(max_queue_size=self._limits.update_queue_size)
        self._steering_inboxes: dict[str, SteeringInbox] = {}
        self._task_handles: dict[str, asyncio.Task[None]] = {}
        self._pending_controls: dict[str, SteeringEvent] = {}
        self._pending_patches: dict[str, PendingContextPatch] = {}
        self._seen_event_ids: dict[str, deque[str]] = {}
        self._concurrency_semaphore = (
            asyncio.Semaphore(self._limits.max_concurrent_tasks)
            if self._limits.max_concurrent_tasks > 0
            else None
        )
        self._control_policy = control_policy or ControlPolicy()
        self._telemetry = telemetry_sink or NoOpTaskTelemetrySink()
        self._context = SessionContext()
        self._foreground_task_id: str | None = None
        self._hydrated = False

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
            self._context.llm_context = dict(llm_context)
        if tool_context is not None:
            self._context.tool_context = dict(tool_context)
        self._context.version += 1
        self._context.context_hash = _hash_context(self._context.llm_context)

    async def _ensure_limits(self, task_type: TaskType) -> None:
        tasks = await self._registry.list_tasks(self.session_id)
        if self._limits.max_tasks_per_session and len(tasks) >= self._limits.max_tasks_per_session:
            raise RuntimeError("task_limit_exceeded")
        if task_type == TaskType.BACKGROUND and self._limits.max_background_tasks:
            background_count = len([t for t in tasks if t.task_type == TaskType.BACKGROUND])
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
        steering = SteeringInbox(maxsize=self._limits.steering_queue_size)
        self._steering_inboxes[task_id] = steering
        handle = asyncio.create_task(
            self._run_task(
                pipeline=pipeline,
                state=state,
                steering=steering,
                merge_strategy=merge_strategy,
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

        payload = patch.model_dump(mode="json")
        if strategy == MergeStrategy.APPEND:
            self._context.llm_context.setdefault("background_results", []).append(payload)
        elif strategy == MergeStrategy.REPLACE:
            self._context.llm_context["background_result"] = payload
        self._context.version += 1
        self._context.context_hash = _hash_context(self._context.llm_context)
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
    ) -> None:
        try:
            await self._execute_task(
                pipeline=pipeline,
                state=state,
                steering=steering,
                merge_strategy=merge_strategy,
            )
        except asyncio.CancelledError:
            await self._registry.update_task(state.task_id, status=TaskStatus.CANCELLED, error="cancelled")
            self._emit_status_change(state, reason="cancelled")
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
        for task_id, handle in list(self._task_handles.items()):
            if not handle.done():
                handle.cancel()
            await self._registry.update_task(task_id, status=TaskStatus.CANCELLED, error="session_closed")
        self._task_handles.clear()
        self._steering_inboxes.clear()

    async def _execute_task(
        self,
        *,
        pipeline: TaskPipeline,
        state: TaskState,
        steering: SteeringInbox,
        merge_strategy: MergeStrategy,
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
                )
            content = {
                "digest": task_result.digest,
                "payload": task_result.payload,
                "artifacts": task_result.artifacts,
                "sources": task_result.sources,
                "task_patch": task_result.context_patch.model_dump(mode="json")
                if task_result.context_patch
                else None,
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
        return TaskContextSnapshot(
            session_id=self.session_id,
            task_id=task_id,
            trace_id=trace_id,
            spawned_from_task_id=parent_task_id
            or (self._foreground_task_id or "foreground"),
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
        state_store: SessionStateStore | None = None,
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
