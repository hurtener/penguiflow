# ToolNode v2: Universal External Tool Integration Plan

**Version:** 2.1
**Target Release:** v2.6.5+
**Status:** PROPOSAL
**Date:** December 2025

---

## Executive Summary

ToolNode provides unified external tool integration for Penguiflow agents. This revised plan leverages **FastMCP 2.x** and **python-utcp 1.x** to reduce implementation effort by **60-75%** while gaining enterprise-grade features.

**Core Value Proposition:**
- Zero wrapper code for MCP ecosystem (230+ servers)
- Any REST API with OpenAPI/UTCP auto-discovery
- User-level OAuth through existing HITL primitives
- Production-ready resilience (retries, timeouts, circuit breakers)

**Design Principles:**
- **FastMCP for MCP:** All MCP server communication goes through FastMCP (handles framing, OAuth, transport detection)
- **UTCP for everything else:** HTTP APIs, CLI tools, WebSocket - UTCP provides unified multi-protocol access
- **No bridge mixing:** We do NOT use utcp-mcp bridge; each library handles its native protocols directly

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ReactPlanner                                      │
│                         (existing, unchanged)                                │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                    Penguiflow Catalog (NodeSpec)                             │
│                         (existing, unchanged)                                │
└──────────┬───────────────────────────────────────────────────┬──────────────┘
           │                                                   │
┌──────────▼──────────┐                             ┌──────────▼──────────────┐
│   Native Tools      │                             │      ToolNode           │
│   (@tool decorator) │                             │   (new, ~500-600 LOC)   │
│   (existing)        │                             └──────────┬──────────────┘
└─────────────────────┘                                        │
                                            ┌──────────────────┴──────────────┐
                                            │                                 │
                                 ┌──────────▼─────────┐           ┌───────────▼───────────┐
                                 │  FastMCP Client    │           │    UTCP Client        │
                                 │  (MCP only)        │           │    (HTTP/CLI/WS)      │
                                 └──────────┬─────────┘           └───────────┬───────────┘
                                            │                                 │
                                            ▼                    ┌────────────┼────────────┐
                                    ┌───────────────┐            ▼            ▼            ▼
                                    │  MCP Servers  │     ┌──────────┐  ┌─────────┐  ┌───────────┐
                                    │  (stdio/SSE/  │     │   HTTP   │  │   CLI   │  │ WebSocket │
                                    │   HTTP)       │     │   APIs   │  │  Tools  │  │   APIs    │
                                    └───────────────┘     └──────────┘  └─────────┘  └───────────┘
```

---

## Dependencies

### New Dependencies

```toml
# pyproject.toml additions (planner dependency group)
[project.optional-dependencies]
planner = [
    "litellm>=1.77.3",
    "dspy>=3.0.3",
    "fastmcp>=2.13.0",
    "utcp>=1.1.0",
    "utcp-http>=1.0.0",
    "tenacity>=9.0.0",
    "aiohttp>=3.9.0",
]

tools-cli = ["utcp-cli>=1.0.0"]       # CLI tool support
tools-websocket = ["utcp-websocket>=1.0.0"]  # WebSocket support
```

### Why These Libraries

| Library | Purpose | Benefit |
|---------|---------|---------|
| **fastmcp** | MCP client/server | Eliminates ~800 lines of JSON-RPC code, handles framing correctly, 7+ OAuth providers, schema conversion built-in |
| **utcp** | Multi-protocol abstraction | Direct API calls, plugin architecture, schema conversion built-in |
| **utcp-http** | HTTP/REST tools | OpenAPI discovery, variable substitution for auth |
| **tenacity** | Retry logic | Battle-tested async retry with proper cancellation handling |
| **aiohttp** | OAuth callback HTTP client | Async token exchange for OAuth flows |

All ToolNode-specific runtime deps live in the `planner` dependency group to avoid inflating the core runtime; CLI/websocket extras remain opt-in.

### What We're NOT Using

| Library | Reason |
|---------|--------|
| **utcp-mcp** | Adds unnecessary indirection; FastMCP handles MCP better |
| **datamodel-code-generator** | FastMCP and UTCP already handle schema conversion |

---

## Component Design

### 1. Configuration Models

```python
# penguiflow/tools/config.py

from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field, model_validator


class TransportType(Enum):
    """Supported communication protocols."""
    MCP = "mcp"           # MCP via FastMCP (stdio/SSE/HTTP auto-detected)
    HTTP = "http"         # REST API via UTCP
    UTCP = "utcp"         # Native UTCP endpoint (manual URL)
    CLI = "cli"           # Command-line tools via UTCP


class AuthType(Enum):
    """Authentication methods."""
    NONE = "none"
    API_KEY = "api_key"           # Static API key (header injection)
    BEARER = "bearer"             # Static bearer token
    OAUTH2_USER = "oauth2_user"   # User-level OAuth (HITL)


class UtcpMode(Enum):
    """How to interpret UTCP connection string."""
    AUTO = "auto"           # Try manual_url first, fallback to base_url
    MANUAL_URL = "manual_url"   # Connection is a UTCP manual endpoint (recommended)
    BASE_URL = "base_url"       # Connection is a REST base URL (limited discovery)


class RetryPolicy(BaseModel):
    """Retry configuration using tenacity semantics."""
    max_attempts: int = Field(default=3, ge=1, le=10)
    wait_exponential_min_s: float = Field(default=0.1, ge=0.01)
    wait_exponential_max_s: float = Field(default=5.0, ge=0.1)
    retry_on_status: list[int] = Field(
        default_factory=lambda: [429, 500, 502, 503, 504]
    )


