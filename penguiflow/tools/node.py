"""ToolNode MCP integration (Phase 1)."""

from __future__ import annotations

import asyncio
import functools
import re
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, create_model

from penguiflow.catalog import NodeSpec
from penguiflow.node import Node
from penguiflow.planner.context import ToolContext
from penguiflow.registry import ModelRegistry

from .adapters import adapt_exception
from .config import AuthType, ExternalToolConfig, TransportType, UtcpMode
from .errors import ToolAuthError, ToolConnectionError, ToolNodeError

if TYPE_CHECKING:
    pass


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

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Connect to tool source and discover available tools."""
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

            if self.config.transport == TransportType.MCP:
                await self._connect_mcp()
            elif self.config.transport in {TransportType.HTTP, TransportType.UTCP, TransportType.CLI}:
                await self._connect_utcp()
            else:
                raise ToolConnectionError(
                    f"Transport '{self.config.transport.value}' not supported",
                )

            self._connected = True
            self._connected_loop = current_loop

    async def _connect_mcp(self) -> None:
        """Connect via FastMCP client."""
        try:
            from fastmcp import Client as MCPClient
            from fastmcp.client.transports import StdioTransport
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
            raise ToolConnectionError(
                "fastmcp is required for MCP ToolNode. Install penguiflow[planner].",
            ) from exc

        try:
            # Determine transport: shell command or URL
            connection = self.config.connection
            transport: Any
            if connection.startswith(("http://", "https://", "ws://", "wss://")):
                # URL-based transport (SSE, WebSocket) - let fastmcp infer
                transport = connection
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
            # Wrap result to match the output model schema: {"result": <data>}
            return {"result": result}

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
        import json

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

            specs.append(
                NodeSpec(
                    node=Node(bound_fn, name=namespaced),
                    name=namespaced,
                    desc=getattr(tool, "description", "") or "",
                    args_model=args_model,
                    out_model=out_model,
                    side_effects="external",
                    tags=("mcp", self.config.name),
                    extra={"source": "mcp", "namespace": self.config.name},
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

            specs.append(
                NodeSpec(
                    node=Node(bound_fn, name=namespaced),
                    name=namespaced,
                    desc=getattr(tool, "description", "") or "",
                    args_model=args_model,
                    out_model=out_model,
                    side_effects="external",
                    tags=("utcp", self.config.name),
                    extra={"source": "utcp", "namespace": self.config.name},
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


__all__ = ["ToolNode"]
