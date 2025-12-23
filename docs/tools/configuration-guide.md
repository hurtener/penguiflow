# ToolNode Configuration Guide

This guide covers environment variable handling, tool namespacing, collision detection, and configuration best practices for ToolNode v2.

## Environment Variable Substitution

ToolNode supports `${VAR}` syntax for environment variable substitution in configuration values.

### Syntax

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType, AuthType

github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        env={
            "GITHUB_TOKEN": "${GITHUB_TOKEN}",       # Substituted from environment
            "GITHUB_ORG": "${MY_ORG}",               # Custom env var name
        },
        auth_type=AuthType.BEARER,
        auth_config={
            "token": "${GITHUB_TOKEN}",             # Also works in auth_config
        },
    ),
    registry=registry,
)
```

### Fail-Fast Behavior

**ToolNode fails immediately if a referenced environment variable is not set.**

```python
# If MISSING_VAR is not in os.environ:
config = ExternalToolConfig(
    name="test",
    transport=TransportType.HTTP,
    connection="https://api.example.com",
    auth_config={"token": "${MISSING_VAR}"},
)

node = ToolNode(config=config, registry=registry)

# Raises ToolAuthError:
# "Missing required environment variable 'MISSING_VAR' for ToolNode 'test'"
```

This fail-fast approach ensures:
1. **Early detection** - Configuration errors surface at startup, not runtime
2. **Clear error messages** - Exact variable name and ToolNode name in error
3. **Security** - Prevents accidental operation with missing credentials

### Best Practices

```python
# DO: Use explicit env var names matching your deployment
auth_config={"token": "${STRIPE_SECRET_KEY}"}

# DO: Document required env vars in your deployment
# Required: GITHUB_TOKEN, STRIPE_SECRET_KEY, DATABASE_URL

# DON'T: Use generic names that might conflict
auth_config={"token": "${TOKEN}"}  # Ambiguous

# DON'T: Hardcode secrets
auth_config={"token": "sk_live_..."}  # Security risk
```

### Multiple Substitutions

Multiple variables can be used in a single value:

```python
env={
    "DATABASE_URL": "postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}/${DB_NAME}",
}
```

---

## Tool Namespacing

All tools from a ToolNode are namespaced with `{config.name}.{tool_name}`.

### How It Works

```python
# ToolNode with name="github"
github = ToolNode(
    config=ExternalToolConfig(
        name="github",  # Namespace prefix
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
    ),
    registry=registry,
)
await github.connect()

# MCP server exposes: create_issue, list_repos, get_user
# After namespacing: github.create_issue, github.list_repos, github.get_user

tools = github.get_tools()
print([t.name for t in tools])
# ['github.create_issue', 'github.list_repos', 'github.get_user']
```

### Benefits

1. **Clarity** - Clear origin of each tool in planner traces
2. **Collision prevention** - Multiple ToolNodes with same tool names won't conflict
3. **Filtering** - Easy to filter tools by namespace in planning hints

### Using Namespaced Tools

The planner sees and uses the namespaced names:

```python
# In planner's reasoning:
# "I'll use github.create_issue to file a bug report"

# When calling tools, ToolNode handles the mapping:
# github.create_issue → calls MCP tool "create_issue"
```

---

## Collision Detection

ToolNode prevents tool name collisions at registration time.

### Collision Types

1. **Self-collision** - Same tool name within a ToolNode (from duplicate discovery)
2. **Cross-ToolNode collision** - Same namespaced name from different ToolNodes
3. **Native tool collision** - Namespaced name conflicts with a native `@tool`

### Error Messages

```python
# Self-collision (duplicate tool in MCP server response)
# Raises: ToolNodeError("Duplicate tool name 'github.create_issue' in ToolNode 'github'")

# Cross-ToolNode collision
github1 = ToolNode(config=ExternalToolConfig(name="github", ...), registry=registry)
github2 = ToolNode(config=ExternalToolConfig(name="github", ...), registry=registry)

await github1.connect()  # OK
await github2.connect()  # Raises: ToolNodeError("Tool name collision for 'github.create_issue'")

# Native tool collision
@tool(desc="My custom GitHub tool")
async def github_create_issue(args: Args, ctx: ToolContext) -> Result:
    ...

# If ToolNode also has github.create_issue:
# Raises: ToolNodeError("Tool name collision for 'github.create_issue' (native tool or another ToolNode)")
```

### Avoiding Collisions

```python
# Use unique, descriptive names
github_primary = ToolNode(
    config=ExternalToolConfig(name="github_prod", ...),
    registry=registry,
)

github_staging = ToolNode(
    config=ExternalToolConfig(name="github_staging", ...),
    registry=registry,
)

# Results in: github_prod.create_issue, github_staging.create_issue
```

### Shared Registry Pattern

**Always use a shared `ModelRegistry`** across all ToolNodes and native tools:

```python
from penguiflow.registry import ModelRegistry
from penguiflow.catalog import build_catalog