class ExternalToolConfig(BaseModel):
    """
    Configuration for an external tool source.

    Examples:
        # MCP server (FastMCP handles transport auto-detection)
        ExternalToolConfig(
            name="github",
            transport=TransportType.MCP,
            connection="npx -y @modelcontextprotocol/server-github",
            auth_type=AuthType.OAUTH2_USER,
        )

        # REST API via UTCP manual (recommended)
        ExternalToolConfig(
            name="weather",
            transport=TransportType.UTCP,
            connection="https://api.weather.com/.well-known/utcp.json",
            utcp_mode=UtcpMode.MANUAL_URL,
        )

        # REST API via base URL (limited, for simple APIs)
        ExternalToolConfig(
            name="stripe",
            transport=TransportType.HTTP,
            connection="https://api.stripe.com/v1",
            utcp_mode=UtcpMode.BASE_URL,
            auth_type=AuthType.BEARER,
            auth_config={"token": "${STRIPE_SECRET_KEY}"},
        )
    """
    # Identity
    name: str = Field(..., description="Unique namespace for tools (e.g., 'github')")
    description: str = Field(default="")

    # Transport
    transport: TransportType
    connection: str = Field(..., description="Connection string (command for MCP, URL for HTTP/UTCP)")

    # UTCP-specific: how to interpret the connection string
    utcp_mode: UtcpMode = Field(
        default=UtcpMode.AUTO,
        description="For HTTP/UTCP: how to interpret connection (manual_url recommended)"
    )

    # Environment (for MCP subprocess)
    env: dict[str, str] = Field(default_factory=dict)

    # Authentication
    auth_type: AuthType = Field(default=AuthType.NONE)
    auth_config: dict[str, str] = Field(default_factory=dict)

    # Resilience
    timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    max_concurrency: int = Field(default=10, ge=1, le=100)

    # Discovery filtering
    tool_filter: list[str] | None = Field(
        default=None,
        description="Regex patterns to include specific tools (None = all)"
    )

    @model_validator(mode="after")
    def validate_config(self) -> "ExternalToolConfig":
        """Validate transport-specific requirements."""
        # Auth validation
        if self.auth_type == AuthType.BEARER and "token" not in self.auth_config:
            raise ValueError("auth_type=BEARER requires auth_config.token")
        if self.auth_type == AuthType.API_KEY and "api_key" not in self.auth_config:
            raise ValueError("auth_type=API_KEY requires auth_config.api_key")

        # UTCP mode only applies to HTTP/UTCP transports
        if self.transport == TransportType.MCP and self.utcp_mode != UtcpMode.AUTO:
            raise ValueError("utcp_mode is only valid for HTTP/UTCP transports")

        return self
```

### 2. ToolNode Implementation

```python
# penguiflow/tools/node.py

import asyncio
import re
from typing import Any
from dataclasses import dataclass, field

from fastmcp import Client as MCPClient
from utcp import UtcpClient
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

from penguiflow.catalog import NodeSpec
from penguiflow.node import Node
from penguiflow.registry import ModelRegistry
from penguiflow.planner.context import ToolContext
from pydantic import BaseModel

from .config import ExternalToolConfig, TransportType, AuthType, UtcpMode
from .auth import OAuthManager
from .adapters import adapt_exception
from .errors import ToolNodeError, ToolAuthError


