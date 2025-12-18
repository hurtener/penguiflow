"""Public package surface for PenguiFlow."""

from __future__ import annotations

from . import testkit
from .bus import BusEnvelope, MessageBus
from .catalog import NodeSpec, SideEffect, build_catalog, tool
from .core import (
    DEFAULT_QUEUE_MAXSIZE,
    Context,
    CycleError,
    PenguiFlow,
    call_playbook,
    create,
)
from .debug import format_flow_event
from .errors import FlowError, FlowErrorCode
from .metrics import FlowEvent
from .middlewares import LatencyCallback, Middleware, log_flow_events
from .node import Node, NodePolicy
from .patterns import join_k, map_concurrent, predicate_router, union_router
from .planner import (
    PlannerAction,
    PlannerFinish,
    ReactPlanner,
    Trajectory,
    TrajectoryStep,
)
from .policies import DictRoutingPolicy, RoutingPolicy, RoutingRequest
from .registry import ModelRegistry
from .remote import (
    RemoteCallRequest,
    RemoteCallResult,
    RemoteNode,
    RemoteStreamEvent,
    RemoteTransport,
)
from .state import RemoteBinding, StateStore, StoredEvent
from .streaming import (
    chunk_to_ws_json,
    emit_stream_events,
    format_sse_event,
    stream_flow,
)
from .types import WM, FinalAnswer, Headers, Message, PlanStep, StreamChunk, Thought
from .viz import flow_to_dot, flow_to_mermaid

__all__ = [
    "__version__",
    "Context",
    "CycleError",
    "PenguiFlow",
    "DEFAULT_QUEUE_MAXSIZE",
    "Node",
    "NodePolicy",
    "ModelRegistry",
    "NodeSpec",
    "SideEffect",
    "build_catalog",
    "tool",
    "Middleware",
    "log_flow_events",
    "LatencyCallback",
    "FlowEvent",
    "format_flow_event",
    "FlowError",
    "FlowErrorCode",
    "MessageBus",
    "BusEnvelope",
    "call_playbook",
    "Headers",
    "Message",
    "StreamChunk",
    "PlanStep",
    "Thought",
    "WM",
    "FinalAnswer",
    "map_concurrent",
    "join_k",
    "predicate_router",
    "union_router",
    "DictRoutingPolicy",
    "RoutingPolicy",
    "RoutingRequest",
    "format_sse_event",
    "chunk_to_ws_json",
    "stream_flow",
    "emit_stream_events",
    "flow_to_mermaid",
    "flow_to_dot",
    "create",
    "testkit",
    "StateStore",
    "StoredEvent",
    "RemoteBinding",
    "RemoteTransport",
    "RemoteCallRequest",
    "RemoteCallResult",
    "RemoteStreamEvent",
    "RemoteNode",
    "ReactPlanner",
    "PlannerAction",
    "PlannerFinish",
    "Trajectory",
    "TrajectoryStep",
]

__version__ = "2.7.1"
