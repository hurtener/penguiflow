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
from .catalog import NodeSpec, SideEffect, ToolInputExample, ToolLoadingMode, build_catalog, tool
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
from .sessions.scheduler import JobDefinition, JobScheduler, JobSchedulerRunner, ScheduleConfig
from .sessions.session import (
    PendingContextPatch,
    SessionLimits,
    SessionManager,
    StreamingSession,
    TaskResult,
    TaskRuntime,
)
from .sessions.transport import SessionConnection, Transport
from .state import RemoteBinding, StateStore, StoredEvent, SupportsConversationBindings
from .steering import SteeringCancelled, SteeringEvent, SteeringEventType, SteeringInbox
from .types import Headers, Message

__all__ = [
    "__version__",
    # Artifacts
    "ArtifactRef",
    "ArtifactRetentionConfig",
    "ArtifactScope",
    "ArtifactStore",
    "InMemoryArtifactStore",
    "NoOpArtifactStore",
    "discover_artifact_store",
    # Logging
    "configure_logging",
    "ExtraFormatter",
    "StructuredFormatter",
    "Node",
    "NodePolicy",
    "ModelRegistry",
    "NodeSpec",
    "SideEffect",
    "ToolInputExample",
    "ToolLoadingMode",
    "build_catalog",
    "tool",
    "Headers",
    "Message",
    "SteeringCancelled",
    "SteeringEvent",
    "SteeringEventType",
    "SteeringInbox",
    "ContextPatch",
    "NotificationAction",
    "StateUpdate",
    "TaskContextSnapshot",
    "TaskStatus",
    "TaskType",
    "UpdateType",
    "PlannerTaskPipeline",
    "InMemorySessionStateStore",
    "JobDefinition",
    "JobScheduler",
    "JobSchedulerRunner",
    "ScheduleConfig",
    "PendingContextPatch",
    "SessionConnection",
    "SessionLimits",
    "SessionManager",
    "SessionStateStore",
    "StateStoreSessionAdapter",
    "StreamingSession",
    "TaskResult",
    "TaskRuntime",
    "Transport",
    "StateStore",
    "StoredEvent",
    "SupportsConversationBindings",
    "RemoteBinding",
    "RemoteTransport",
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
    "REMOTE_TERMINAL_TASK_STATES",
    "SupportsRemoteTasks",
    "ReactPlanner",
    "PlannerAction",
    "PlannerFinish",
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
    "ToolExamplesConfig",
    "ToolSearchConfig",
    "Trajectory",
    "TrajectoryStep",
]

try:
    __version__ = _metadata.version("penguiflow-lite")
except _metadata.PackageNotFoundError:  # imported from a source tree that is not installed
    __version__ = "0.0.0"
