"""Session/task coordination primitives for bidirectional streaming."""

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
from .persistence import (
    InMemorySessionStateStore,
    SessionStateStore,
    StateStoreSessionAdapter,
)
from .planner import PlannerTaskPipeline, TaskPlanner
from .policy import ControlPolicy
from .proactive import (
    create_default_proactive_generator,
    setup_proactive_reporting,
)
from .registry import TaskRegistry
from .scheduler import (
    InMemoryJobStore,
    JobDefinition,
    JobScheduler,
    JobSchedulerRunner,
    JobStore,
    ScheduleConfig,
)
from .session import (
    PendingContextPatch,
    SessionLimits,
    SessionManager,
    StreamingSession,
    TaskPipeline,
    TaskResult,
    TaskRuntime,
)
from .task_service import (
    ContextDepth,
    InProcessTaskService,
    NoOpSpawnGuard,
    SpawnDecision,
    SpawnGuard,
    SpawnRequest,
    TaskDetails,
    TaskService,
    TaskSpawnResult,
    TaskSummary,
)
from .task_tools import build_task_tool_specs
from .telemetry import (
    NoOpTaskTelemetrySink,
    TaskTelemetryEvent,
    TaskTelemetrySink,
)
from .transport import SessionConnection, Transport

__all__ = [
    "ContextDepth",
    "ContextPatch",
    "ControlPolicy",
    "InMemoryJobStore",
    "InMemorySessionStateStore",
    "InProcessTaskService",
    "JobDefinition",
    "JobScheduler",
    "JobSchedulerRunner",
    "JobStore",
    "MergeStrategy",
    "NoOpSpawnGuard",
    "NoOpTaskTelemetrySink",
    "NotificationAction",
    "NotificationPayload",
    "PendingContextPatch",
    "PlannerTaskPipeline",
    "ScheduleConfig",
    "SessionConnection",
    "SessionLimits",
    "SessionManager",
    "SessionStateStore",
    "SpawnDecision",
    "SpawnGuard",
    "SpawnRequest",
    "StateStoreSessionAdapter",
    "StateUpdate",
    "StreamingSession",
    "TaskContextSnapshot",
    "TaskDetails",
    "TaskPipeline",
    "TaskPlanner",
    "TaskRegistry",
    "TaskResult",
    "TaskRuntime",
    "TaskService",
    "TaskSpawnResult",
    "TaskState",
    "TaskStateModel",
    "TaskStatus",
    "TaskSummary",
    "TaskTelemetryEvent",
    "TaskTelemetrySink",
    "TaskType",
    "Transport",
    "UpdateBroker",
    "UpdateType",
    "build_task_tool_specs",
    "create_default_proactive_generator",
    "setup_proactive_reporting",
]
