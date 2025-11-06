"""Enterprise agent example with ReactPlanner.

This package provides a gold-standard implementation for production
agent deployments using PenguiFlow's ReactPlanner with comprehensive
observability, error handling, and configuration management.

Public API:
    - EnterpriseAgentOrchestrator: Main agent orchestrator
    - AgentConfig: Type-safe configuration
    - AgentTelemetry: Observability middleware

Example:
    >>> from examples.planner_enterprise_agent import (
    ...     EnterpriseAgentOrchestrator,
    ...     AgentConfig,
    ... )
    >>> config = AgentConfig.from_env()
    >>> agent = EnterpriseAgentOrchestrator(config)
    >>> result = await agent.execute("Analyze deployment logs")
"""

from .config import AgentConfig
from .main import EnterpriseAgentOrchestrator
from .telemetry import AgentTelemetry

__all__ = [
    "AgentConfig",
    "AgentTelemetry",
    "EnterpriseAgentOrchestrator",
]
