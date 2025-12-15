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

from .config import AuthType, ExternalToolConfig, TransportType

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


__all__ = ["POPULAR_MCP_SERVERS", "get_preset"]
