"""Optional A2A adapters for PenguiFlow."""

from .bindings.http import create_a2a_http_app
from .config import A2AConfig, PayloadMode
from .core import A2AService
from .server import (
    A2AAgentCard,
    A2AMessagePayload,
    A2AServerAdapter,
    A2ASkill,
    A2ATaskCancelRequest,
    create_a2a_app,
)
from .transport import A2AHttpTransport

__all__ = [
    "A2AAgentCard",
    "A2ASkill",
    "A2AMessagePayload",
    "A2ATaskCancelRequest",
    "A2AServerAdapter",
    "create_a2a_app",
    "A2AConfig",
    "PayloadMode",
    "A2AService",
    "create_a2a_http_app",
    "A2AHttpTransport",
]

try:
    from .bindings.grpc import A2AGrpcServicer, add_a2a_grpc_service  # noqa: F401

    __all__.extend(["A2AGrpcServicer", "add_a2a_grpc_service"])
except RuntimeError:
    pass