@dataclass
class ToolNode:
    """
    Unified external tool integration for Penguiflow.

    Wraps FastMCP (for MCP servers) and UTCP (for HTTP/CLI/multi-protocol)
    into a single interface that integrates with ReactPlanner's catalog.

    Example:
        # Shared registry (required - aligns with build_catalog pattern)
        registry = ModelRegistry()

        github = ToolNode(
            config=ExternalToolConfig(
                name="github",
                transport=TransportType.MCP,
                connection="npx -y @modelcontextprotocol/server-github",
                auth_type=AuthType.OAUTH2_USER,
            ),
            registry=registry,
            auth_manager=oauth_manager,
        )
        await github.connect()

        catalog = [*local_tools, *github.get_tools()]
        planner = ReactPlanner(catalog=catalog, llm="gpt-4")
    """

    config: ExternalToolConfig
    registry: ModelRegistry  # Required - shared across all tools (aligns with build_catalog)
    auth_manager: OAuthManager | None = None

    # Internal state
    _mcp_client: MCPClient | None = field(default=None, repr=False)
    _utcp_client: UtcpClient | None = field(default=None, repr=False)
    _tools: list[NodeSpec] = field(default_factory=list, repr=False)
    _tool_name_map: dict[str, str] = field(default_factory=dict, repr=False)  # namespaced -> original
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)
    _connected: bool = field(default=False, repr=False)

    def __post_init__(self):
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)

    async def connect(self) -> None:
        """Connect to tool source and discover available tools."""
        if self._connected:
            return

        match self.config.transport:
            case TransportType.MCP:
                await self._connect_mcp()
            case TransportType.HTTP | TransportType.UTCP | TransportType.CLI:
                await self._connect_utcp()

        self._connected = True

    async def _connect_mcp(self) -> None:
        """Connect via FastMCP client."""
        # FastMCP auto-detects transport from connection string
        self._mcp_client = MCPClient(self.config.connection)
        await self._mcp_client.__aenter__()

        # Discover tools - FastMCP returns proper schema objects
        mcp_tools = await self._mcp_client.list_tools()
        self._tools = self._convert_mcp_tools(mcp_tools)

    async def _connect_utcp(self) -> None:
        """Connect via UTCP client."""
        utcp_config = self._build_utcp_config()
        self._utcp_client = await UtcpClient.create(config=utcp_config)

        # Discover tools - UTCP returns Tool objects with inputs/outputs
        utcp_tools = await self._utcp_client.list_tools()
        self._tools = self._convert_utcp_tools(utcp_tools)

    def get_tools(self) -> list[NodeSpec]:
        """Return discovered tools as Penguiflow NodeSpec entries."""
        if not self._connected:
            raise ToolNodeError(f"ToolNode '{self.config.name}' not connected")
        return self._tools

    async def call(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> Any:
        """
        Execute a tool with auth resolution and resilience.

        May pause for OAuth via ctx.pause() if user auth is required.
        """
        async with self._semaphore:  # Concurrency control
            # Resolve auth (may trigger HITL OAuth pause)
            auth_headers = await self._resolve_auth(ctx)

            # Get the original tool name (what the client expects)
            original_name = self._tool_name_map.get(tool_name)
            if original_name is None:
                # Fallback: strip namespace prefix
                original_name = tool_name.removeprefix(f"{self.config.name}.")

            # Execute with retry
            return await self._call_with_retry(original_name, args, auth_headers)

    async def close(self) -> None:
        """Clean up resources."""
        self._connected = False
        if self._mcp_client:
            await self._mcp_client.__aexit__(None, None, None)
            self._mcp_client = None
        if self._utcp_client:
            self._utcp_client = None

    # ─── Auth Resolution ────────────────────────────────────────────────────────

    async def _resolve_auth(self, ctx: ToolContext) -> dict[str, str]:
        """Resolve authentication headers, pausing for OAuth if needed."""
        match self.config.auth_type:
            case AuthType.NONE:
                return {}

            case AuthType.API_KEY:
                key = self._substitute_env(self.config.auth_config.get("api_key", ""))
                header = self.config.auth_config.get("header", "X-API-Key")
                return {header: key}

            case AuthType.BEARER:
                token = self._substitute_env(self.config.auth_config.get("token", ""))
                return {"Authorization": f"Bearer {token}"}

            case AuthType.OAUTH2_USER:
                return await self._resolve_user_oauth(ctx)

        return {}

    async def _resolve_user_oauth(self, ctx: ToolContext) -> dict[str, str]:
        """Handle user-level OAuth with HITL pause/resume."""
        if not self.auth_manager:
            raise ToolAuthError(
                f"ToolNode '{self.config.name}' requires user OAuth "
                f"but no auth_manager was provided"
            )

        user_id = ctx.tool_context.get("user_id")
        if not user_id:
            raise ToolAuthError("user_id required in tool_context for OAuth")

        # Check for existing valid token
        token = await self.auth_manager.get_token(user_id, self.config.name)
        if token:
            return {"Authorization": f"Bearer {token}"}

        # No token - pause for OAuth consent
        trace_id = ctx.tool_context.get("trace_id", "")
        auth_request = self.auth_manager.get_auth_request(
            provider=self.config.name,
            user_id=user_id,
            trace_id=trace_id,
        )

        await ctx.pause(
            reason="external_event",
            payload={
                "pause_type": "oauth",
                "provider": self.config.name,
                **auth_request,
            }
        )

        # After resume, token should be available
        token = await self.auth_manager.get_token(user_id, self.config.name)
        if not token:
            raise ToolAuthError(f"OAuth for {self.config.name} was not completed")

        return {"Authorization": f"Bearer {token}"}

    # ─── Resilience ─────────────────────────────────────────────────────────────

    async def _call_with_retry(
        self,
        tool_name: str,
        args: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> Any:
        """Execute tool call with intelligent retry based on error category."""
        policy = self.config.retry_policy
        transport = "mcp" if self._mcp_client else "utcp"

        def should_retry(exc: BaseException) -> bool:
            """Determine if exception is retryable."""
            if isinstance(exc, asyncio.CancelledError):
                return False
            if isinstance(exc, ToolNodeError):
                return exc.is_retryable
            return isinstance(exc, (TimeoutError, ConnectionError, OSError))

        @retry(
            stop=stop_after_attempt(policy.max_attempts),
            wait=wait_exponential(
                min=policy.wait_exponential_min_s,
                max=policy.wait_exponential_max_s,
            ),
            retry=retry_if_exception(should_retry),
            reraise=True,
        )
        async def _execute():
            try:
                async with asyncio.timeout(self.config.timeout_s):
                    if self._mcp_client:
                        # MCP: pass tool name as-is (FastMCP expects "create_issue")
                        return await self._mcp_client.call_tool(tool_name, args)
                    else:
                        # UTCP: pass the full UTCP tool name (may include manual namespace)
                        return await self._utcp_client.call_tool(tool_name, args)
            except asyncio.CancelledError:
                raise  # Never wrap cancellation
            except ToolNodeError:
                raise  # Already wrapped
            except Exception as exc:
                # Wrap in our error hierarchy
                raise adapt_exception(exc, transport) from exc

        return await _execute()

    # ─── Tool Conversion ────────────────────────────────────────────────────────

    def _convert_mcp_tools(self, mcp_tools: list) -> list[NodeSpec]:
        """Convert MCP tool schemas to Penguiflow NodeSpec.

        FastMCP already provides proper Pydantic-compatible schemas,
        so we use them directly rather than re-parsing JSON Schema.
        """
        specs = []
        for tool in mcp_tools:
            if not self._matches_filter(tool.name):
                continue

            namespaced = f"{self.config.name}.{tool.name}"

            # Store mapping: namespaced -> original (for call routing)
            if namespaced in self._tool_name_map:
                raise ToolNodeError(f"Duplicate tool name '{namespaced}' in ToolNode '{self.config.name}'")
            self._tool_name_map[namespaced] = tool.name

            # FastMCP provides inputSchema as dict; create dynamic model
            # FastMCP handles complex schemas internally, we just wrap
            args_model = self._create_args_model(namespaced, tool.inputSchema or {})
            out_model = self._create_result_model(namespaced)

            # Register models in shared registry
            try:
                self.registry.register(namespaced, args_model, out_model)
            except ValueError as exc:
                raise ToolNodeError(
                    f"Tool name collision for '{namespaced}' (native tool or another ToolNode)"
                ) from exc

            # Create wrapper function with proper closure capture
            async def _tool_fn(
                args: BaseModel,
                ctx: ToolContext,
                *,
                _namespaced: str = namespaced,
            ) -> Any:
                return await self.call(_namespaced, args.model_dump(), ctx)

            specs.append(NodeSpec(
                node=Node(_tool_fn, name=namespaced),
                name=namespaced,
                desc=tool.description or "",
                args_model=args_model,
                out_model=out_model,
                side_effects="external",
                tags=("mcp", self.config.name),
                extra={"source": "mcp", "namespace": self.config.name},
            ))

        return specs

    def _convert_utcp_tools(self, utcp_tools: list) -> list[NodeSpec]:
        """Convert UTCP tool schemas to Penguiflow NodeSpec.

        UTCP provides Tool objects with inputs/outputs already parsed.
        """
        specs = []
        for tool in utcp_tools:
            # UTCP tool names may be "manual.tool_name" - extract just the tool
            parts = tool.name.split(".")
            original_tool_name = parts[-1] if len(parts) > 1 else tool.name

            if not self._matches_filter(original_tool_name):
                continue

            namespaced = f"{self.config.name}.{original_tool_name}"

            # Store mapping: namespaced -> UTCP's full name (for call routing)
            if namespaced in self._tool_name_map:
                raise ToolNodeError(f"Duplicate tool name '{namespaced}' in ToolNode '{self.config.name}'")
            self._tool_name_map[namespaced] = tool.name

            # UTCP provides inputs/outputs as dicts
            args_model = self._create_args_model(namespaced, tool.inputs or {})
            out_model = self._create_result_model(namespaced, tool.outputs)

            try:
                self.registry.register(namespaced, args_model, out_model)
            except ValueError as exc:
                raise ToolNodeError(
                    f"Tool name collision for '{namespaced}' (native tool or another ToolNode)"
                ) from exc

            async def _tool_fn(
                args: BaseModel,
                ctx: ToolContext,
                *,
                _namespaced: str = namespaced,
            ) -> Any:
                return await self.call(_namespaced, args.model_dump(), ctx)

            specs.append(NodeSpec(
                node=Node(_tool_fn, name=namespaced),
                name=namespaced,
                desc=tool.description or "",
                args_model=args_model,
                out_model=out_model,
                side_effects="external",
                tags=("utcp", self.config.name),
                extra={"source": "utcp", "namespace": self.config.name},
            ))

        return specs

        # Namespacing & collisions:
        # - All external tools register as "{config.name}.{tool}".
        # - `_tool_name_map` keeps the mapping to original names for call routing.
        # - We fail fast if a namespaced tool already exists (native tools or other ToolNodes),
        #   wrapping ModelRegistry's ValueError into ToolNodeError.

    # ─── Model Creation ─────────────────────────────────────────────────────────

    def _create_args_model(self, name: str, schema: dict) -> type[BaseModel]:
        """Create Pydantic model from JSON schema for tool arguments.

        Uses a simple approach for common cases. Complex nested schemas
        will use dict[str, Any] as a fallback - this is intentional for v2.6.5.
        The LLM can still work effectively with permissive types.
        """
        from pydantic import create_model

        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        if not props:
            # Empty schema -> accept any dict
            return create_model(f"{name}Args", data=(dict[str, Any] | None, None))

        fields: dict[str, tuple] = {}
        for prop_name, prop_schema in props.items():
            python_type = self._json_type_to_python(prop_schema)
            if prop_name in required:
                fields[prop_name] = (python_type, ...)
            else:
                fields[prop_name] = (python_type | None, None)

        return create_model(f"{name}Args", **fields)

    def _create_result_model(self, name: str, schema: dict | None = None) -> type[BaseModel]:
        """Create Pydantic model for tool results.

        Results are more permissive - we accept whatever the tool returns.
        """
        from pydantic import create_model

        # Most tools return arbitrary JSON; use permissive model
        return create_model(f"{name}Result", result=(Any, None))

    def _json_type_to_python(self, prop_schema: dict) -> type:
        """Map JSON schema type to Python type.

        For complex types (nested objects, arrays of objects, oneOf, etc.),
        we fallback to permissive types. This is intentional for v2.6.5.
        """
        json_type = prop_schema.get("type", "string")

        # Simple types
        simple_mapping = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
        }

        if json_type in simple_mapping:
            return simple_mapping[json_type]

        # Arrays - check if items are simple
        if json_type == "array":
            items = prop_schema.get("items", {})
            items_type = items.get("type")
            if items_type in simple_mapping:
                return list[simple_mapping[items_type]]
            # Complex array items -> list[Any]
            return list

        # Objects and complex types -> dict[str, Any]
        return dict[str, Any]

    # ─── UTCP Config ────────────────────────────────────────────────────────────

    def _build_utcp_config(self) -> dict:
        """Build UTCP client configuration based on utcp_mode."""
        mode = self.config.utcp_mode

        # Resolve actual mode if AUTO
        if mode == UtcpMode.AUTO:
            # If URL looks like a manual endpoint, use manual_url mode
            if self.config.connection.endswith((".json", "/utcp", "/.well-known/utcp")):
                mode = UtcpMode.MANUAL_URL
            else:
                mode = UtcpMode.BASE_URL

        if mode == UtcpMode.MANUAL_URL:
            # Point UTCP at the manual URL for full discovery
            return {
                "manuals": [self.config.connection],
                "variables": self._build_utcp_variables(),
            }
        else:
            # BASE_URL mode: build a simple inline manual
            # This is limited - only creates a generic HTTP call template
            call_template_type = "cli" if self.config.transport == TransportType.CLI else "http"
            return {
                "manual_call_templates": [{
                    "name": self.config.name,
                    "call_template_type": call_template_type,
                    "url": self.config.connection,
                    "http_method": "POST",
                }],
                "variables": self._build_utcp_variables(),
            }

    def _build_utcp_variables(self) -> dict[str, str]:
        """Build UTCP variable substitutions from env and auth_config."""
        variables = {}

        # Add environment variables
        for k, v in self.config.env.items():
            variables[k] = self._substitute_env(v)

        # Add auth config (tokens, api keys, etc.)
        for k, v in self.config.auth_config.items():
            variables[k] = self._substitute_env(v)

        return variables

    # ─── Helpers ────────────────────────────────────────────────────────────────

    def _matches_filter(self, tool_name: str) -> bool:
        """Check if tool matches configured filter patterns."""
        if not self.config.tool_filter:
            return True
        return any(re.match(p, tool_name) for p in self.config.tool_filter)

    def _substitute_env(self, value: str) -> str:
        """Substitute ${VAR} patterns with environment variables, failing fast on missing values."""
        import os
        import warnings

        pattern = r"\$\{([^}]+)\}"

        def _replace(match: re.Match[str]) -> str:
            var = match.group(1)
            val = os.environ.get(var)
            if val is None:
                warnings.warn(
                    f"Environment variable '{var}' not set for ToolNode '{self.config.name}'",
                    DeprecationWarning,
                    stacklevel=2,
                )
                raise ToolAuthError(
                    f"Missing required environment variable '{var}' for ToolNode '{self.config.name}'"
                )
            return val

        return re.sub(pattern, _replace, value)
```

### 3. OAuth Manager (Simplified)

```python
# penguiflow/tools/auth.py

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol

import aiohttp


class TokenStore(Protocol):
    """Protocol for token persistence. Implement for Redis, Postgres, etc."""

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        """Store access token."""

    async def get(self, user_id: str, provider: str) -> str | None:
        """Get valid access token, or None if expired/missing."""

    async def delete(self, user_id: str, provider: str) -> None:
        """Delete token."""


class InMemoryTokenStore:
    """Simple in-memory token store for development."""

    def __init__(self):
        self._tokens: dict[tuple[str, str], tuple[str, float | None]] = {}

    async def store(self, user_id: str, provider: str, token: str, expires_at: float | None) -> None:
        self._tokens[(user_id, provider)] = (token, expires_at)

    async def get(self, user_id: str, provider: str) -> str | None:
        data = self._tokens.get((user_id, provider))
        if not data:
            return None
        token, expires_at = data
        if expires_at and time.time() > expires_at:
            await self.delete(user_id, provider)
            return None
        return token

    async def delete(self, user_id: str, provider: str) -> None:
        self._tokens.pop((user_id, provider), None)


@dataclass
class OAuthProviderConfig:
    """Configuration for an OAuth provider."""
    name: str
    display_name: str
    auth_url: str
    token_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: list[str] = field(default_factory=list)


@dataclass
class OAuthManager:
    """
    Manages user OAuth flows with HITL integration.

    Flow:
    1. Tool calls get_token() - returns None if not authenticated
    2. Tool calls get_auth_request() to generate OAuth URL
    3. Tool pauses via ctx.pause(auth_request=...)
    4. User completes OAuth in browser
    5. Callback calls handle_callback() to exchange code for token
    6. Application resumes planner with planner.resume(token)
    7. Tool retries, now with valid token
    """

    providers: dict[str, OAuthProviderConfig]
    token_store: TokenStore = field(default_factory=InMemoryTokenStore)

    _pending: dict[str, dict] = field(default_factory=dict, repr=False)

    async def get_token(self, user_id: str, provider: str) -> str | None:
        """Get valid token for user+provider, or None."""
        return await self.token_store.get(user_id, provider)

    def get_auth_request(
        self,
        provider: str,
        user_id: str,
        trace_id: str,
    ) -> dict:
        """Generate OAuth authorization request for HITL pause payload."""
        config = self.providers.get(provider)
        if not config:
            raise ValueError(f"Unknown OAuth provider: {provider}")

        state = secrets.token_urlsafe(32)
        self._pending[state] = {
            "user_id": user_id,
            "trace_id": trace_id,
            "provider": provider,
            "created_at": time.time(),
        }

        params = {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": " ".join(config.scopes),
            "state": state,
            "response_type": "code",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())

        return {
            "display_name": config.display_name,
            "auth_url": f"{config.auth_url}?{query}",
            "scopes": config.scopes,
        }

    async def handle_callback(self, code: str, state: str) -> tuple[str, str]:
        """
        Handle OAuth callback. Returns (user_id, trace_id) for resuming.

        Call this from your /oauth/callback endpoint.
        """
        pending = self._pending.pop(state, None)
        if not pending:
            raise ValueError("Invalid or expired OAuth state")

        if time.time() - pending["created_at"] > 600:  # 10 min timeout
            raise ValueError("OAuth request expired")

        config = self.providers[pending["provider"]]

        # Exchange code for token
        async with aiohttp.ClientSession() as session:
            async with session.post(
                config.token_url,
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "code": code,
                    "redirect_uri": config.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            ) as resp:
                result = await resp.json()

        if "error" in result:
            raise ValueError(f"OAuth error: {result.get('error_description', result['error'])}")

        # Calculate expiry
        expires_at = None
        if "expires_in" in result:
            expires_at = time.time() + result["expires_in"]

        # Store token
        await self.token_store.store(
            pending["user_id"],
            pending["provider"],
            result["access_token"],
            expires_at,
        )

        return pending["user_id"], pending["trace_id"]
```

### 4. Error Types

```python
# penguiflow/tools/errors.py

from enum import Enum
from typing import Any


class ErrorCategory(Enum):
    """Classification for retry decisions."""
    RETRYABLE_SERVER = "retryable_server"      # 500-504, transient
    RETRYABLE_RATE_LIMIT = "retryable_rate"    # 429, backoff required
    NON_RETRYABLE_CLIENT = "non_retryable"     # 400-428 (except 429)
    AUTH_REQUIRED = "auth_required"            # 401, 403
    NETWORK = "network"                        # Connection errors
    CANCELLED = "cancelled"                    # Task cancelled
    UNKNOWN = "unknown"


class ToolNodeError(Exception):
    """Base exception for ToolNode errors."""

    category: ErrorCategory = ErrorCategory.UNKNOWN
    status_code: int | None = None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        category: ErrorCategory | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.category = category or self._infer_category(status_code)
        self.__cause__ = cause

    def _infer_category(self, status_code: int | None) -> ErrorCategory:
        if status_code is None:
            return ErrorCategory.UNKNOWN
        if status_code == 429:
            return ErrorCategory.RETRYABLE_RATE_LIMIT
        if 500 <= status_code <= 504:
            return ErrorCategory.RETRYABLE_SERVER
        if status_code in (401, 403):
            return ErrorCategory.AUTH_REQUIRED
        if 400 <= status_code < 500:
            return ErrorCategory.NON_RETRYABLE_CLIENT
        return ErrorCategory.UNKNOWN

    @property
    def is_retryable(self) -> bool:
        return self.category in (
            ErrorCategory.RETRYABLE_SERVER,
            ErrorCategory.RETRYABLE_RATE_LIMIT,
            ErrorCategory.NETWORK,
        )

    @property
    def retry_after_seconds(self) -> float | None:
        """Hint for backoff, especially for 429s."""
        if self.category == ErrorCategory.RETRYABLE_RATE_LIMIT:
            return 1.0  # Could be extracted from Retry-After header
        return None

    def to_dict(self) -> dict:
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "status_code": self.status_code,
            "category": self.category.value,
            "is_retryable": self.is_retryable,
        }


