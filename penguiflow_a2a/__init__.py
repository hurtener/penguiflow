"""Optional A2A adapters for PenguiFlow."""

from .server import (
    A2AAgentCard,
    A2AMessagePayload,
    A2AServerAdapter,
    A2ASkill,
    A2ATaskCancelRequest,
    create_a2a_app,
)

__all__ = [
    "A2AAgentCard",
    "A2ASkill",
    "A2AMessagePayload",
    "A2ATaskCancelRequest",
    "A2AServerAdapter",
    "create_a2a_app",
]
