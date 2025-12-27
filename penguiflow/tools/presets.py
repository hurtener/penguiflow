"""Popular MCP server presets for ToolNode.

IMPORTANT: These are convenience presets for local development and learning.
They use `npx -y` which requires Node.js to be installed on your system.

For production deployments or containerized environments, consider these alternatives:

1. **Run MCP servers as separate services**: Deploy MCP servers as standalone
   processes or containers and connect via SSE/HTTP URLs instead of stdio.

2. **Use UTCP transport**: For direct API access without MCP protocol overhead,
   configure tools to use UTCP transport (see UTCP_INTEGRATION_ANALYSIS.md).

3. **Include Node.js in container**: If you need to use these presets in Docker,
   ensure your container image includes Node.js (e.g., use node:alpine as base).

Example for production:
    # Instead of using the preset
    config = ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="http://mcp-github-service:8080/sse",  # Separate service
        auth_type=AuthType.OAUTH2_USER,
    )
"""

from __future__ import annotations

from typing import Any

from .config import (
    ArtifactExtractionConfig,
    ArtifactFieldConfig,
    AuthType,
    BinaryDetectionConfig,
    ExternalToolConfig,
    ResourceHandlingConfig,
    TransportType,
)

POPULAR_MCP_SERVERS = {
    # NOTE: All presets use `npx -y` for quick local development.
    # These require Node.js installed. For production, see module docstring above.
    "github": ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        auth_type=AuthType.OAUTH2_USER,
        description="GitHub repositories, issues, pull requests",
    ),
    "filesystem": ExternalToolConfig(
        name="filesystem",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-filesystem /data",
        auth_type=AuthType.NONE,
        description="Read/write local filesystem",
    ),
    "postgres": ExternalToolConfig(
        name="postgres",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-postgres",
        env={"DATABASE_URL": "${DATABASE_URL}"},
        auth_type=AuthType.NONE,
        description="Query PostgreSQL databases",
    ),
    "slack": ExternalToolConfig(
        name="slack",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-slack",
        auth_type=AuthType.OAUTH2_USER,
        description="Slack channels, messages, users",
    ),
    "google-drive": ExternalToolConfig(
        name="google-drive",
        transport=TransportType.MCP,
        connection="npx -y @anthropic/mcp-server-google-drive",
        auth_type=AuthType.OAUTH2_USER,
        description="Google Drive files and folders",
    ),
    "duckduckgo": ExternalToolConfig(
        name="duckduckgo",
        transport=TransportType.MCP,
        connection="npx -y duckduckgo-mcp-server",
        auth_type=AuthType.NONE,
        description="Web search and content retrieval via DuckDuckGo",
    ),
    "brave-search": ExternalToolConfig(
        name="brave-search",
        transport=TransportType.MCP,
        connection="npx -y @anthropic/mcp-server-brave-search",
        env={"BRAVE_API_KEY": "${BRAVE_API_KEY}"},
        auth_type=AuthType.NONE,  # Key passed via env, not auth_config
        description="Web search via Brave Search API (requires BRAVE_API_KEY env var)",
    ),
    # NOTE: Redis MCP server is Python-based (uvx), not Node.js (npx)
    "redis": ExternalToolConfig(
        name="redis",
        transport=TransportType.MCP,
        connection="uvx --from redis-mcp-server@latest redis-mcp-server",
        env={"REDIS_HOST": "${REDIS_HOST}", "REDIS_PORT": "${REDIS_PORT}"},
        auth_type=AuthType.NONE,
        description="Redis database operations (requires uvx/Python)",
    ),
}


def get_preset(name: str) -> ExternalToolConfig:
    """Get a pre-configured MCP server config."""
    if name not in POPULAR_MCP_SERVERS:
        raise ValueError(f"Unknown preset: {name}. Available: {list(POPULAR_MCP_SERVERS.keys())}")
    return POPULAR_MCP_SERVERS[name]


# -----------------------------------------------------------------------------
# Artifact Extraction Presets
# -----------------------------------------------------------------------------

