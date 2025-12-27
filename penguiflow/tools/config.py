"""Configuration models for ToolNode."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from penguiflow.planner.context import ToolContext


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
    COOKIE = "cookie"  # Cookie-based auth (e.g., Databricks Apps session)
    OAUTH2_USER = "oauth2_user"  # User-level OAuth (HITL)


class UtcpMode(str, Enum):
    """How to interpret UTCP connection string."""

    AUTO = "auto"  # Try manual_url first, fallback to base_url
    MANUAL_URL = "manual_url"  # Connection is a UTCP manual endpoint (recommended)
    BASE_URL = "base_url"  # Connection is a REST base URL (limited discovery)


class McpTransportMode(str, Enum):
    """MCP transport mode for URL-based connections.

    FastMCP supports multiple transports. This setting controls which is used
    when connecting to MCP servers over HTTP/HTTPS.
    """

    AUTO = "auto"  # Let FastMCP auto-detect (default when no auth headers)
    SSE = "sse"  # Force Server-Sent Events transport (legacy)
    STREAMABLE_HTTP = "streamable_http"  # Force Streamable HTTP (modern, recommended)


class RetryPolicy(BaseModel):
    """Retry configuration using tenacity semantics."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    wait_exponential_min_s: float = Field(default=0.1, ge=0.01)
    wait_exponential_max_s: float = Field(default=5.0, ge=0.1)
    retry_on_status: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504],
    )


# -----------------------------------------------------------------------------
# Artifact Extraction Configuration (Phase 1)
# -----------------------------------------------------------------------------

# Default binary signatures for detection (base64 prefixes -> (extension, mime_type))
DEFAULT_BINARY_SIGNATURES: dict[str, tuple[str, str]] = {
    "JVBERi": ("pdf", "application/pdf"),  # PDF (%PDF-)
    "iVBORw": ("png", "image/png"),  # PNG
    "/9j/": ("jpeg", "image/jpeg"),  # JPEG
    "R0lGOD": ("gif", "image/gif"),  # GIF (GIF8)
    "UEsDB": ("zip", "application/zip"),  # ZIP/DOCX/XLSX/PPTX
    "UEsFB": ("zip", "application/zip"),  # ZIP variant
    "AAAA": ("unknown", "application/octet-stream"),  # Ambiguous but often binary
}


class BinaryDetectionConfig(BaseModel):
    """Configuration for automatic binary content detection."""

    enabled: bool = Field(default=True, description="Enable binary content detection")
    signatures: dict[str, tuple[str, str]] = Field(
        default_factory=lambda: dict(DEFAULT_BINARY_SIGNATURES),
        description="Map of base64 prefix -> (extension, mime_type)",
    )
    min_size_for_detection: int = Field(
        default=1000,
        ge=0,
        description="Minimum string length to check for binary content",
    )
    max_decode_bytes: int = Field(
        default=5_000_000,
        ge=1000,
        description="Maximum bytes to decode when probing binary content",
    )
    require_magic_bytes: bool = Field(
        default=True,
        description="Require magic byte validation after base64 decode",
    )


class ResourceHandlingConfig(BaseModel):
    """Policy for MCP resources and resource_links."""

    enabled: bool = Field(default=True, description="Enable resource link handling")
    auto_read_if_size_under_bytes: int = Field(
        default=0,
        ge=0,
        description="Auto-read resources smaller than this (0 = never auto-read)",
    )
    inline_text_if_under_chars: int = Field(
        default=10_000,
        ge=0,
        description="Inline text resources smaller than this",
    )
    cache_reads_to_artifacts: bool = Field(
        default=True,
        description="Cache resource reads to artifact store",
    )


class ArtifactFieldConfig(BaseModel):
    """Configuration for extracting specific fields as artifacts."""

    field_path: str = Field(
        ...,
        description="JSONPath or dot notation to the field (e.g., 'content' or 'result.pdf_data')",
    )
    content_type: str = Field(
        ...,
        description="Expected content type: pdf, image, binary, text",
    )
    mime_type: str | None = Field(
        default=None,
        description="Override MIME type (auto-detected if None)",
    )
    summary_template: str = Field(
        default="Downloaded {content_type} ({size} bytes)",
        description="Template for LLM summary",
    )


