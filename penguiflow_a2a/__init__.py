"""A2A server and client adapters for ReactPlanner agents."""

from .bindings.http import create_a2a_http_app, create_a2a_http_router, install_a2a_http
from .config import A2AConfig
from .core import A2AService
from .planner_tools import A2AAgentToolset, RemoteEventSink
from .registry import (
    A2ARouterToolset,
    AgentRegistry,
    AgentRouteCandidate,
    AgentRouteRequest,
    RemoteAgentRecord,
    RemoteSkillRecord,
    RouterDelegationArgs,
    RouterDelegationResult,
    RouterPolicy,
    fetch_agent_card,
    load_agent_registry_config,
)
from .transport import A2AHttpTransport

__all__ = [
    "A2AConfig",
    "A2AService",
    "create_a2a_http_app",
    "create_a2a_http_router",
    "install_a2a_http",
    "A2AHttpTransport",
    "A2AAgentToolset",
    "RemoteEventSink",
    "A2ARouterToolset",
    "AgentRegistry",
    "AgentRouteCandidate",
    "AgentRouteRequest",
    "RemoteAgentRecord",
    "RemoteSkillRecord",
    "RouterPolicy",
    "RouterDelegationArgs",
    "RouterDelegationResult",
    "fetch_agent_card",
    "load_agent_registry_config",
]

try:
    from .bindings.grpc import A2AGrpcServicer, add_a2a_grpc_service  # noqa: F401

    __all__.extend(["A2AGrpcServicer", "add_a2a_grpc_service"])
except RuntimeError:
    pass
