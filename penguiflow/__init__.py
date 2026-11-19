"""Public package surface for PenguiFlow (ReactPlanner agents)."""

from __future__ import annotations

import importlib.metadata as _metadata

from .artifacts import (
    ArtifactRef,
    ArtifactRetentionConfig,
    ArtifactScope,
    ArtifactStore,
    InMemoryArtifactStore,
    NoOpArtifactStore,
    discover_artifact_store,
)
from .catalog import (
    NodeSpec,
    SideEffect,
    ToolInputExample,
    ToolLoadingMode,
    build_catalog,
    tool,
)
from .logging import ExtraFormatter, StructuredFormatter, configure_logging
from .node import Node, NodePolicy
from .planner import (
    PlannerAction,
    PlannerFinish,
    ReactPlanner,
    SkillCapabilityContext,
    SkillPackConfig,
    SkillProposalConfig,
    SkillProposalDraft,
    SkillProposeRequest,
    SkillProposeResponse,
    SkillProvider,
    SkillProviderFactory,
    SkillsConfig,
    SkillsDirectoryConfig,
    ToolExamplesConfig,
    ToolSearchConfig,
    Trajectory,
    TrajectoryStep,
)
from .registry import ModelRegistry
from .remote import (
    REMOTE_TERMINAL_TASK_STATES,
    RemoteCallRequest,
    RemoteCallResult,
    RemotePushNotificationBinding,
    RemoteStreamEvent,
    RemoteTaskAuthRequired,
    RemoteTaskEvent,
    RemoteTaskInputRequired,
    RemoteTaskPage,
    RemoteTaskSnapshot,
    RemoteTaskState,
    RemoteTaskStatus,
    RemoteTransport,
    SupportsRemoteTasks,
)
from .sessions.models import (
    ContextPatch,
    NotificationAction,
    StateUpdate,
    TaskContextSnapshot,
    TaskStatus,
    TaskType,
    UpdateType,
)
from .sessions.persistence import (
    InMemorySessionStateStore,
    SessionStateStore,
    StateStoreSessionAdapter,
)
from .sessions.planner import PlannerTaskPipeline
from .sessions.scheduler import (
    JobDefinition,
    JobScheduler,
    JobSchedulerRunner,
    ScheduleConfig,
)
from .sessions.session import (
    PendingContextPatch,
    SessionLimits,
    SessionManager,
    StreamingSession,
    TaskResult,
    TaskRuntime,
)
from .sessions.transport import SessionConnection, Transport
from .state import (
    RemoteBinding,
    StateStore,
    StoredEvent,
    SupportsConversationBindings,
)
from .steering import (
    SteeringCancelled,
    SteeringEvent,
    SteeringEventType,
    SteeringInbox,
)
from .types import Headers, Message

__all__ = [
    "REMOTE_TERMINAL_TASK_STATES",
    # Artifacts
    "ArtifactRef",
    "ArtifactRetentionConfig",
    "ArtifactScope",
    "ArtifactStore",
    "ContextPatch",
    "ExtraFormatter",
    "Headers",
    "InMemoryArtifactStore",
    "InMemorySessionStateStore",
    "JobDefinition",
    "JobScheduler",
    "JobSchedulerRunner",
    "Message",
    "ModelRegistry",
    "NoOpArtifactStore",
    "Node",
    "NodePolicy",
    "NodeSpec",
    "NotificationAction",
    "PendingContextPatch",
    "PlannerAction",
    "PlannerFinish",
    "PlannerTaskPipeline",
    "ReactPlanner",
    "RemoteBinding",
    "RemoteCallRequest",
    "RemoteCallResult",
    "RemotePushNotificationBinding",
    "RemoteStreamEvent",
    "RemoteTaskAuthRequired",
    "RemoteTaskEvent",
    "RemoteTaskInputRequired",
    "RemoteTaskPage",
    "RemoteTaskSnapshot",
    "RemoteTaskState",
    "RemoteTaskStatus",
    "RemoteTransport",
    "ScheduleConfig",
    "SessionConnection",
    "SessionLimits",
    "SessionManager",
    "SessionStateStore",
    "SideEffect",
    "SkillCapabilityContext",
    "SkillPackConfig",
    "SkillProposalConfig",
    "SkillProposalDraft",
    "SkillProposeRequest",
    "SkillProposeResponse",
    "SkillProvider",
    "SkillProviderFactory",
    "SkillsConfig",
    "SkillsDirectoryConfig",
    "StateStore",
    "StateStoreSessionAdapter",
    "StateUpdate",
    "SteeringCancelled",
    "SteeringEvent",
    "SteeringEventType",
    "SteeringInbox",
    "StoredEvent",
    "StreamingSession",
    "StructuredFormatter",
    "SupportsConversationBindings",
    "SupportsRemoteTasks",
    "TaskContextSnapshot",
    "TaskResult",
    "TaskRuntime",
    "TaskStatus",
    "TaskType",
    "ToolExamplesConfig",
    "ToolInputExample",
    "ToolLoadingMode",
    "ToolSearchConfig",
    "Trajectory",
    "TrajectoryStep",
    "Transport",
    "UpdateType",
    "__version__",
    "build_catalog",
    # Logging
    "configure_logging",
    "discover_artifact_store",
    "tool",
]

try:
    __version__ = _metadata.version("penguiflow-lite")
except (
    _metadata.PackageNotFoundError
):  # imported from a source tree that is not installed
    __version__ = "0.0.0"
