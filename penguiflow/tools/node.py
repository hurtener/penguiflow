"""ToolNode: External tool integration via MCP and UTCP protocols.

ToolNode provides a unified interface for connecting to external tool sources
(MCP servers, UTCP endpoints) and exposing their capabilities as typed NodeSpecs
compatible with PenguiFlow's planner and registry system.

Key capabilities:
- **Multi-protocol support**: MCP (stdio/SSE/streamable-http) and UTCP transports
- **Artifact extraction**: Multi-layer pipeline for handling binary content
  (L4: field extraction, L1: resource links, L2: MCP content blocks,
   L3: heuristic binary detection, L0: size safety net)
- **MCP Resources**: Discovery, caching, subscriptions, and generated tools
- **OAuth integration**: HITL flows for user-scoped authentication
- **Automatic retries**: Configurable retry policies with backoff
- **Tool namespacing**: Collision-free multi-source tool registration

See RFC: docs/RFC_MCP_BINARY_CONTENT_HANDLING.md for artifact handling details.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import inspect
import json
import logging
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, create_model

from penguiflow.catalog import NodeSpec
from penguiflow.node import Node
from penguiflow.planner.context import ToolContext
from penguiflow.registry import ModelRegistry

from .adapters import adapt_exception
from .config import (
    AuthType,
    ExternalToolConfig,
    McpTransportMode,
    TransportType,
    UtcpMode,
)
from .errors import ToolAuthError, ToolConnectionError, ToolNodeError
from .resources import (
    ResourceCache,
    ResourceCacheConfig,
    ResourceInfo,
    ResourceSubscriptionManager,
    ResourceTemplateInfo,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class ToolNode:
    """Unified external tool integration for Penguiflow (MCP only for Phase 1)."""

    config: ExternalToolConfig
    registry: ModelRegistry
    auth_manager: Any | None = None

    _mcp_client: Any | None = field(default=None, repr=False)
    _utcp_client: Any | None = field(default=None, repr=False)
    _tools: list[NodeSpec] = field(default_factory=list, repr=False)
    _tool_name_map: dict[str, str] = field(default_factory=dict, repr=False)  # namespaced -> original
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)
    _connected: bool = field(default=False, repr=False)
    _connect_lock: asyncio.Lock = field(init=False, repr=False)
    _connected_loop: Any | None = field(default=None, repr=False)  # Track event loop for reconnection

    # MCP Resources support (Phase 2)
    _resource_cache: ResourceCache | None = field(default=None, repr=False)
    _subscription_manager: ResourceSubscriptionManager | None = field(default=None, repr=False)
    _resources: list[ResourceInfo] = field(default_factory=list, repr=False)
    _resource_templates: list[ResourceTemplateInfo] = field(default_factory=list, repr=False)
    _resources_supported: bool = field(default=False, repr=False)
    _resource_update_callback: Callable[[str], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)
        self._connect_lock = asyncio.Lock()
        self._subscription_manager = ResourceSubscriptionManager(self.config.name)

    async def connect(self, ctx: ToolContext | None = None) -> None:
        """Connect to tool source and discover available tools.

        Args:
            ctx: Optional ToolContext for HITL OAuth during connection.
                 Required if auth_type is OAUTH2_USER.
        """
        current_loop = asyncio.get_running_loop()

        # Check if we need to reconnect due to event loop change
        if self._connected and self._connected_loop and self._connected_loop is not current_loop:
            # Connection was made on a different event loop - must reconnect
            await self._force_reconnect()
            return

        if self._connected:
            return

        async with self._connect_lock:
            if self._connected and self._connected_loop is current_loop:
                return

            # Reset discovery caches before connecting
            self._tools = []
            self._tool_name_map.clear()

            # Resolve auth headers for connection (supports HITL OAuth if ctx provided)
            auth_headers = await self._resolve_connection_auth(ctx)

            if self.config.transport == TransportType.MCP:
                await self._connect_mcp(auth_headers)
            elif self.config.transport in {TransportType.HTTP, TransportType.UTCP, TransportType.CLI}:
                await self._connect_utcp()
            else:
                raise ToolConnectionError(
                    f"Transport '{self.config.transport.value}' not supported",
                )

            self._connected = True
            self._connected_loop = current_loop

    async def _resolve_connection_auth(self, ctx: ToolContext | None) -> dict[str, str]:
        """Resolve auth headers for connection phase.

        For static auth (BEARER, API_KEY, COOKIE), returns headers directly.
        For OAUTH2_USER, requires ctx and triggers HITL OAuth flow.
        """
        if self.config.auth_type == AuthType.NONE:
            return {}

        # Static auth types don't need ctx
        if self.config.auth_type in {AuthType.BEARER, AuthType.API_KEY, AuthType.COOKIE}:
            return self._get_static_auth_headers()

        # OAUTH2_USER requires ctx for HITL flow
        if self.config.auth_type == AuthType.OAUTH2_USER:
            if ctx is None:
                raise ToolAuthError(
                    f"ToolNode '{self.config.name}' requires HITL OAuth but no ToolContext provided. "
                    "Call connect(ctx) with a ToolContext containing user_id."
                )
            return await self._resolve_user_oauth(ctx)

        return {}

    async def _connect_mcp(self, auth_headers: dict[str, str] | None = None) -> None:
        """Connect via FastMCP client.

        Args:
            auth_headers: Optional auth headers resolved during connect().
        """
        try:
            from fastmcp import Client as MCPClient
            from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
            raise ToolConnectionError(
                "fastmcp is required for MCP ToolNode. Install penguiflow[planner].",
            ) from exc

        try:
            # Determine transport: shell command or URL
            connection = self.config.connection
            transport: Any
            if connection.startswith(("http://", "https://", "ws://", "wss://")):
                # URL-based transport - determine which to use
                transport = self._resolve_mcp_url_transport(
                    connection,
                    auth_headers,
                    SSETransport,
                    StreamableHttpTransport,
                )
            else:
                # Shell command - split into command + args and create StdioTransport
                import shlex

                parts = shlex.split(connection)
                command = parts[0]
                args = parts[1:] if len(parts) > 1 else []
                # Pass env vars from config, substituting ${VAR} placeholders
                env: dict[str, str] | None = None
                if self.config.env:
                    env = {}
                    for key, val in self.config.env.items():
                        env[key] = self._substitute_env(str(val)) if isinstance(val, str) else str(val)
                transport = StdioTransport(command=command, args=args, env=env)

            self._mcp_client = MCPClient(transport)
            await self._mcp_client.__aenter__()
            mcp_tools = await self._mcp_client.list_tools()
        except Exception as exc:
            if self._mcp_client:
                try:
                    await self._mcp_client.__aexit__(type(exc), exc, exc.__traceback__)
                except Exception:
                    pass
                self._mcp_client = None
            raise ToolConnectionError(
                f"Failed to connect to MCP tool source '{self.config.name}': {exc}",
            ) from exc

        self._tools = self._convert_mcp_tools(mcp_tools)

        # Discover MCP resources (Phase 2)
        await self._discover_mcp_resources()

    async def _discover_mcp_resources(self) -> None:
        """Discover MCP resources and templates.

        Best-effort: doesn't fail if server doesn't support resources.
        """
        if self._mcp_client is None:
            return

        # Try to list resources
        try:
            resources = await self._mcp_client.list_resources()
            self._resources = [
                ResourceInfo(
                    uri=str(getattr(r, "uri", r.get("uri", "")) if isinstance(r, dict) else r.uri),
                    name=getattr(r, "name", r.get("name") if isinstance(r, dict) else None),
                    description=getattr(r, "description", r.get("description") if isinstance(r, dict) else None),
                    mime_type=getattr(r, "mimeType", r.get("mimeType") if isinstance(r, dict) else None),
                )
                for r in (resources if isinstance(resources, list) else [])
            ]
            self._resources_supported = True
            logger.debug(
                "Discovered %d MCP resources for '%s'",
                len(self._resources),
                self.config.name,
            )
        except Exception as e:
            # Server doesn't support resources - that's fine
            logger.debug(
                "MCP resources not supported by '%s': %s",
                self.config.name,
                e,
            )
            self._resources = []
            self._resources_supported = False

        # Try to list resource templates
        try:
            templates = await self._mcp_client.list_resource_templates()
            self._resource_templates = []
            for t in templates if isinstance(templates, list) else []:
                uri_tmpl = t.get("uriTemplate", "") if isinstance(t, dict) else getattr(t, "uriTemplate", "")
                desc = t.get("description") if isinstance(t, dict) else getattr(t, "description", None)
                mime = t.get("mimeType") if isinstance(t, dict) else getattr(t, "mimeType", None)
                name = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
                self._resource_templates.append(
                    ResourceTemplateInfo(
                        uri_template=str(uri_tmpl),
                        name=name,
                        description=desc,
                        mime_type=mime,
                    )
                )
            logger.debug(
                "Discovered %d MCP resource templates for '%s'",
                len(self._resource_templates),
                self.config.name,
            )
        except Exception as e:
            logger.debug(
                "MCP resource templates not supported by '%s': %s",
                self.config.name,
                e,
            )
            self._resource_templates = []

        # Generate planner tools for resources if supported
        if self._resources_supported:
            resource_tools = self._generate_resource_tools()
            self._tools.extend(resource_tools)

    def _resolve_mcp_url_transport(
        self,
        connection: str,
        auth_headers: dict[str, str] | None,
        sse_transport_cls: type[Any],
        streamable_http_transport_cls: type[Any],
    ) -> Any:
        """Resolve the appropriate MCP transport for URL-based connections.

        Transport selection logic:
        1. If mcp_transport_mode is explicitly set (SSE or STREAMABLE_HTTP), use that
        2. For AUTO mode without auth headers, let FastMCP auto-detect (pass URL string)
        3. For AUTO mode with auth headers, detect from URL pattern:
           - /sse → SSE transport
           - /mcp or other → StreamableHTTP (modern default)

        Args:
            connection: The URL to connect to
            auth_headers: Auth headers if any
            sse_transport_cls: SSETransport class from fastmcp
            streamable_http_transport_cls: StreamableHttpTransport class from fastmcp

        Returns:
            Transport object or URL string for FastMCP client
        """
        mode = self.config.mcp_transport_mode

        # Explicit SSE mode
        if mode == McpTransportMode.SSE:
            return sse_transport_cls(url=connection, headers=auth_headers or {})

        # Explicit StreamableHTTP mode
        if mode == McpTransportMode.STREAMABLE_HTTP:
            return streamable_http_transport_cls(url=connection, headers=auth_headers or {})

        # AUTO mode
        if not auth_headers:
            # No auth headers - let FastMCP auto-detect from URL
            return connection

        # AUTO mode with auth headers - need to construct transport explicitly
        # Detect from URL pattern
        if "/sse" in connection.lower():
            return sse_transport_cls(url=connection, headers=auth_headers)

        # Default to StreamableHTTP (modern MCP standard)
        return streamable_http_transport_cls(url=connection, headers=auth_headers)

    async def _connect_utcp(self) -> None:
        """Connect via UTCP client (manual_url or base_url)."""
        try:
            from utcp import UtcpClient
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
            raise ToolConnectionError(
                "utcp is required for UTCP ToolNode. Install penguiflow[planner].",
            ) from exc

        config = self._build_utcp_config()
        try:
            self._utcp_client = await UtcpClient.create(config=config)
            utcp_tools = await self._utcp_client.list_tools()
        except Exception as exc:
            if self._utcp_client:
                try:
                    await self._utcp_client.aclose()
                except Exception:
                    pass
                self._utcp_client = None
            raise ToolConnectionError(
                f"Failed to connect to UTCP tool source '{self.config.name}': {exc}",
            ) from exc

        self._tools = self._convert_utcp_tools(utcp_tools)

    def get_tools(self) -> list[NodeSpec]:
        """Return discovered tools as Penguiflow NodeSpec entries.

        Note: connect() must be called before this method.
        """
        return self._tools

    def get_tool_specs(self) -> list[NodeSpec]:
        """Alias for get_tools for compatibility with generators."""
        return self.get_tools()

    async def call(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> Any:
        """Execute a tool with auth resolution and resilience."""
        current_loop = asyncio.get_running_loop()
        if not self._connected or (self._connected_loop and self._connected_loop is not current_loop):
            await self._force_reconnect()

        async with self._semaphore:
            auth_headers = await self._resolve_auth(ctx)

            original_name = self._tool_name_map.get(tool_name)
            if original_name is None:
                original_name = tool_name.removeprefix(f"{self.config.name}.")

            result = await self._call_with_retry(original_name, args, auth_headers)
            # Transform output through layered artifact extraction
            transformed = await self._transform_output(original_name, result, ctx)
            # Wrap result to match the output model schema: {"result": <data>}
            return {"result": transformed}

    async def close(self) -> None:
        """Clean up resources."""
        self._connected = False
        self._connected_loop = None
        self._tools = []
        self._tool_name_map.clear()
        if self._mcp_client:
            try:
                await self._mcp_client.__aexit__(None, None, None)
            except Exception:  # pragma: no cover - may fail if wrong event loop
                pass
            self._mcp_client = None
        if self._utcp_client:
            try:
                await self._utcp_client.aclose()
            except Exception:  # pragma: no cover - best effort
                pass
            self._utcp_client = None

    async def _force_reconnect(self) -> None:
        """Force reconnection when event loop has changed.

        This handles the case where the initial connection was made on a different
        event loop (e.g., during build_planner before uvicorn starts) and we need
        to reconnect on the current request-handling event loop.
        """
        # Clean up old connection (best effort - may fail on wrong loop)
        self._connected = False
        self._connected_loop = None
        if self._mcp_client:
            # Don't await __aexit__ as it's bound to old loop
            self._mcp_client = None
        if self._utcp_client:
            self._utcp_client = None

        # Now connect fresh on current loop
        await self.connect()

    # ─── Auth Resolution ────────────────────────────────────────────────────────

    def _get_static_auth_headers(self) -> dict[str, str]:
        """Get static auth headers (BEARER/API_KEY/COOKIE) for connection-time auth.

        Returns empty dict for NONE or OAUTH2_USER (which requires ToolContext).
        """
        if self.config.auth_type == AuthType.API_KEY:
            key = self._substitute_env(str(self.config.auth_config.get("api_key", "")))
            header = self.config.auth_config.get("header", "X-API-Key")
            return {str(header): key}

        if self.config.auth_type == AuthType.BEARER:
            token = self._substitute_env(str(self.config.auth_config.get("token", "")))
            return {"Authorization": f"Bearer {token}"}

        if self.config.auth_type == AuthType.COOKIE:
            cookie_name = self._substitute_env(str(self.config.auth_config.get("cookie_name", "")))
            cookie_value = self._substitute_env(str(self.config.auth_config.get("cookie_value", "")))
            return {"Cookie": f"{cookie_name}={cookie_value}"}

        return {}

    async def _resolve_auth(self, ctx: ToolContext) -> dict[str, str]:
        """Resolve authentication headers, pausing for OAuth if needed."""
        if self.config.auth_type == AuthType.NONE:
            return {}

        if self.config.auth_type == AuthType.API_KEY:
            key = self._substitute_env(str(self.config.auth_config.get("api_key", "")))
            header = self.config.auth_config.get("header", "X-API-Key")
            return {str(header): key}

        if self.config.auth_type == AuthType.BEARER:
            token = self._substitute_env(str(self.config.auth_config.get("token", "")))
            return {"Authorization": f"Bearer {token}"}

        if self.config.auth_type == AuthType.COOKIE:
            cookie_name = self._substitute_env(str(self.config.auth_config.get("cookie_name", "")))
            cookie_value = self._substitute_env(str(self.config.auth_config.get("cookie_value", "")))
            return {"Cookie": f"{cookie_name}={cookie_value}"}

        if self.config.auth_type == AuthType.OAUTH2_USER:
            return await self._resolve_user_oauth(ctx)

        return {}

    async def _resolve_user_oauth(self, ctx: ToolContext) -> dict[str, str]:
        """Handle user-level OAuth with HITL pause/resume."""
        if not self.auth_manager:
            raise ToolAuthError(
                f"ToolNode '{self.config.name}' requires user OAuth but no auth_manager was provided",
            )

        user_id = ctx.tool_context.get("user_id")
        if not user_id:
            raise ToolAuthError("user_id required in tool_context for OAuth")

        token = await self.auth_manager.get_token(user_id, self.config.name)
        if token:
            return {"Authorization": f"Bearer {token}"}

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
            },
        )

        token = await self.auth_manager.get_token(user_id, self.config.name)
        if not token:
            raise ToolAuthError(f"OAuth for {self.config.name} was not completed")

        return {"Authorization": f"Bearer {token}"}

    # ─── Resilience ─────────────────────────────────────────────────────────────

    async def _call_with_retry(
        self,
        tool_name: str,
        args: dict[str, Any],
        auth_headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute tool call with intelligent retry based on error category."""
        policy = self.config.retry_policy
        transport = "mcp" if self._mcp_client else "utcp"

        retry, retry_if_exception, stop_after_attempt, wait_exponential = self._load_tenacity()

        def should_retry(exc: BaseException) -> bool:
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
        async def _execute() -> Any:
            try:
                async with asyncio.timeout(self.config.timeout_s):
                    if self._mcp_client:
                        result = await self._mcp_client.call_tool(tool_name, args)
                        return self._serialize_mcp_result(result)
                    if self._utcp_client:
                        return await self._call_utcp_tool(tool_name, args, auth_headers or {})
                    raise ToolNodeError("No client available for tool execution")
            except asyncio.CancelledError:
                raise
            except ToolNodeError:
                raise
            except Exception as exc:  # pragma: no cover - wrapped in adapter
                raise adapt_exception(exc, transport) from exc

        return await _execute()

    def _serialize_mcp_result(self, result: Any) -> Any:
        """Convert MCP CallToolResult to JSON-serializable format."""

        # If it's already a dict or primitive (except string), return as-is
        if isinstance(result, (dict, int, float, bool, type(None), list)):
            return result

        # Handle CallToolResult from fastmcp/mcp
        if hasattr(result, "structuredContent") and result.structuredContent is not None:
            return result.structuredContent

        if hasattr(result, "content"):
            # Extract text from content blocks
            texts = []
            for item in result.content:
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif hasattr(item, "model_dump"):
                    texts.append(item.model_dump())
                else:
                    texts.append(str(item))
            # If single text result, try to parse as JSON
            if len(texts) == 1:
                text = texts[0]
                # Try to parse as JSON if it looks like JSON
                if isinstance(text, str) and text.strip().startswith(("{", "[")):
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
                return text
            return texts

        # If it's a string that looks like JSON, parse it
        if isinstance(result, str) and result.strip().startswith(("{", "[")):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result

        # If it's a plain string, return as-is
        if isinstance(result, str):
            return result

        # Fallback: try model_dump for pydantic models
        if hasattr(result, "model_dump"):
            return result.model_dump()

        # Last resort: convert to string
        return str(result)

    async def _call_utcp_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> Any:
        """Invoke UTCP tool, attempting to pass headers when supported."""
        if not self._utcp_client:
            raise ToolNodeError("UTCP client not initialised")
        try:
            if auth_headers:
                return await self._utcp_client.call_tool(tool_name, args, headers=auth_headers)
            return await self._utcp_client.call_tool(tool_name, args)
        except TypeError:
            # Older UTCP clients may not accept headers kwarg; fallback to args-only.
            return await self._utcp_client.call_tool(tool_name, args)

    # ─── Tool Conversion ────────────────────────────────────────────────────────

    def _convert_mcp_tools(self, mcp_tools: list[Any]) -> list[NodeSpec]:
        """Convert MCP tool schemas to Penguiflow NodeSpec."""
        specs: list[NodeSpec] = []
        for tool in mcp_tools:
            if not self._matches_filter(tool.name):
                continue

            namespaced = f"{self.config.name}.{tool.name}"
            if namespaced in self._tool_name_map:
                raise ToolNodeError(f"Duplicate tool name '{namespaced}' in ToolNode '{self.config.name}'")
            self._tool_name_map[namespaced] = tool.name

            args_model = self._create_args_model(namespaced, getattr(tool, "inputSchema", {}) or {})
            out_model = self._create_result_model(namespaced)

            # Only register if not already in registry (handles reconnection case)
            if not self.registry.has(namespaced):
                try:
                    self.registry.register(namespaced, args_model, out_model)
                except ValueError as exc:
                    raise ToolNodeError(
                        f"Tool name collision for '{namespaced}' (native tool or another ToolNode)",
                    ) from exc

            # Use functools.partial to capture `namespaced` by value, not reference.
            # Without this, all closures would reference the last loop iteration's value.
            async def _make_call(bound_name: str, args: BaseModel, ctx: ToolContext) -> Any:
                return await self.call(bound_name, args.model_dump(), ctx)

            bound_fn = functools.partial(_make_call, namespaced)

            extra: dict[str, Any] = {
                "source": "mcp",
                "namespace": self.config.name,
                "tool_node": self,
            }
            if isinstance(self.config.arg_validation, dict):
                extra["arg_validation"] = dict(self.config.arg_validation)

            specs.append(
                NodeSpec(
                    node=Node(bound_fn, name=namespaced),
                    name=namespaced,
                    desc=getattr(tool, "description", "") or "",
                    args_model=args_model,
                    out_model=out_model,
                    side_effects="external",
                    tags=("mcp", self.config.name),
                    extra=extra,
                ),
            )

        return specs

    def _convert_utcp_tools(self, utcp_tools: list[Any]) -> list[NodeSpec]:
        """Convert UTCP tool schemas to Penguiflow NodeSpec."""
        specs: list[NodeSpec] = []
        for tool in utcp_tools:
            parts = tool.name.split(".")
            original_tool_name = parts[-1] if len(parts) > 1 else tool.name

            if not self._matches_filter(original_tool_name):
                continue

            namespaced = f"{self.config.name}.{original_tool_name}"
            if namespaced in self._tool_name_map:
                raise ToolNodeError(f"Duplicate tool name '{namespaced}' in ToolNode '{self.config.name}'")
            self._tool_name_map[namespaced] = tool.name

            args_model = self._create_args_model(namespaced, getattr(tool, "inputs", {}) or {})
            out_model = self._create_result_model(namespaced)

            # Only register if not already in registry (handles reconnection case)
            if not self.registry.has(namespaced):
                try:
                    self.registry.register(namespaced, args_model, out_model)
                except ValueError as exc:
                    raise ToolNodeError(
                        f"Tool name collision for '{namespaced}' (native tool or another ToolNode)",
                    ) from exc

            # Use functools.partial to capture `namespaced` by value, not reference.
            # Without this, all closures would reference the last loop iteration's value.
            async def _make_call(bound_name: str, args: BaseModel, ctx: ToolContext) -> Any:
                return await self.call(bound_name, args.model_dump(), ctx)

            bound_fn = functools.partial(_make_call, namespaced)

            extra: dict[str, Any] = {
                "source": "utcp",
                "namespace": self.config.name,
                "tool_node": self,
            }
            if isinstance(self.config.arg_validation, dict):
                extra["arg_validation"] = dict(self.config.arg_validation)

            specs.append(
                NodeSpec(
                    node=Node(bound_fn, name=namespaced),
                    name=namespaced,
                    desc=getattr(tool, "description", "") or "",
                    args_model=args_model,
                    out_model=out_model,
                    side_effects="external",
                    tags=("utcp", self.config.name),
                    extra=extra,
                ),
            )
        return specs

    # ─── Model Creation ─────────────────────────────────────────────────────────

    def _create_args_model(self, name: str, schema: dict[str, Any]) -> type[BaseModel]:
        """Create Pydantic model from JSON schema for tool arguments."""
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        if not props:
            return create_model(f"{name.replace('.', '_')}Args", data=(dict[str, Any] | None, None))

        fields: dict[str, tuple[Any, Any]] = {}
        for prop_name, prop_schema in props.items():
            python_type = self._json_type_to_python(prop_schema)
            if prop_name in required:
                fields[prop_name] = (python_type, ...)
            else:
                fields[prop_name] = (python_type | None, None)

        return create_model(f"{name.replace('.', '_')}Args", **fields)  # type: ignore[call-overload]

    def _create_result_model(self, name: str) -> type[BaseModel]:
        """Create Pydantic model for tool results (permissive)."""
        return create_model(f"{name.replace('.', '_')}Result", result=(Any, None))

    def _json_type_to_python(self, prop_schema: dict[str, Any]) -> type[Any]:
        """Map JSON schema type to Python type."""
        json_type = prop_schema.get("type", "string")

        simple_mapping: dict[str, type[Any]] = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
        }

        if json_type in simple_mapping:
            return simple_mapping[json_type]

        if json_type == "array":
            items = prop_schema.get("items", {})
            items_type = items.get("type")
            if items_type in simple_mapping:
                inner_type = simple_mapping[items_type]
                return cast(type[Any], list[inner_type])  # type: ignore[valid-type]
            return list

        return dict[str, Any]

    # ─── UTCP Config ────────────────────────────────────────────────────────────

    def _build_utcp_config(self) -> dict[str, Any]:
        """Build UTCP client configuration based on utcp_mode."""
        mode = self.config.utcp_mode

        if mode == UtcpMode.AUTO:
            if self.config.connection.endswith((".json", "/utcp", "/.well-known/utcp")):
                mode = UtcpMode.MANUAL_URL
            else:
                mode = UtcpMode.BASE_URL

        if mode == UtcpMode.MANUAL_URL:
            return {
                "manuals": [self.config.connection],
                "variables": self._build_utcp_variables(),
            }

        call_template_type = "cli" if self.config.transport == TransportType.CLI else "http"
        return {
            "manual_call_templates": [
                {
                    "name": self.config.name,
                    "call_template_type": call_template_type,
                    "url": self.config.connection,
                    "http_method": "POST",
                },
            ],
            "variables": self._build_utcp_variables(),
        }

    def _build_utcp_variables(self) -> dict[str, str]:
        """Build UTCP variable substitutions from env and auth_config."""
        variables: dict[str, str] = {}

        for key, value in self.config.env.items():
            variables[key] = self._substitute_env(value)

        for key, value in self.config.auth_config.items():
            variables[key] = self._substitute_env(str(value))

        return variables

    # ─── Helpers ────────────────────────────────────────────────────────────────

    def _matches_filter(self, tool_name: str) -> bool:
        if not self.config.tool_filter:
            return True
        return any(re.match(pattern, tool_name) for pattern in self.config.tool_filter)

    def _substitute_env(self, value: str) -> str:
        """Substitute ${VAR} patterns with environment variables, failing fast on missing values."""
        import os

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
                    f"Missing required environment variable '{var}' for ToolNode '{self.config.name}'",
                )
            return val

        return re.sub(pattern, _replace, value)

    def _load_tenacity(self):
        """Lazily import tenacity to keep dependency optional."""
        try:
            from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "tenacity is required for ToolNode retries. Install penguiflow[planner].",
            ) from exc
        return retry, retry_if_exception, stop_after_attempt, wait_exponential

    # ─── Output Transformation (Phase 1) ───────────────────────────────────────

    async def _transform_output(
        self,
        tool_name: str,
        result: Any,
        ctx: ToolContext,
    ) -> Any:
        """Transform tool output through layered artifact extraction.

        Layer priority (highest to lowest):
        - L5: Custom transformer (escape hatch for complete control)
        - L4: Per-tool field configuration (explicit field extraction)
        - L1: Resource links (MCP resource_link content blocks)
        - L2: MCP typed content (embedded content blocks with mime_type)
        - L3: Heuristic binary detection (base64 signature matching)
        - L0: Size safety net (clamp oversized string content)

        Args:
            tool_name: The original (unnamespaced) tool name
            result: Raw tool result from MCP/UTCP
            ctx: Tool context with artifact store access

        Returns:
            Transformed result with binary/large content replaced by ArtifactRefs
        """
        config = self.config.artifact_extraction

        # L5: Custom transformer (complete override)
        if self.config.output_transformer is not None:
            namespaced = f"{self.config.name}.{tool_name}"
            transformed = self.config.output_transformer(namespaced, result, ctx)
            if inspect.isawaitable(transformed):
                return await transformed
            return transformed

        # L4: Per-tool field configuration
        namespaced = f"{self.config.name}.{tool_name}"
        if namespaced in config.tool_fields:
            result = await self._apply_field_extraction(result, config.tool_fields[namespaced], ctx)

        # L1: Resource links (MCP resource_link content blocks)
        result = await self._handle_resource_links(result, ctx)

        # L2: MCP typed content blocks
        result = await self._transform_mcp_content_blocks(result, ctx)

        # L3: Heuristic binary detection
        if config.binary_detection.enabled:
            result = await self._detect_and_extract_binary(result, ctx)

        # L0: Size safety net
        result = await self._apply_size_limits(result, ctx)

        return result

    async def _apply_field_extraction(
        self,
        result: Any,
        field_configs: list[Any],  # list[ArtifactFieldConfig]
        ctx: ToolContext,
    ) -> Any:
        """Extract specific fields as artifacts based on per-tool configuration.

        Args:
            result: The tool result to process
            field_configs: List of ArtifactFieldConfig for this tool
            ctx: Tool context with artifact store

        Returns:
            Result with configured fields replaced by ArtifactRefs
        """
        if not isinstance(result, dict):
            return result

        for field_config in field_configs:
            path = field_config.field_path
            # Simple dot notation path traversal
            parts = path.split(".")
            current = result
            parent = None
            last_key = None

            for part in parts:
                if not isinstance(current, dict) or part not in current:
                    break
                parent = current
                last_key = part
                current = current[part]
            else:
                # Found the field - extract as artifact
                if parent is not None and last_key is not None:
                    value = parent[last_key]
                    if isinstance(value, str) and len(value) > 100:
                        # Decode and store
                        try:
                            if field_config.content_type in ("pdf", "image", "binary"):
                                data = base64.b64decode(value)
                            else:
                                data = value.encode("utf-8")

                            mime = field_config.mime_type
                            if mime is None:
                                mime = self._infer_mime_type(field_config.content_type)

                            ref = await ctx.artifacts.put_bytes(
                                data,
                                mime_type=mime,
                                namespace=self.config.name,
                            )

                            # Replace with summary and reference
                            summary = field_config.summary_template.format(
                                content_type=field_config.content_type,
                                size=len(data),
                                artifact_id=ref.id,
                            )
                            parent[last_key] = {
                                "artifact": ref.model_dump(),
                                "summary": summary,
                            }
                        except Exception as e:
                            logger.debug(f"Field extraction failed for {path}: {e}")

        return result

    async def _handle_resource_links(self, result: Any, ctx: ToolContext) -> Any:
        """Handle MCP resource_link content blocks.

        Converts resource_link references to artifact references with optional
        auto-read for small resources.

        Args:
            result: Tool result that may contain resource_link content
            ctx: Tool context

        Returns:
            Result with resource_links converted to artifact references
        """
        config = self.config.artifact_extraction.resources
        if not config.enabled:
            return result

        # Handle MCP CallToolResult with content blocks
        if hasattr(result, "content") and isinstance(result.content, list):
            for i, item in enumerate(result.content):
                if hasattr(item, "type") and item.type == "resource_link":
                    # Convert resource_link to artifact reference stub
                    resource_uri = getattr(item, "uri", None) or getattr(item, "resource", {}).get("uri")
                    if resource_uri:
                        result.content[i] = {
                            "type": "artifact_stub",
                            "resource_uri": str(resource_uri),
                            "summary": f"Resource link: {resource_uri}",
                            "note": "Use MCP resources/read to fetch content",
                        }
        elif isinstance(result, dict):
            result = await self._walk_and_transform_resource_links(result, ctx)
        elif isinstance(result, list):
            result = [await self._handle_resource_links(item, ctx) for item in result]

        return result

    async def _walk_and_transform_resource_links(
        self,
        data: dict[str, Any],
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Recursively walk dict and transform resource_link entries."""
        transformed: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                if value.get("type") == "resource_link":
                    # Transform resource_link to stub
                    uri = value.get("uri") or value.get("resource", {}).get("uri")
                    transformed[key] = {
                        "type": "artifact_stub",
                        "resource_uri": str(uri) if uri else None,
                        "summary": f"Resource link: {uri}",
                    }
                else:
                    transformed[key] = await self._walk_and_transform_resource_links(value, ctx)
            elif isinstance(value, list):
                transformed[key] = [
                    await self._walk_and_transform_resource_links(item, ctx) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                transformed[key] = value
        return transformed

    async def _transform_mcp_content_blocks(self, result: Any, ctx: ToolContext) -> Any:
        """Transform MCP typed content blocks (embedded content with mime_type).

        Handles:
        - EmbeddedResource with blob data
        - Content blocks with explicit mime_type

        Args:
            result: Tool result
            ctx: Tool context

        Returns:
            Result with embedded binary content extracted as artifacts
        """
        if hasattr(result, "content") and isinstance(result.content, list):
            for i, item in enumerate(result.content):
                # Handle EmbeddedResource with blob
                if hasattr(item, "type") and item.type == "resource":
                    resource = getattr(item, "resource", None)
                    if resource and hasattr(resource, "blob"):
                        try:
                            data = base64.b64decode(resource.blob)
                            mime = getattr(resource, "mimeType", None) or "application/octet-stream"
                            ref = await ctx.artifacts.put_bytes(
                                data,
                                mime_type=mime,
                                namespace=self.config.name,
                            )
                            config = self.config.artifact_extraction
                            summary = config.default_binary_summary.format(
                                mime_type=mime,
                                size=len(data),
                                artifact_id=ref.id,
                            )
                            result.content[i] = {
                                "type": "artifact",
                                "artifact": ref.model_dump(),
                                "summary": summary,
                            }
                        except Exception as e:
                            logger.debug(f"MCP content block extraction failed: {e}")

        elif isinstance(result, dict):
            # Check for blob fields in dict structure
            for key, value in list(result.items()):
                if isinstance(value, dict) and "blob" in value:
                    try:
                        data = base64.b64decode(value["blob"])
                        mime = value.get("mimeType", "application/octet-stream")
                        ref = await ctx.artifacts.put_bytes(
                            data,
                            mime_type=mime,
                            namespace=self.config.name,
                        )
                        config = self.config.artifact_extraction
                        result[key] = {
                            "artifact": ref.model_dump(),
                            "summary": config.default_binary_summary.format(
                                mime_type=mime,
                                size=len(data),
                                artifact_id=ref.id,
                            ),
                        }
                    except Exception as e:
                        logger.debug(f"Blob extraction failed for {key}: {e}")

        return result

    async def _detect_and_extract_binary(self, result: Any, ctx: ToolContext) -> Any:
        """Detect binary content heuristically and extract as artifacts.

        Uses base64 signature matching to identify binary content.

        Args:
            result: Tool result to scan
            ctx: Tool context

        Returns:
            Result with detected binary content replaced by artifact references
        """
        if isinstance(result, str):
            return await self._maybe_extract_binary_string(result, ctx)
        elif isinstance(result, dict):
            return await self._walk_and_extract_binary(result, ctx)
        elif isinstance(result, list):
            return [await self._detect_and_extract_binary(item, ctx) for item in result]

        return result

    async def _maybe_extract_binary_string(self, value: str, ctx: ToolContext) -> Any:
        """Check if a string is base64-encoded binary and extract if so.

        Args:
            value: String to check
            ctx: Tool context

        Returns:
            ArtifactRef dict if binary detected, original string otherwise
        """
        config = self.config.artifact_extraction.binary_detection

        # Skip short strings
        if len(value) < config.min_size_for_detection:
            return value

        # Check for binary signatures
        detected = self._detect_binary_signature(value)
        if detected is None:
            return value

        extension, mime_type = detected

        try:
            # Decode and validate
            data = base64.b64decode(value[: config.max_decode_bytes * 4 // 3])
            if len(data) > config.max_decode_bytes:
                data = data[: config.max_decode_bytes]

            # Optional magic byte validation
            if config.require_magic_bytes and not self._validate_magic_bytes(data, extension):
                return value

            # Store as artifact
            ref = await ctx.artifacts.put_bytes(
                data,
                mime_type=mime_type,
                namespace=self.config.name,
            )

            extract_config = self.config.artifact_extraction
            summary = extract_config.default_binary_summary.format(
                mime_type=mime_type,
                size=len(data),
                artifact_id=ref.id,
            )

            return {
                "artifact": ref.model_dump(),
                "summary": summary,
            }

        except Exception as e:
            logger.debug(f"Binary extraction failed: {e}")
            return value

    async def _walk_and_extract_binary(
        self,
        data: dict[str, Any],
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Recursively walk dict and extract binary content."""
        transformed: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str):
                transformed[key] = await self._maybe_extract_binary_string(value, ctx)
            elif isinstance(value, dict):
                transformed[key] = await self._walk_and_extract_binary(value, ctx)
            elif isinstance(value, list):
                transformed[key] = [await self._detect_and_extract_binary(item, ctx) for item in value]
            else:
                transformed[key] = value
        return transformed

    def _detect_binary_signature(self, value: str) -> tuple[str, str] | None:
        """Check if string starts with a known binary signature.

        Args:
            value: String to check (potentially base64 encoded)

        Returns:
            Tuple of (extension, mime_type) if detected, None otherwise
        """
        signatures = self.config.artifact_extraction.binary_detection.signatures
        for prefix, (ext, mime) in signatures.items():
            if value.startswith(prefix):
                return (ext, mime)
        return None

    def _validate_magic_bytes(self, data: bytes, extension: str) -> bool:
        """Validate binary magic bytes match expected format.

        Args:
            data: Decoded binary data
            extension: Expected file extension

        Returns:
            True if magic bytes match, False otherwise
        """
        magic_bytes = {
            "pdf": b"%PDF",
            "png": b"\x89PNG",
            "jpeg": b"\xff\xd8\xff",
            "gif": b"GIF8",
            "zip": b"PK\x03\x04",
        }

        expected = magic_bytes.get(extension)
        if expected is None:
            return True  # No validation for unknown types

        return data.startswith(expected)

    def _infer_mime_type(self, content_type: str) -> str:
        """Infer MIME type from content type hint.

        Args:
            content_type: Hint like "pdf", "image", "text"

        Returns:
            MIME type string
        """
        mapping = {
            "pdf": "application/pdf",
            "image": "image/png",
            "binary": "application/octet-stream",
            "text": "text/plain",
        }
        return mapping.get(content_type, "application/octet-stream")

    async def _apply_size_limits(self, result: Any, ctx: ToolContext) -> Any:
        """Apply size limits as safety net for oversized content.

        Args:
            result: Tool result
            ctx: Tool context

        Returns:
            Result with oversized strings converted to artifact references
        """
        config = self.config.artifact_extraction

        if not config.auto_artifact_large_content:
            return result

        max_size = config.max_inline_size

        if isinstance(result, str) and len(result) > max_size:
            # Large string - store as text artifact
            ref = await ctx.artifacts.put_text(
                result,
                namespace=self.config.name,
            )
            summary = config.default_text_summary.format(
                size=len(result),
                artifact_id=ref.id,
            )
            return {
                "artifact": ref.model_dump(),
                "summary": summary,
                "preview": result[:500] + "..." if len(result) > 500 else result,
            }

        elif isinstance(result, dict):
            return await self._walk_and_apply_size_limits(result, ctx)

        elif isinstance(result, list):
            return [await self._apply_size_limits(item, ctx) for item in result]

        return result

    async def _walk_and_apply_size_limits(
        self,
        data: dict[str, Any],
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Recursively walk dict and apply size limits."""
        config = self.config.artifact_extraction
        max_size = config.max_inline_size
        transformed: dict[str, Any] = {}

        for key, value in data.items():
            if isinstance(value, str) and len(value) > max_size:
                # Store as text artifact
                ref = await ctx.artifacts.put_text(
                    value,
                    namespace=self.config.name,
                )
                transformed[key] = {
                    "artifact": ref.model_dump(),
                    "summary": config.default_text_summary.format(
                        size=len(value),
                        artifact_id=ref.id,
                    ),
                    "preview": value[:500] + "..." if len(value) > 500 else value,
                }
            elif isinstance(value, dict):
                transformed[key] = await self._walk_and_apply_size_limits(value, ctx)
            elif isinstance(value, list):
                transformed[key] = [await self._apply_size_limits(item, ctx) for item in value]
            else:
                transformed[key] = value

        return transformed

    # ─── MCP Resources Methods (Phase 2) ───────────────────────────────────────

    def _generate_resource_tools(self) -> list[NodeSpec]:
        """Generate NodeSpecs for MCP resource operations.

        Creates tools for:
        - {namespace}.resources_list: List available resources
        - {namespace}.resources_read: Read resource by URI
        - {namespace}.resources_templates_list: List resource templates

        Returns:
            List of NodeSpec for resource tools
        """
        specs: list[NodeSpec] = []

        # Only generate resource tools if resources are supported
        if not self._resources_supported:
            return specs

        namespace = self.config.name

        # Create models for resources_list
        ResourceListInput = create_model(f"{namespace}_ResourceListInput")
        ResourceListOutput = create_model(
            f"{namespace}_ResourceListOutput",
            resources=(list[dict[str, Any]], []),
            count=(int, 0),
        )

        # Create model for resources_read input/output
        ResourceReadInput = create_model(
            f"{namespace}_ResourceReadInput",
            uri=(str, ...),
        )
        ResourceReadOutput = create_model(
            f"{namespace}_ResourceReadOutput",
            result=(dict[str, Any], {}),
            uri=(str, ""),
        )

        # Create model for templates list output
        TemplatesListInput = create_model(f"{namespace}_TemplatesListInput")
        TemplatesListOutput = create_model(
            f"{namespace}_TemplatesListOutput",
            templates=(list[dict[str, Any]], []),
            count=(int, 0),
        )

        # Register in registry (if not already registered)
        if not self.registry.has(f"{namespace}.resources_list"):
            self.registry.register(
                f"{namespace}.resources_list",
                ResourceListInput,
                ResourceListOutput,
            )
        if not self.registry.has(f"{namespace}.resources_read"):
            self.registry.register(
                f"{namespace}.resources_read",
                ResourceReadInput,
                ResourceReadOutput,
            )
        if not self.registry.has(f"{namespace}.resources_templates_list"):
            self.registry.register(
                f"{namespace}.resources_templates_list",
                TemplatesListInput,
                TemplatesListOutput,
            )

        # resources_list tool
        specs.append(
            NodeSpec(
                name=f"{namespace}.resources_list",
                desc=f"List available resources from {namespace}",
                args_model=ResourceListInput,
                out_model=ResourceListOutput,
                node=Node(self._handle_resources_list, name=f"{namespace}.resources_list"),
                tags=["mcp", "resources", namespace],
                extra={"source": "mcp", "namespace": namespace, "tool_node": self, "resource_tool": True},
            )
        )

        # resources_read tool
        specs.append(
            NodeSpec(
                name=f"{namespace}.resources_read",
                desc=f"Read a resource by URI from {namespace}",
                args_model=ResourceReadInput,
                out_model=ResourceReadOutput,
                node=Node(self._handle_resources_read, name=f"{namespace}.resources_read"),
                tags=["mcp", "resources", namespace],
                extra={"source": "mcp", "namespace": namespace, "tool_node": self, "resource_tool": True},
            )
        )

        # resources_templates_list tool
        specs.append(
            NodeSpec(
                name=f"{namespace}.resources_templates_list",
                desc=f"List resource templates from {namespace}",
                args_model=TemplatesListInput,
                out_model=TemplatesListOutput,
                node=Node(self._handle_resources_templates_list, name=f"{namespace}.resources_templates_list"),
                tags=["mcp", "resources", namespace],
                extra={"source": "mcp", "namespace": namespace, "tool_node": self, "resource_tool": True},
            )
        )

        # Store tool name mappings
        self._tool_name_map[f"{namespace}.resources_list"] = "resources_list"
        self._tool_name_map[f"{namespace}.resources_read"] = "resources_read"
        self._tool_name_map[f"{namespace}.resources_templates_list"] = "resources_templates_list"

        logger.debug(
            "Generated %d resource tools for '%s'",
            len(specs),
            namespace,
        )

        return specs

    async def _handle_resources_list(
        self,
        _args: Any,
        _ctx: ToolContext,
    ) -> dict[str, Any]:
        """Handler for resources_list tool."""
        resources = await self.list_resources()
        return {
            "resources": [r.model_dump() for r in resources],
            "count": len(resources),
        }

    async def _handle_resources_read(
        self,
        args: Any,
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Handler for resources_read tool."""
        uri = getattr(args, "uri", args.get("uri") if isinstance(args, dict) else None)
        if not uri:
            return {"result": {"error": "Missing required 'uri' parameter"}, "uri": ""}

        result = await self.read_resource(uri, ctx)
        return {"result": result, "uri": uri}

    async def _handle_resources_templates_list(
        self,
        _args: Any,
        _ctx: ToolContext,
    ) -> dict[str, Any]:
        """Handler for resources_templates_list tool."""
        templates = await self.list_resource_templates()
        return {
            "templates": [t.model_dump() for t in templates],
            "count": len(templates),
        }

    async def list_resources(self, refresh: bool = False) -> list[ResourceInfo]:
        """List available MCP resources.

        Args:
            refresh: Force refresh from server

        Returns:
            List of ResourceInfo objects (empty if not connected or resources not supported)
        """
        if not self._connected or not self._resources_supported:
            return self._resources  # Return cached resources or empty list

        if refresh and self._mcp_client is not None:
            try:
                resources = await self._mcp_client.list_resources()
                self._resources = [
                    ResourceInfo(
                        uri=str(getattr(r, "uri", r.get("uri", "")) if isinstance(r, dict) else r.uri),
                        name=getattr(r, "name", r.get("name") if isinstance(r, dict) else None),
                        description=getattr(r, "description", r.get("description") if isinstance(r, dict) else None),
                        mime_type=getattr(r, "mimeType", r.get("mimeType") if isinstance(r, dict) else None),
                    )
                    for r in (resources if isinstance(resources, list) else [])
                ]
            except Exception as e:
                logger.warning(f"Failed to refresh resources: {e}")

        return self._resources

    async def list_resource_templates(self, refresh: bool = False) -> list[ResourceTemplateInfo]:
        """List available MCP resource templates.

        Args:
            refresh: Force refresh from server

        Returns:
            List of ResourceTemplateInfo objects (empty if not connected or not supported)
        """
        if not self._connected or not self._resources_supported:
            return self._resource_templates  # Return cached templates or empty list

        if refresh and self._mcp_client is not None:
            try:
                templates = await self._mcp_client.list_resource_templates()
                self._resource_templates = [
                    ResourceTemplateInfo(
                        uri_template=str(
                            getattr(t, "uriTemplate", t.get("uriTemplate", ""))
                            if isinstance(t, dict)
                            else t.uriTemplate
                        ),
                        name=getattr(t, "name", t.get("name") if isinstance(t, dict) else None),
                        description=getattr(t, "description", t.get("description") if isinstance(t, dict) else None),
                        mime_type=getattr(t, "mimeType", t.get("mimeType") if isinstance(t, dict) else None),
                    )
                    for t in (templates if isinstance(templates, list) else [])
                ]
            except Exception as e:
                logger.warning(f"Failed to refresh resource templates: {e}")

        return self._resource_templates

    async def read_resource(
        self,
        uri: str,
        ctx: ToolContext,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Read a resource by URI.

        Uses ResourceCache to avoid repeated fetches. Binary content is
        stored in ArtifactStore; small text may be inlined.

        Args:
            uri: Resource URI to read
            ctx: Tool context with artifact store access
            use_cache: Whether to use caching (default True)

        Returns:
            Dict with 'artifact' (ArtifactRef) or 'text' (inline content) or 'error'
        """
        if not self._connected:
            return {"error": f"ToolNode '{self.config.name}' not connected"}

        if not self._resources_supported:
            return {"error": f"Resources not supported by '{self.config.name}'"}

        if self._mcp_client is None:
            return {"error": "MCP client not available"}

        # Initialize resource cache if needed
        if self._resource_cache is None:
            self._resource_cache = ResourceCache(
                artifact_store=ctx.artifacts,
                namespace=self.config.name,
                config=ResourceCacheConfig(
                    inline_text_if_under_chars=self.config.artifact_extraction.resources.inline_text_if_under_chars,
                ),
            )

        # Capture client for closure (preserves type narrowing)
        mcp_client = self._mcp_client

        # Read function for cache
        async def read_fn(resource_uri: str) -> Any:
            return await mcp_client.read_resource(resource_uri)

        if use_cache:
            return await self._resource_cache.get_or_fetch(uri, read_fn, ctx)
        else:
            # Bypass cache, read directly
            try:
                contents = await self._mcp_client.read_resource(uri)
                # Process contents similar to cache
                return await self._process_resource_contents(contents, ctx)
            except Exception as e:
                return {"error": str(e)}

    async def _process_resource_contents(
        self,
        contents: Any,
        ctx: ToolContext,
    ) -> dict[str, Any]:
        """Process resource contents into artifact or inline text."""
        import base64

        # Extract content from various formats
        if isinstance(contents, dict):
            resource_data = contents.get("contents", [contents])
            if isinstance(resource_data, list) and resource_data:
                content_item = resource_data[0]
            else:
                content_item = resource_data
        else:
            content_item = contents

        text = getattr(content_item, "text", None) or (
            content_item.get("text") if isinstance(content_item, dict) else None
        )
        blob = getattr(content_item, "blob", None) or (
            content_item.get("blob") if isinstance(content_item, dict) else None
        )
        mime_type = getattr(content_item, "mimeType", None) or (
            content_item.get("mimeType") if isinstance(content_item, dict) else None
        )

        if blob is not None:
            try:
                data = base64.b64decode(blob)
                ref = await ctx.artifacts.put_bytes(
                    data,
                    mime_type=mime_type or "application/octet-stream",
                    namespace=f"{self.config.name}.resource",
                )
                return {"artifact": ref.model_dump()}
            except Exception as e:
                return {"error": f"Failed to process blob: {e}"}

        elif text is not None:
            threshold = self.config.artifact_extraction.resources.inline_text_if_under_chars
            if len(text) <= threshold:
                return {"text": text}
            else:
                ref = await ctx.artifacts.put_text(
                    text,
                    mime_type=mime_type or "text/plain",
                    namespace=f"{self.config.name}.resource",
                )
                return {"artifact": ref.model_dump()}

        return {"error": "Resource has no content"}

    async def subscribe_resource(
        self,
        uri: str,
        callback: Any | None = None,
    ) -> bool:
        """Subscribe to resource updates.

        Args:
            uri: Resource URI to subscribe to
            callback: Optional callback for updates

        Returns:
            True if subscription successful
        """
        if not self._connected or self._mcp_client is None:
            return False

        if not self._resources_supported:
            return False

        if self._subscription_manager is None:
            return False

        # Capture client for closure (preserves type narrowing)
        mcp_client = self._mcp_client

        async def subscribe_fn(resource_uri: str) -> None:
            await mcp_client.subscribe(resource_uri)

        return await self._subscription_manager.subscribe(uri, subscribe_fn, callback)

    async def unsubscribe_resource(self, uri: str) -> bool:
        """Unsubscribe from resource updates.

        Args:
            uri: Resource URI to unsubscribe from

        Returns:
            True if unsubscription successful
        """
        if not self._connected or self._mcp_client is None:
            return False

        if self._subscription_manager is None:
            return False

        # Capture client for closure (preserves type narrowing)
        mcp_client = self._mcp_client

        async def unsubscribe_fn(resource_uri: str) -> None:
            await mcp_client.unsubscribe(resource_uri)

        return await self._subscription_manager.unsubscribe(uri, unsubscribe_fn)

    def handle_resource_updated(self, uri: str) -> None:
        """Handle a resource updated notification from MCP server.

        Invalidates cache and notifies subscribers.

        Args:
            uri: URI of updated resource
        """
        # Invalidate cache
        if self._resource_cache is not None:
            self._resource_cache.invalidate(uri)

        if self._resource_update_callback is not None:
            try:
                self._resource_update_callback(uri)
            except Exception:
                logger.debug("resource_update_callback_failed", exc_info=True)

        # Notify subscribers (only if in async context)
        if self._subscription_manager is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._subscription_manager.handle_update(uri))
            except RuntimeError:
                # No running loop - skip subscriber notification
                pass

    def set_resource_updated_callback(self, callback: Callable[[str], None] | None) -> None:
        """Register a callback invoked when MCP resources are updated."""
        self._resource_update_callback = callback

    @property
    def resources_supported(self) -> bool:
        """Check if MCP resources are supported by this tool source."""
        return self._resources_supported

    @property
    def resources(self) -> list[ResourceInfo]:
        """Get cached list of resources (call list_resources for refresh)."""
        return self._resources

    @property
    def resource_templates(self) -> list[ResourceTemplateInfo]:
        """Get cached list of resource templates."""
        return self._resource_templates


__all__ = ["ToolNode"]