class ToolAuthError(ToolNodeError):
    """Authentication required or failed."""
    category = ErrorCategory.AUTH_REQUIRED


class ToolTimeoutError(ToolNodeError):
    """Tool execution exceeded timeout."""
    category = ErrorCategory.RETRYABLE_SERVER


class ToolConnectionError(ToolNodeError):
    """Failed to connect to tool source."""
    category = ErrorCategory.NETWORK


class ToolRateLimitError(ToolNodeError):
    """Rate limited by external service."""
    category = ErrorCategory.RETRYABLE_RATE_LIMIT


class ToolClientError(ToolNodeError):
    """Client error (4xx) - don't retry."""
    category = ErrorCategory.NON_RETRYABLE_CLIENT


class ToolServerError(ToolNodeError):
    """Server error (5xx) - retry."""
    category = ErrorCategory.RETRYABLE_SERVER
```

### Error Adapters (transport-aware)

```python
# penguiflow/tools/adapters.py

import asyncio
from typing import Any

from .errors import (
    ToolNodeError,
    ToolConnectionError,
    ToolTimeoutError,
    ToolRateLimitError,
    ToolClientError,
    ToolServerError,
    ToolAuthError,
)


def adapt_mcp_error(exc: Exception) -> ToolNodeError:
    """Convert FastMCP exceptions to ToolNodeError."""
    exc_type = type(exc).__name__
    exc_msg = str(exc)

    # FastMCP wraps errors - try to extract status if available
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)

    # Check for known FastMCP exception types
    if "McpError" in exc_type or "ToolError" in exc_type:
        # MCP tool execution failed - usually non-retryable
        return ToolClientError(f"MCP tool error: {exc_msg}", cause=exc)

    if "ConnectionError" in exc_type or "connection" in exc_msg.lower():
        return ToolConnectionError(f"MCP connection failed: {exc_msg}", cause=exc)

    # If we have a status code, use it
    if status_code:
        return _from_status(status_code, exc_msg, exc)

    # Default: wrap as unknown
    return ToolNodeError(f"MCP error: {exc_msg}", cause=exc)


