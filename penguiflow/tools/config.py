"""Configuration models for ToolNode."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TransportType(str, Enum):
    """Supported communication protocols."""

    MCP = "mcp"  # MCP via FastMCP (stdio/SSE/HTTP auto-detected)
    HTTP = "http"  # REST API via UTCP (planned)
    UTCP = "utcp"  # Native UTCP endpoint (planned)
    CLI = "cli"  # Command-line tools via UTCP (planned)


class AuthType(str, Enum):
    """Authentication methods."""

    NONE = "none"
    API_KEY = "api_key"  # Static API key (header injection)
    BEARER = "bearer"  # Static bearer token
    OAUTH2_USER = "oauth2_user"  # User-level OAuth (HITL)


class UtcpMode(str, Enum):
    """How to interpret UTCP connection string."""

    AUTO = "auto"  # Try manual_url first, fallback to base_url
    MANUAL_URL = "manual_url"  # Connection is a UTCP manual endpoint (recommended)
    BASE_URL = "base_url"  # Connection is a REST base URL (limited discovery)


class RetryPolicy(BaseModel):
    """Retry configuration using tenacity semantics."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    wait_exponential_min_s: float = Field(default=0.1, ge=0.01)
    wait_exponential_max_s: float = Field(default=5.0, ge=0.1)
    retry_on_status: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504],
    )


class ExternalToolConfig(BaseModel):
    """Configuration for an external tool source."""

    # Identity
    name: str = Field(..., description="Unique namespace for tools (e.g., 'github')")
    description: str = Field(default="")

    # Transport
    transport: TransportType
    connection: str = Field(..., description="Connection string (command for MCP, URL for HTTP/UTCP)")

    # UTCP-specific: how to interpret the connection string
    utcp_mode: UtcpMode = Field(
        default=UtcpMode.AUTO,
        description="For HTTP/UTCP: how to interpret connection (manual_url recommended)",
    )

    # Environment (for MCP subprocess)
    env: dict[str, str] = Field(default_factory=dict)

    # Authentication
    auth_type: AuthType = Field(default=AuthType.NONE)
    auth_config: dict[str, Any] = Field(default_factory=dict)

    # Resilience
    timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    max_concurrency: int = Field(default=10, ge=1, le=100)

    # Discovery filtering
    tool_filter: list[str] | None = Field(
        default=None,
        description="Regex patterns to include specific tools (None = all)",
    )

    @model_validator(mode="after")
    def validate_config(self) -> ExternalToolConfig:
        """Validate transport-specific requirements."""
        if self.auth_type == AuthType.BEARER and "token" not in self.auth_config:
            raise ValueError("auth_type=BEARER requires auth_config.token")
        if self.auth_type == AuthType.API_KEY and "api_key" not in self.auth_config:
            raise ValueError("auth_type=API_KEY requires auth_config.api_key")

        if self.transport == TransportType.MCP and self.utcp_mode != UtcpMode.AUTO:
            raise ValueError("utcp_mode is only valid for HTTP/UTCP transports")

        return self


__all__ = [
    "AuthType",
    "ExternalToolConfig",
    "RetryPolicy",
    "TransportType",
    "UtcpMode",
]
