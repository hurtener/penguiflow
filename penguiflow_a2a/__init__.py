"""A2A server and client adapters for ReactPlanner agents."""

import contextlib

from .bindings.http import (
    create_a2a_http_app,
    create_a2a_http_router,
    install_a2a_http,
)
from .config import A2AConfig
from .core import A2APlanner, A2AService
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
    "A2AAgentToolset",
    "A2AConfig",
    "A2AHttpTransport",
    "A2APlanner",
    "A2ARouterToolset",
    "A2AService",
    "AgentRegistry",
    "AgentRouteCandidate",
    "AgentRouteRequest",
    "RemoteAgentRecord",
    "RemoteEventSink",
    "RemoteSkillRecord",
    "RouterDelegationArgs",
    "RouterDelegationResult",
    "RouterPolicy",
    "create_a2a_http_app",
    "create_a2a_http_router",
    "fetch_agent_card",
    "install_a2a_http",
    "load_agent_registry_config",
]

# The gRPC binding needs the optional `a2a-grpc` extra and raises
# RuntimeError on import when it is missing.
with contextlib.suppress(RuntimeError):
    from .bindings.grpc import A2AGrpcServicer as A2AGrpcServicer
    from .bindings.grpc import add_a2a_grpc_service as add_a2a_grpc_service

    __all__ += ["A2AGrpcServicer", "add_a2a_grpc_service"]