def adapt_utcp_error(exc: Exception) -> ToolNodeError:
    """Convert UTCP exceptions to ToolNodeError."""
    exc_type = type(exc).__name__
    exc_msg = str(exc)

    # UTCP HTTP plugin surfaces status via exception attributes or message
    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)

    # Try to parse status from message if not in attribute
    # UTCP often includes "HTTP 429" or "status: 500" in error messages
    if status_code is None:
        import re

        match = re.search(r"(?:HTTP|status)[:\s]*(\d{3})", exc_msg, re.IGNORECASE)
        if match:
            status_code = int(match.group(1))

    # Check for known UTCP exception types
    if "UtcpHttpError" in exc_type or "HttpError" in exc_type:
        return _from_status(status_code, exc_msg, exc)

    if "UtcpConnectionError" in exc_type or "connection" in exc_msg.lower():
        return ToolConnectionError(f"UTCP connection failed: {exc_msg}", cause=exc)

    if "UtcpTimeoutError" in exc_type or "timeout" in exc_msg.lower():
        return ToolTimeoutError(f"UTCP timeout: {exc_msg}", cause=exc)

    # If we extracted a status code, use it
    if status_code:
        return _from_status(status_code, exc_msg, exc)

    # Default
    return ToolNodeError(f"UTCP error: {exc_msg}", cause=exc)