# Single shared registry
registry = ModelRegistry()

# Native tools use the registry
native_catalog = build_catalog([my_tool, another_tool], registry)

# ToolNodes use the same registry
github = ToolNode(config=github_config, registry=registry)
stripe = ToolNode(config=stripe_config, registry=registry)

await github.connect()
await stripe.connect()

# Combined catalog with guaranteed uniqueness
catalog = [*native_catalog, *github.get_tools(), *stripe.get_tools()]
```

---

## Tool Filtering

Filter which tools are exposed from a ToolNode using regex patterns.

### Basic Filtering

```python
# Only expose read operations
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        tool_filter=["get_.*", "list_.*", "search_.*"],  # Read-only tools
    ),
    registry=registry,
)
```

### Filter Patterns

```python
# Include specific tools
tool_filter=["create_issue", "close_issue"]

# Include by prefix
tool_filter=["repo_.*"]  # All repo_* tools

# Include by suffix
tool_filter=[".*_user"]  # All *_user tools

# Complex patterns
tool_filter=[
    "get_.*",           # All getters
    "list_.*",          # All listers
    "create_issue",     # Specific write operation
]
```

### Security Use Cases

```python
# Production: Read-only access
prod_github = ToolNode(
    config=ExternalToolConfig(
        name="github_readonly",
        transport=TransportType.MCP,
        connection="...",
        tool_filter=["get_.*", "list_.*", "search_.*"],
    ),
    registry=registry,
)

# Development: Full access
dev_github = ToolNode(
    config=ExternalToolConfig(
        name="github_full",
        transport=TransportType.MCP,
        connection="...",
        tool_filter=None,  # All tools (default)
    ),
    registry=registry,
)
```

---

## Arg Validation Telemetry

ToolNode can attach a planner arg-validation policy to all discovered tools. The
default is **telemetry-only** (no blocking), which helps catch placeholder args
from smaller models without false positives.

```python
ExternalToolConfig(
    name="github",
    transport=TransportType.MCP,
    connection="npx -y @modelcontextprotocol/server-github",
    arg_validation={
        "emit_suspect": True,       # telemetry only
        "reject_placeholders": False,
        "reject_autofill": False,
        "placeholders": ["<auto>"], # optional override
    },
)
```

When enabled, the planner emits `planner_args_suspect` events and records
details under `trajectory.metadata.suspect_args`.

---

## Transport Configuration

### MCP Transport

For MCP servers (stdio-based):

```python
ExternalToolConfig(
    name="github",
    transport=TransportType.MCP,
    connection="npx -y @modelcontextprotocol/server-github",  # Command to run
    env={"GITHUB_TOKEN": "${GITHUB_TOKEN}"},                  # Passed to subprocess
)
```

### UTCP Transport (Recommended for HTTP APIs)

For HTTP APIs with UTCP discovery:

```python
# Manual URL mode (recommended) - Full discovery from UTCP endpoint
ExternalToolConfig(
    name="weather",
    transport=TransportType.UTCP,
    connection="https://api.weather.com/.well-known/utcp.json",
    utcp_mode=UtcpMode.MANUAL_URL,
)

# Base URL mode - Limited discovery, generic HTTP templates
ExternalToolConfig(
    name="stripe",
    transport=TransportType.HTTP,
    connection="https://api.stripe.com/v1",
    utcp_mode=UtcpMode.BASE_URL,
)

# Auto mode (default) - Detects based on URL pattern
ExternalToolConfig(
    name="api",
    transport=TransportType.UTCP,
    connection="https://api.example.com/.well-known/utcp.json",
    utcp_mode=UtcpMode.AUTO,  # Detects .json → MANUAL_URL
)
```

### CLI Transport

For command-line tools via UTCP:

```python
ExternalToolConfig(
    name="ffmpeg",
    transport=TransportType.CLI,
    connection="https://ffmpeg-tools.example.com/.well-known/utcp.json",
)
```

---

## Authentication Configuration

### No Authentication

```python
ExternalToolConfig(
    name="public_api",
    transport=TransportType.HTTP,
    connection="https://api.public.com",
    auth_type=AuthType.NONE,
)
```

### API Key

```python
ExternalToolConfig(
    name="weather",
    transport=TransportType.HTTP,
    connection="https://api.weather.com/v1",
    auth_type=AuthType.API_KEY,
    auth_config={
        "api_key": "${WEATHER_API_KEY}",
        "header": "X-API-Key",  # Default: "X-API-Key"
    },
)
```

### Bearer Token

```python
ExternalToolConfig(
    name="stripe",
    transport=TransportType.HTTP,
    connection="https://api.stripe.com/v1",
    auth_type=AuthType.BEARER,
    auth_config={
        "token": "${STRIPE_SECRET_KEY}",
    },
)
```

### User-Level OAuth

```python
ExternalToolConfig(
    name="github",
    transport=TransportType.MCP,
    connection="npx -y @modelcontextprotocol/server-github",
    auth_type=AuthType.OAUTH2_USER,
    # auth_config not needed - OAuthManager handles it
)
```

Requires `OAuthManager` at ToolNode creation:

```python
oauth_manager = OAuthManager(
    providers={
        "github": OAuthProviderConfig(
            name="github",
            display_name="GitHub",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            client_id="${GITHUB_CLIENT_ID}",
            client_secret="${GITHUB_CLIENT_SECRET}",
            redirect_uri="https://myapp.com/oauth/callback",
            scopes=["repo", "user"],
        ),
    },
)