class ArtifactExtractionConfig(BaseModel):
    """Configuration for extracting artifacts from tool outputs."""

    # Size-based safety net (Layer 0)
    max_inline_size: int = Field(
        default=10_000,
        ge=0,
        description="Maximum chars before auto-artifact extraction",
    )
    auto_artifact_large_content: bool = Field(
        default=True,
        description="Automatically store large content as artifacts",
    )

    # Binary detection (Layer 3)
    binary_detection: BinaryDetectionConfig = Field(
        default_factory=BinaryDetectionConfig,
    )

    # MCP resources + links (Layer 1)
    resources: ResourceHandlingConfig = Field(
        default_factory=ResourceHandlingConfig,
    )

    # Per-tool field configuration (Layer 4)
    tool_fields: dict[str, list[ArtifactFieldConfig]] = Field(
        default_factory=dict,
        description="Map of tool_name -> list of field extraction configs",
    )

    # Summary templates
    default_binary_summary: str = Field(
        default="Binary content stored as artifact ({mime_type}, {size} bytes). Artifact ID: {artifact_id}",
    )
    default_text_summary: str = Field(
        default="Large text stored as artifact ({size} chars). Artifact ID: {artifact_id}",
    )


# Type alias for custom output transformer (Layer 5)
OutputTransformer = Callable[[str, Any, ToolContext], Any | Awaitable[Any]]


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

    # MCP-specific: which transport to use for URL-based connections
    mcp_transport_mode: McpTransportMode = Field(
        default=McpTransportMode.AUTO,
        description="For MCP over HTTP: auto-detect, sse, or streamable_http",
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

    # Tool arg validation defaults (telemetry-only)
    arg_validation: dict[str, Any] = Field(
        default_factory=lambda: {"emit_suspect": True},
        description="Planner arg validation policy for discovered tools.",
    )

    # Artifact extraction (Phase 1)
    artifact_extraction: ArtifactExtractionConfig = Field(
        default_factory=ArtifactExtractionConfig,
        description="Configuration for extracting binary/large content as artifacts",
    )

    # Custom output transformer (Layer 5 escape hatch)
    # Note: Not serializable, must be set programmatically
    output_transformer: OutputTransformer | None = Field(
        default=None,
        exclude=True,  # Exclude from serialization
        description="Custom async function to transform tool outputs",
    )

    @model_validator(mode="after")
    def validate_config(self) -> ExternalToolConfig:
        """Validate transport-specific requirements."""
        if self.auth_type == AuthType.BEARER and "token" not in self.auth_config:
            raise ValueError("auth_type=BEARER requires auth_config.token")
        if self.auth_type == AuthType.API_KEY and "api_key" not in self.auth_config:
            raise ValueError("auth_type=API_KEY requires auth_config.api_key")
        if self.auth_type == AuthType.COOKIE:
            if "cookie_name" not in self.auth_config:
                raise ValueError("auth_type=COOKIE requires auth_config.cookie_name")
            if "cookie_value" not in self.auth_config:
                raise ValueError("auth_type=COOKIE requires auth_config.cookie_value")

        if self.transport == TransportType.MCP and self.utcp_mode != UtcpMode.AUTO:
            raise ValueError("utcp_mode is only valid for HTTP/UTCP transports")

        return self


__all__ = [
    "ArtifactExtractionConfig",
    "ArtifactFieldConfig",
    "AuthType",
    "BinaryDetectionConfig",
    "DEFAULT_BINARY_SIGNATURES",
    "ExternalToolConfig",
    "McpTransportMode",
    "OutputTransformer",
    "ResourceHandlingConfig",
    "RetryPolicy",
    "TransportType",
    "UtcpMode",
]