def adapt_exception(exc: Exception, transport: str) -> ToolNodeError:
    """Route to appropriate adapter based on transport."""
    # Handle standard Python exceptions first
    if isinstance(exc, asyncio.CancelledError):
        raise exc  # Never wrap cancellation

    if isinstance(exc, asyncio.TimeoutError):
        return ToolTimeoutError(f"Operation timed out: {exc}", cause=exc)

    if isinstance(exc, (ConnectionError, OSError)):
        return ToolConnectionError(f"Connection failed: {exc}", cause=exc)

    # Already our error type
    if isinstance(exc, ToolNodeError):
        return exc

    # Route to transport-specific adapter
    if transport == "mcp":
        return adapt_mcp_error(exc)
    else:  # utcp, http, cli
        return adapt_utcp_error(exc)


def _from_status(status_code: int | None, exc_msg: str, exc: Exception) -> ToolNodeError:
    """Map HTTP-like status codes into concrete ToolNodeError subclasses."""
    if status_code is None:
        return ToolNodeError(exc_msg, cause=exc)
    if status_code == 429:
        return ToolRateLimitError(exc_msg, status_code=status_code, cause=exc)
    if 500 <= status_code <= 504:
        return ToolServerError(exc_msg, status_code=status_code, cause=exc)
    if status_code in (401, 403):
        return ToolAuthError(exc_msg, status_code=status_code, cause=exc)
    if 400 <= status_code < 500:
        return ToolClientError(exc_msg, status_code=status_code, cause=exc)
    return ToolNodeError(exc_msg, status_code=status_code, cause=exc)