TABLEAU_ARTIFACT_PRESET = ArtifactExtractionConfig(
    max_inline_size=5_000,  # Tableau responses can be large
    auto_artifact_large_content=True,
    binary_detection=BinaryDetectionConfig(
        enabled=True,
        min_size_for_detection=500,  # Tableau PDFs start small
        require_magic_bytes=True,
    ),
    resources=ResourceHandlingConfig(
        enabled=True,
        auto_read_if_size_under_bytes=0,  # Never auto-read large resources
        inline_text_if_under_chars=5_000,
        cache_reads_to_artifacts=True,
    ),
    tool_fields={
        "download_workbook": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="pdf",
                mime_type="application/pdf",
                summary_template="Downloaded workbook '{name}' as PDF ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
        "get_view_as_pdf": [
            ArtifactFieldConfig(
                field_path="pdf_data",
                content_type="pdf",
                mime_type="application/pdf",
                summary_template="Exported view '{view_name}' as PDF ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
        "get_view_as_image": [
            ArtifactFieldConfig(
                field_path="image_data",
                content_type="image",
                mime_type="image/png",
                summary_template="Exported view '{view_name}' as image ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
        "export_dashboard": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="pdf",
                mime_type="application/pdf",
                summary_template="Exported dashboard '{name}' ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
    },
    default_binary_summary="Tableau content stored as artifact ({mime_type}, {size} bytes). Artifact ID: {artifact_id}",
)


GITHUB_ARTIFACT_PRESET = ArtifactExtractionConfig(
    max_inline_size=10_000,  # GitHub file contents can be large
    auto_artifact_large_content=True,
    binary_detection=BinaryDetectionConfig(
        enabled=True,
        min_size_for_detection=1000,
        require_magic_bytes=True,
    ),
    resources=ResourceHandlingConfig(
        enabled=True,
        auto_read_if_size_under_bytes=50_000,  # Auto-read small files
        inline_text_if_under_chars=10_000,
        cache_reads_to_artifacts=True,
    ),
    tool_fields={
        "get_file_contents": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="binary",
                mime_type=None,  # Auto-detect
                summary_template="Retrieved file '{path}' ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
        "download_artifact": [
            ArtifactFieldConfig(
                field_path="data",
                content_type="binary",
                mime_type="application/zip",
                summary_template="Downloaded artifact '{name}' ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
        "get_release_asset": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="binary",
                mime_type=None,
                summary_template="Downloaded release asset '{name}' ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
    },
    default_binary_summary="GitHub content stored as artifact ({mime_type}, {size} bytes). Artifact ID: {artifact_id}",
)


FILESYSTEM_ARTIFACT_PRESET = ArtifactExtractionConfig(
    max_inline_size=50_000,  # Allow larger inline for text files
    auto_artifact_large_content=True,
    binary_detection=BinaryDetectionConfig(
        enabled=True,
        min_size_for_detection=500,
        require_magic_bytes=True,
    ),
    resources=ResourceHandlingConfig(
        enabled=True,
        auto_read_if_size_under_bytes=100_000,  # Auto-read small files
        inline_text_if_under_chars=50_000,
        cache_reads_to_artifacts=True,
    ),
    tool_fields={
        "read_file": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="binary",
                mime_type=None,  # Auto-detect from filename
                summary_template="Read file '{path}' ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
    },
    default_binary_summary="File content stored as artifact ({mime_type}, {size} bytes). Artifact ID: {artifact_id}",
    default_text_summary="Large text file stored as artifact ({size} chars). Artifact ID: {artifact_id}",
)


GOOGLE_DRIVE_ARTIFACT_PRESET = ArtifactExtractionConfig(
    max_inline_size=10_000,
    auto_artifact_large_content=True,
    binary_detection=BinaryDetectionConfig(
        enabled=True,
        min_size_for_detection=1000,
        require_magic_bytes=True,
    ),
    resources=ResourceHandlingConfig(
        enabled=True,
        auto_read_if_size_under_bytes=100_000,
        inline_text_if_under_chars=10_000,
        cache_reads_to_artifacts=True,
    ),
    tool_fields={
        "download_file": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="binary",
                mime_type=None,
                summary_template="Downloaded '{name}' from Google Drive ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
        "export_document": [
            ArtifactFieldConfig(
                field_path="content",
                content_type="pdf",
                mime_type="application/pdf",
                summary_template="Exported document '{name}' as PDF ({size} bytes). Artifact ID: {artifact_id}",
            ),
        ],
    },
    default_binary_summary=(
        "Google Drive content stored as artifact ({mime_type}, {size} bytes). "
        "Artifact ID: {artifact_id}"
    ),
)


# Registry of artifact extraction presets
ARTIFACT_PRESETS: dict[str, ArtifactExtractionConfig] = {
    "tableau": TABLEAU_ARTIFACT_PRESET,
    "github": GITHUB_ARTIFACT_PRESET,
    "filesystem": FILESYSTEM_ARTIFACT_PRESET,
    "google-drive": GOOGLE_DRIVE_ARTIFACT_PRESET,
}


def get_artifact_preset(name: str) -> ArtifactExtractionConfig:
    """Get an artifact extraction preset by name.

    Args:
        name: Preset name (e.g., 'tableau', 'github', 'filesystem').

    Returns:
        ArtifactExtractionConfig for the named preset.

    Raises:
        KeyError: If the preset name is not found.

    Example:
        from penguiflow.tools.presets import get_artifact_preset

        config = ExternalToolConfig(
            name="tableau",
            transport=TransportType.MCP,
            connection="http://tableau-mcp:8080/sse",
            artifact_extraction=get_artifact_preset("tableau"),
        )
    """
    if name not in ARTIFACT_PRESETS:
        available = ", ".join(sorted(ARTIFACT_PRESETS.keys()))
        raise KeyError(f"Unknown artifact preset '{name}'. Available: {available}")
    return ARTIFACT_PRESETS[name]


def get_artifact_preset_with_overrides(
    name: str,
    **overrides: Any,
) -> ArtifactExtractionConfig:
    """Get an artifact preset with field overrides.

    Args:
        name: Preset name.
        **overrides: Field overrides to apply.

    Returns:
        ArtifactExtractionConfig with overrides applied.

    Example:
        config = get_artifact_preset_with_overrides(
            "tableau",
            max_inline_size=2000,
        )
    """
    base = get_artifact_preset(name)
    if not overrides:
        return base

    data = base.model_dump()
    data.update(overrides)
    return ArtifactExtractionConfig.model_validate(data)


def merge_artifact_preset(
    base: ArtifactExtractionConfig,
    preset_name: str,
) -> ArtifactExtractionConfig:
    """Merge a preset's tool_fields into an existing config.

    Useful for combining preset tool field mappings with custom settings.

    Args:
        base: The base configuration to merge into.
        preset_name: Name of the preset to merge from.

    Returns:
        New ArtifactExtractionConfig with merged tool_fields.

    Example:
        custom = ArtifactExtractionConfig(max_inline_size=2000)
        merged = merge_artifact_preset(custom, "tableau")
    """
    preset = get_artifact_preset(preset_name)
    merged_fields = dict(base.tool_fields)
    merged_fields.update(preset.tool_fields)

    return ArtifactExtractionConfig(
        max_inline_size=base.max_inline_size,
        auto_artifact_large_content=base.auto_artifact_large_content,
        binary_detection=base.binary_detection,
        resources=base.resources,
        tool_fields=merged_fields,
        default_binary_summary=base.default_binary_summary,
        default_text_summary=base.default_text_summary,
    )


def list_artifact_presets() -> list[str]:
    """List available artifact preset names."""
    return sorted(ARTIFACT_PRESETS.keys())


def get_artifact_preset_info(name: str) -> dict[str, Any]:
    """Get information about an artifact preset.

    Args:
        name: Preset name.

    Returns:
        Dict with preset metadata.
    """
    preset = get_artifact_preset(name)
    return {
        "name": name,
        "max_inline_size": preset.max_inline_size,
        "auto_artifact_large_content": preset.auto_artifact_large_content,
        "binary_detection_enabled": preset.binary_detection.enabled,
        "resources_enabled": preset.resources.enabled,
        "tool_fields": list(preset.tool_fields.keys()),
    }


__all__ = [
    # Connection presets
    "POPULAR_MCP_SERVERS",
    "get_preset",
    # Artifact extraction presets
    "ARTIFACT_PRESETS",
    "TABLEAU_ARTIFACT_PRESET",
    "GITHUB_ARTIFACT_PRESET",
    "FILESYSTEM_ARTIFACT_PRESET",
    "GOOGLE_DRIVE_ARTIFACT_PRESET",
    "get_artifact_preset",
    "get_artifact_preset_with_overrides",
    "merge_artifact_preset",
    "list_artifact_presets",
    "get_artifact_preset_info",
]