github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="...",
        auth_type=AuthType.OAUTH2_USER,
    ),
    registry=registry,
    auth_manager=oauth_manager,  # Required for OAUTH2_USER
)
```

---

## Resilience Configuration

### Retry Policy

```python
from penguiflow.tools import RetryPolicy

ExternalToolConfig(
    name="api",
    transport=TransportType.HTTP,
    connection="https://api.example.com",
    retry_policy=RetryPolicy(
        max_attempts=3,                        # 1-10, default: 3
        wait_exponential_min_s=0.1,            # Min backoff, default: 0.1
        wait_exponential_max_s=5.0,            # Max backoff, default: 5.0
        retry_on_status=[429, 500, 502, 503, 504],  # Status codes to retry
    ),
)
```

### Timeout

```python
ExternalToolConfig(
    name="slow_api",
    transport=TransportType.HTTP,
    connection="https://slow.api.com",
    timeout_s=60.0,  # 1-300 seconds, default: 30
)
```

### Concurrency

```python
ExternalToolConfig(
    name="rate_limited_api",
    transport=TransportType.HTTP,
    connection="https://api.ratelimited.com",
    max_concurrency=5,  # 1-100, default: 10
)
```

---

## Complete Configuration Example

```python
import os
from penguiflow.tools import (
    ToolNode,
    ExternalToolConfig,
    TransportType,
    AuthType,
    UtcpMode,
    RetryPolicy,
    OAuthManager,
    OAuthProviderConfig,
)
from penguiflow.registry import ModelRegistry

# Shared registry
registry = ModelRegistry()

# OAuth manager for user-level auth
oauth_manager = OAuthManager(
    providers={
        "github": OAuthProviderConfig(
            name="github",
            display_name="GitHub",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            client_id=os.environ["GITHUB_CLIENT_ID"],
            client_secret=os.environ["GITHUB_CLIENT_SECRET"],
            redirect_uri="https://myapp.com/oauth/callback",
            scopes=["repo", "user"],
        ),
    },
)

# MCP server with OAuth
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        description="GitHub repository management",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        auth_type=AuthType.OAUTH2_USER,
        timeout_s=30.0,
        max_concurrency=10,
        tool_filter=["create_issue", "list_issues", "get_repo"],
    ),
    registry=registry,
    auth_manager=oauth_manager,
)

# UTCP API with bearer token
stripe = ToolNode(
    config=ExternalToolConfig(
        name="stripe",
        description="Stripe payment processing",
        transport=TransportType.UTCP,
        connection="https://stripe.com/.well-known/utcp.json",
        utcp_mode=UtcpMode.MANUAL_URL,
        auth_type=AuthType.BEARER,
        auth_config={"token": "${STRIPE_SECRET_KEY}"},
        timeout_s=45.0,
        max_concurrency=5,
        retry_policy=RetryPolicy(
            max_attempts=3,
            wait_exponential_min_s=0.5,
            wait_exponential_max_s=10.0,
            retry_on_status=[429, 500, 502, 503, 504],
        ),
    ),
    registry=registry,
)

# HTTP API with API key
weather = ToolNode(
    config=ExternalToolConfig(
        name="weather",
        description="Weather forecasts",
        transport=TransportType.HTTP,
        connection="https://api.weather.com/v1",
        utcp_mode=UtcpMode.BASE_URL,
        auth_type=AuthType.API_KEY,
        auth_config={
            "api_key": "${WEATHER_API_KEY}",
            "header": "X-API-Key",
        },
        timeout_s=15.0,
        max_concurrency=20,
    ),
    registry=registry,
)

# Connect all ToolNodes
async def setup():
    await github.connect()
    await stripe.connect()
    await weather.connect()

    # Combined catalog
    catalog = [
        *github.get_tools(),
        *stripe.get_tools(),
        *weather.get_tools(),
    ]

    return catalog
```

---

## See Also

- [StateStore Implementation Guide](./statestore-guide.md)
- [Concurrency Configuration Guide](./concurrency-guide.md)
- [TOOLNODE_V2_PLAN.md](../proposals/TOOLNODE_V2_PLAN.md) - Full design specification