```
```

---

## Integration with Existing Penguiflow

### StateStore for Distributed Deployments

Penguiflow already provides the `StateStore` protocol (`penguiflow/state.py:47-63`) with optional pause/resume support. The planner checks for `save_planner_state` and `load_planner_state` methods via duck typing.

**Existing Protocol:**
```python
class StateStore(Protocol):
    async def save_event(self, event: StoredEvent) -> None: ...
    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]: ...
    async def save_remote_binding(self, binding: RemoteBinding) -> None: ...
```

**For OAuth/ToolNode, users should implement:**
```python
class ProductionStateStore:
    """Example PostgreSQL implementation."""

    async def save_event(self, event: StoredEvent) -> None:
        # Existing: save to postgres events table
        ...

    async def load_history(self, trace_id: str) -> Sequence[StoredEvent]:
        # Existing: load from postgres
        ...

    async def save_remote_binding(self, binding: RemoteBinding) -> None:
        # Existing: save binding
        ...

    # Optional: Pause/resume support (duck-typed by ReactPlanner)
    async def save_planner_state(self, token: str, payload: dict) -> None:
        """Save pause record for distributed resume."""
        await self.db.execute(
            "INSERT INTO planner_pauses (token, payload) VALUES ($1, $2)",
            token, json.dumps(payload)
        )

    async def load_planner_state(self, token: str) -> dict:
        """Load pause record for resume."""
        row = await self.db.fetchone(
            "SELECT payload FROM planner_pauses WHERE token = $1", token
        )
        return json.loads(row["payload"]) if row else {}
```

**Documentation requirement:** Add to `manual.md` a section on implementing `save_planner_state`/`load_planner_state` for production deployments with OAuth.

### Concurrency/Backpressure

Penguiflow already provides two levels of concurrency control:

1. **Planner-level:** `absolute_max_parallel` (default: 50) and `planning_hints.max_parallel`
   - Enforced before execution via constraint checking
   - Limits how many parallel tool calls the planner can issue

2. **Pattern-level:** `map_concurrent(max_concurrency=8)` in `patterns.py`
   - Semaphore-based limiting within nodes

**ToolNode adds a third level:**

3. **ToolNode-level:** `config.max_concurrency` (default: 10)
   - Limits concurrent calls to a specific external source
   - Protects external APIs from overwhelming
   - Independent of planner concurrency

**Example interaction:**
```
ReactPlanner (absolute_max_parallel=50)
    |
    +- github.create_issue --+
    +- github.list_repos ----+-- ToolNode (max_concurrency=10)
    +- github.get_user ------+       +-- FastMCP Client
    |
    +- stripe.create_charge -+
    +- stripe.list_customers +-- ToolNode (max_concurrency=5)
                             |       +-- UTCP Client
```

The planner might issue 50 parallel calls, but each ToolNode's semaphore ensures the external APIs receive at most `max_concurrency` concurrent requests.

**This is sufficient** - no additional backpressure mechanisms needed. Users can tune:
- `absolute_max_parallel` for overall planner throughput
- `planning_hints.max_parallel` for per-query limits
- `ToolNode.config.max_concurrency` for per-source limits

---

## Popular MCP Servers (Pre-configured)

```python
# penguiflow/tools/presets.py

from .config import ExternalToolConfig, TransportType, AuthType

POPULAR_MCP_SERVERS = {
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
}


def get_preset(name: str) -> ExternalToolConfig:
    """Get a pre-configured MCP server config."""
    if name not in POPULAR_MCP_SERVERS:
        raise ValueError(f"Unknown preset: {name}. Available: {list(POPULAR_MCP_SERVERS.keys())}")
    return POPULAR_MCP_SERVERS[name]
```

---

## Usage Examples

### Basic MCP Integration

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType
from penguiflow.planner import ReactPlanner
from penguiflow.registry import ModelRegistry

# Shared registry (required)
registry = ModelRegistry()

# Configure GitHub MCP server
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        env={"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]},
    ),
    registry=registry,
)

# Connect and discover tools
await github.connect()
print(f"Discovered {len(github.get_tools())} GitHub tools")

# Build planner with GitHub tools
planner = ReactPlanner(
    catalog=[*local_tools, *github.get_tools()],
    llm="gpt-4",
)

result = await planner.run("List open issues in my repo")
```

### User OAuth with HITL

```python
from penguiflow.tools import ToolNode, OAuthManager, OAuthProviderConfig
from penguiflow.planner import ReactPlanner, PlannerPause
from penguiflow.registry import ModelRegistry

registry = ModelRegistry()

# Configure OAuth
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

# ToolNode with OAuth
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
        auth_type=AuthType.OAUTH2_USER,
    ),
    registry=registry,
    auth_manager=oauth_manager,
)

await github.connect()

