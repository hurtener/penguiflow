---
name: library-implementation-principles
description: Guidelines for implementing generalizable solutions in the penguiflow library. Use when modifying library code, adding features, or fixing bugs in penguiflow core.
---

# Library Implementation Principles

## Core Principle: Generalizability

When solving specific issues in the penguiflow library, implementations MUST be generalizable. Never hardcode solutions for specific use cases.

## Anti-Pattern Examples

### Bad: Hardcoding Transport Type

```python
# WRONG - Forces StreamableHTTP for all URL-based connections
if connection.startswith(("http://", "https://")):
    transport = StreamableHttpTransport(url=connection, headers=auth_headers)
```

### Bad: Assuming Auth Mechanism

```python
# WRONG - Only handles one auth type at connection time
if self.config.auth_type == AuthType.BEARER:
    # handle bearer
# Missing: API_KEY, COOKIE, OAUTH2_USER, custom auth
```

## Correct Patterns

### 1. Transport Detection

FastMCP supports multiple transports - let it detect or make it configurable:

```python
# GOOD - Configurable MCP transport mode (in config.py)
class McpTransportMode(str, Enum):
    """MCP transport mode for URL-based connections."""
    AUTO = "auto"  # Let FastMCP auto-detect (default when no auth headers)
    SSE = "sse"  # Force Server-Sent Events transport (legacy)
    STREAMABLE_HTTP = "streamable_http"  # Force Streamable HTTP (modern)

class ExternalToolConfig(BaseModel):
    # ...
    mcp_transport_mode: McpTransportMode = Field(
        default=McpTransportMode.AUTO,
        description="For MCP over HTTP: auto-detect, sse, or streamable_http",
    )

# GOOD - In ToolNode._resolve_mcp_url_transport()
def _resolve_mcp_url_transport(self, connection, auth_headers, sse_cls, streamable_cls):
    mode = self.config.mcp_transport_mode

    # Explicit SSE mode
    if mode == McpTransportMode.SSE:
        return sse_cls(url=connection, headers=auth_headers or {})

    # Explicit StreamableHTTP mode
    if mode == McpTransportMode.STREAMABLE_HTTP:
        return streamable_cls(url=connection, headers=auth_headers or {})

    # AUTO mode without auth - let FastMCP auto-detect
    if not auth_headers:
        return connection

    # AUTO mode with auth - detect from URL pattern
    if "/sse" in connection.lower():
        return sse_cls(url=connection, headers=auth_headers)

    return streamable_cls(url=connection, headers=auth_headers)
```

### 2. Auth Type Extensibility

Support all auth types uniformly:

```python
# GOOD - Extensible auth resolution
class AuthType(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    COOKIE = "cookie"
    OAUTH2_USER = "oauth2_user"
    CUSTOM = "custom"  # For custom auth handlers

def _get_auth_headers(self) -> dict[str, str]:
    """Get auth headers based on config - supports all auth types."""
    handlers = {
        AuthType.NONE: lambda: {},
        AuthType.API_KEY: self._get_api_key_headers,
        AuthType.BEARER: self._get_bearer_headers,
        AuthType.COOKIE: self._get_cookie_headers,
        # OAuth2 is async and handled separately
    }
    handler = handlers.get(self.config.auth_type)
    return handler() if handler else {}
```

### 3. Configuration Over Code

Make behavior configurable, not hardcoded:

```python
# GOOD - Configuration-driven behavior (Pydantic model in config.py)
class ExternalToolConfig(BaseModel):
    name: str
    transport: TransportType = TransportType.MCP
    mcp_transport_mode: McpTransportMode = McpTransportMode.AUTO
    auth_type: AuthType = AuthType.NONE
    auth_config: dict[str, Any] = Field(default_factory=dict)
    # ... other config
```

## MCP Transport Guidelines

### FastMCP Auto-Detection

FastMCP can auto-detect transport when given a URL:
- `/mcp` endpoints → typically StreamableHTTP
- `/sse` endpoints → typically SSE

But some servers don't follow this convention.

### When to Force Transport

```python
from penguiflow.tools.config import ExternalToolConfig, McpTransportMode, TransportType

# SSE endpoints (legacy)
ExternalToolConfig(
    name="legacy-server",
    transport=TransportType.MCP,
    connection="https://server.com/sse",
    mcp_transport_mode=McpTransportMode.SSE,
)

# StreamableHTTP endpoints (modern)
ExternalToolConfig(
    name="modern-server",
    transport=TransportType.MCP,
    connection="https://server.com/mcp",
    mcp_transport_mode=McpTransportMode.STREAMABLE_HTTP,
)

# Let FastMCP decide (default)
ExternalToolConfig(
    name="auto-detect",
    transport=TransportType.MCP,
    connection="https://server.com/mcp",
    mcp_transport_mode=McpTransportMode.AUTO,
)
```

## Checklist Before Implementation

1. [ ] Does this work for ALL auth types, not just the one I'm testing?
2. [ ] Does this work for ALL transport types (SSE, StreamableHTTP, STDIO)?
3. [ ] Is behavior configurable via ExternalToolConfig?
4. [ ] Are there sensible defaults that don't break existing usage?
5. [ ] Is error handling comprehensive?
6. [ ] Does it follow existing patterns in the codebase?

## Testing Requirements

When adding new features:
1. Test with multiple auth types
2. Test with multiple transport types
3. Test with edge cases (missing config, invalid values)
4. Add unit tests AND integration tests

## DNA Pattern References

- See `penguiflow/planner/` for ReactPlanner patterns
- See `penguiflow/tools/config.py` for configuration patterns
- See `test_generation/*/src/*/external_tools.py` for usage patterns