planner = ReactPlanner(
    catalog=github.get_tools(),
    llm="gpt-4",
)

# Run - may pause for OAuth
result = await planner.run(
    "Create an issue in my private repo",
    tool_context={"user_id": "user_123", "trace_id": "trace_abc"},
)

if isinstance(result, PlannerPause):
    # Frontend shows auth button with result.payload["auth_url"]
    print(f"Auth required: {result.payload}")
    # After OAuth callback:
    # user_id, trace_id = await oauth_manager.handle_callback(code, state)
    # result = await planner.resume(result.resume_token, tool_context={...})
```

### UTCP with Manual URL (Recommended for HTTP APIs)

```python
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType, UtcpMode
from penguiflow.registry import ModelRegistry

registry = ModelRegistry()

# Connect to API that publishes UTCP manual
weather_api = ToolNode(
    config=ExternalToolConfig(
        name="weather",
        transport=TransportType.UTCP,
        connection="https://api.weather.com/.well-known/utcp.json",
        utcp_mode=UtcpMode.MANUAL_URL,  # Recommended
        auth_type=AuthType.API_KEY,
        auth_config={"api_key": "${WEATHER_API_KEY}", "header": "X-API-Key"},
    ),
    registry=registry,
)

await weather_api.connect()
# UTCP client fetches manual and discovers all available tools
print(f"Discovered {len(weather_api.get_tools())} weather tools")
```

### Mixed Native + External Tools

```python
from penguiflow.catalog import tool, build_catalog
from penguiflow.tools import ToolNode, ExternalToolConfig, TransportType
from penguiflow.registry import ModelRegistry
from pydantic import BaseModel

# Shared registry for everything
registry = ModelRegistry()

# Native tool
class SummarizeArgs(BaseModel):
    text: str

class SummarizeResult(BaseModel):
    summary: str

@tool(desc="Summarize text using local LLM", tags=["local"])
async def summarize(args: SummarizeArgs, ctx) -> SummarizeResult:
    return SummarizeResult(summary=args.text[:100] + "...")

# External tools
github = ToolNode(
    config=ExternalToolConfig(
        name="github",
        transport=TransportType.MCP,
        connection="npx -y @modelcontextprotocol/server-github",
    ),
    registry=registry,
)

stripe = ToolNode(
    config=ExternalToolConfig(
        name="stripe",
        transport=TransportType.UTCP,
        connection="https://stripe.com/.well-known/utcp.json",
        auth_type=AuthType.BEARER,
        auth_config={"token": "${STRIPE_SECRET_KEY}"},
    ),
    registry=registry,
)

await github.connect()
await stripe.connect()

# Unified catalog
catalog = [
    *build_catalog([summarize], registry),  # Native
    *github.get_tools(),                     # MCP
    *stripe.get_tools(),                     # UTCP
]

planner = ReactPlanner(catalog=catalog, llm="gpt-4")
```

---

## File Structure

```
penguiflow/
+-- tools/
    +-- __init__.py          # Exports: ToolNode, ExternalToolConfig, etc.
    +-- config.py            # Configuration models (~100 lines)
    +-- node.py              # ToolNode implementation (~350 lines)
    +-- auth.py              # OAuthManager + TokenStore (~150 lines)
    +-- errors.py            # Error types + categories (~80 lines)
    +-- adapters.py          # Transport-aware error adapters (~80 lines)
    +-- presets.py           # Popular MCP server configs (~50 lines)
```

**Total new code: ~760 lines**

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1)
- [ ] Add planner-group deps: `fastmcp`, `utcp`, `utcp-http`, `tenacity`, `aiohttp`
- [ ] Create `penguiflow/tools/` package
- [ ] Implement `ExternalToolConfig` with `UtcpMode`
- [ ] Implement `ToolNode` with MCP support (FastMCP)
- [ ] Implement error classification + adapters + retry wiring for MCP
- [ ] Basic tests with mock MCP server

### Phase 2: Multi-Protocol + Auth (Week 2)
- [ ] Add UTCP support (manual_url and base_url modes)
- [ ] Implement `OAuthManager` and `TokenStore`
- [ ] Wire OAuth to HITL pause/resume
- [ ] Add FastAPI callback handler example
- [ ] Tests for OAuth flow and retry/cancellation

### Phase 3: Documentation + Polish (Week 3)
- [ ] Add `presets.py` with popular MCP servers
- [ ] Document StateStore implementation for production
- [ ] Document concurrency configuration
- [ ] Document env-var fail-fast behavior and namespacing/collision guarantees
- [ ] Example: React frontend with OAuth
- [ ] Example: Multi-tool agent

### Phase 4: CLI Integration (Optional, Week 4)
- [ ] Add `penguiflow tools list` command
- [ ] Add `penguiflow tools connect <preset>` command
- [ ] Integration with `penguiflow new` templates

---

## Success Metrics

| Metric | Target |
|--------|--------|
| MCP connection time | < 2s |
| Tool call overhead (vs direct) | < 50ms |
| OAuth flow completion | < 60s end-to-end |
| Test coverage | > 85% |
| New code lines | < 700 |

---

## Not In Scope (Deferred)

| Feature | Reason | Future Release |
|---------|--------|----------------|
| MCP Resources/Prompts | Focus on tools first | v2.7.0 |
| MCP Sampling | Rare use case | v2.8.0 |
| Expose Penguiflow as MCP server | A2A feature | v2.8.0 |
| Built-in Postgres StateStore | User should implement | v2.8.0 |

---

## References

- [FastMCP](https://github.com/jlowin/fastmcp) - v2.13.1
- [python-utcp](https://github.com/universal-tool-calling-protocol/python-utcp) - v1.1.2
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - v1.23.1
- [UTCP Documentation](https://www.utcp.io/)
- [MCP Specification](https://modelcontextprotocol.io/)
