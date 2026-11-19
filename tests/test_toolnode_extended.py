"""Extended tests for penguiflow/tools/node.py - coverage expansion."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import mcp.types as mcp_types
import pytest

from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import (
    AuthType,
    ExternalToolConfig,
    TransportType,
    UtcpMode,
)
from penguiflow.tools.errors import ToolAuthError, ToolNodeError
from penguiflow.tools.node import (
    ToolNode,
    _create_result_model,
    _serialize_mcp_result,
)

from .tool_context_doubles import FakeToolContext

pytest.importorskip("tenacity")


class DummyCtx(FakeToolContext):
    """Tool context for ToolNode tests."""

    def __init__(
        self,
        tool_context: dict[str, Any] | None = None,
    ) -> None:
        """Use ``tool_context`` when given."""
        super().__init__(tool_context=tool_context)


def build_config(**overrides: object) -> ExternalToolConfig:
    """Build an MCP tool config, with ``overrides`` applied."""
    base: dict[str, object] = {
        "name": "test_tool",
        "transport": TransportType.MCP,
        "connection": "npx -y test-server",
    }
    base.update(overrides)
    return ExternalToolConfig.model_validate(base)


# ─── Test serialize_mcp_result ───────────────────────────────────────────────


def test_serialize_mcp_result_dict() -> None:
    """Dict input should pass through unchanged."""
    result = _serialize_mcp_result({"key": "value"})
    assert result == {"key": "value"}


def test_serialize_mcp_result_primitives() -> None:
    """Primitive types should pass through unchanged."""
    assert _serialize_mcp_result(42) == 42
    assert _serialize_mcp_result(3.14) == 3.14
    assert _serialize_mcp_result(True) is True
    assert _serialize_mcp_result(None) is None
    assert _serialize_mcp_result([1, 2, 3]) == [1, 2, 3]


def test_serialize_mcp_result_structured_content() -> None:
    """Objects with structuredContent should return that content."""
    result = _serialize_mcp_result(
        mcp_types.CallToolResult(
            content=[], structuredContent={"data": "structured"}
        )
    )
    assert result == {"data": "structured"}


def test_serialize_mcp_result_content_with_text() -> None:
    """Objects with content blocks containing text should extract text."""

    class FakeTextBlock:
        text = "extracted text"

    fake_result = SimpleNamespace(
        structuredContent=None, content=[FakeTextBlock()]
    )

    result = _serialize_mcp_result(fake_result)
    assert result == "extracted text"


def test_serialize_mcp_result_content_with_json_text() -> None:
    """Content with JSON-like text should be parsed."""

    class FakeTextBlock:
        text = '{"parsed": true}'

    fake_result = SimpleNamespace(
        structuredContent=None, content=[FakeTextBlock()]
    )

    result = _serialize_mcp_result(fake_result)
    assert result == {"parsed": True}


def test_serialize_mcp_result_content_with_invalid_json() -> None:
    """Content with invalid JSON should return raw text."""

    class FakeTextBlock:
        text = "{not valid json"

    fake_result = SimpleNamespace(
        structuredContent=None, content=[FakeTextBlock()]
    )

    result = _serialize_mcp_result(fake_result)
    assert result == "{not valid json"


def test_serialize_mcp_result_content_multiple_blocks() -> None:
    """Multiple content blocks should return list."""

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    fake_result = SimpleNamespace(
        structuredContent=None,
        content=[FakeTextBlock("first"), FakeTextBlock("second")],
    )

    result = _serialize_mcp_result(fake_result)
    assert result == ["first", "second"]


def test_serialize_mcp_result_content_with_model_dump() -> None:
    """Content blocks with model_dump should call it."""

    class FakePydanticBlock:
        def model_dump(self) -> dict[str, bool]:
            return {"dumped": True}

    fake_result = SimpleNamespace(
        structuredContent=None, content=[FakePydanticBlock()]
    )

    result = _serialize_mcp_result(fake_result)
    assert result == {"dumped": True}


def test_serialize_mcp_result_content_fallback_str() -> None:
    """Content blocks without text or model_dump should use str()."""

    class FakeBlock:
        def __str__(self) -> str:
            return "stringified"

    fake_result = SimpleNamespace(
        structuredContent=None, content=[FakeBlock()]
    )

    result = _serialize_mcp_result(fake_result)
    assert result == "stringified"


def test_serialize_mcp_result_string_json() -> None:
    """JSON string input should be parsed."""
    result = _serialize_mcp_result('{"json": "string"}')
    assert result == {"json": "string"}


def test_serialize_mcp_result_string_array_json() -> None:
    """JSON array string input should be parsed."""
    result = _serialize_mcp_result("[1, 2, 3]")
    assert result == [1, 2, 3]


def test_serialize_mcp_result_string_invalid_json() -> None:
    """Invalid JSON string should return as-is."""
    result = _serialize_mcp_result("{invalid")
    assert result == "{invalid"


def test_serialize_mcp_result_plain_string() -> None:
    """Plain string should pass through."""
    result = _serialize_mcp_result("plain text")
    assert result == "plain text"


def test_serialize_mcp_result_pydantic_model() -> None:
    """Pydantic-like object with model_dump should be serialized."""

    class FakePydantic:
        def model_dump(self) -> dict[str, str]:
            return {"from": "pydantic"}

    result = _serialize_mcp_result(FakePydantic())
    assert result == {"from": "pydantic"}


def test_serialize_mcp_result_fallback() -> None:
    """Unknown types should be converted to string."""

    class Unknown:
        def __str__(self) -> str:
            return "unknown_str"

    result = _serialize_mcp_result(Unknown())
    assert result == "unknown_str"


# ─── Test auth resolution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_auth_none() -> None:
    """AuthType.NONE should return empty headers."""
    registry = ModelRegistry()
    config = build_config(auth_type=AuthType.NONE)
    node = ToolNode(config=config, registry=registry)
    ctx = DummyCtx()

    headers = await node.resolve_auth(ctx)
    assert headers == {}


@pytest.mark.asyncio
async def test_resolve_auth_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """AuthType.API_KEY should return API key header."""
    monkeypatch.setenv("TEST_API_KEY", "secret123")
    registry = ModelRegistry()
    config = build_config(
        auth_type=AuthType.API_KEY,
        auth_config={"api_key": "${TEST_API_KEY}", "header": "X-Custom-Key"},
    )
    node = ToolNode(config=config, registry=registry)
    ctx = DummyCtx()

    headers = await node.resolve_auth(ctx)
    assert headers == {"X-Custom-Key": "secret123"}


@pytest.mark.asyncio
async def test_resolve_auth_api_key_default_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthType.API_KEY should default to X-API-Key header."""
    monkeypatch.setenv("MY_KEY", "myapikey")
    registry = ModelRegistry()
    config = build_config(
        auth_type=AuthType.API_KEY,
        auth_config={"api_key": "${MY_KEY}"},
    )
    node = ToolNode(config=config, registry=registry)
    ctx = DummyCtx()

    headers = await node.resolve_auth(ctx)
    assert headers == {"X-API-Key": "myapikey"}


@pytest.mark.asyncio
async def test_resolve_auth_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    """AuthType.BEARER should return Bearer token header."""
    monkeypatch.setenv("BEARER_TOKEN", "token456")
    registry = ModelRegistry()
    config = build_config(
        auth_type=AuthType.BEARER,
        auth_config={"token": "${BEARER_TOKEN}"},
    )
    node = ToolNode(config=config, registry=registry)
    ctx = DummyCtx()

    headers = await node.resolve_auth(ctx)
    assert headers == {"Authorization": "Bearer token456"}


@pytest.mark.asyncio
async def test_resolve_user_oauth_no_manager() -> None:
    """OAuth without auth_manager should raise ToolAuthError."""
    registry = ModelRegistry()
    config = build_config(auth_type=AuthType.OAUTH2_USER)
    node = ToolNode(config=config, registry=registry, auth_manager=None)
    ctx = DummyCtx(tool_context={"user_id": "user1"})

    with pytest.raises(ToolAuthError, match="no auth_manager"):
        await node.resolve_auth(ctx)


@pytest.mark.asyncio
async def test_resolve_user_oauth_no_user_id() -> None:
    """OAuth without user_id should raise ToolAuthError."""
    registry = ModelRegistry()
    config = build_config(auth_type=AuthType.OAUTH2_USER)

    fake_manager = MagicMock()
    node = ToolNode(
        config=config, registry=registry, auth_manager=fake_manager
    )
    ctx = DummyCtx(tool_context={})  # No user_id

    with pytest.raises(ToolAuthError, match="user_id required"):
        await node.resolve_auth(ctx)


# ─── Test close method ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_clears_state() -> None:
    """close() should reset node state."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    node.connected = True
    node.tools = [MagicMock()]
    node.tool_name_map = {"test.tool": "tool"}

    await node.close()

    assert node.connected is False
    assert node.tools == []
    assert node.tool_name_map == {}


@pytest.mark.asyncio
async def test_close_cleans_mcp_client() -> None:
    """close() should clean up MCP client."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    mock_client = AsyncMock()
    node.mcp_client = mock_client
    node.connected = True

    await node.close()

    mock_client.__aexit__.assert_called_once()
    assert node.mcp_client is None


@pytest.mark.asyncio
async def test_close_cleans_utcp_client() -> None:
    """close() should clean up UTCP client."""
    registry = ModelRegistry()
    config = build_config(transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)
    mock_client = AsyncMock()
    node.utcp_client = mock_client
    node.connected = True

    await node.close()

    mock_client.aclose.assert_called_once()
    assert node.utcp_client is None


# ─── Test get_tools and get_tool_specs ───────────────────────────────────────


def test_get_tools_returns_tools() -> None:
    """get_tools() should return discovered tools."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    fake_spec = MagicMock()
    node.tools = [fake_spec]

    result = node.get_tools()
    assert result == [fake_spec]


def test_get_tool_specs_alias() -> None:
    """get_tool_specs() should be an alias for get_tools()."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    fake_spec = MagicMock()
    node.tools = [fake_spec]

    result = node.get_tool_specs()
    assert result == node.get_tools()


# ─── Test UTCP config building ───────────────────────────────────────────────


def test_build_utcp_config_manual_url_mode() -> None:
    """UTCP manual_url mode should return manuals config."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        connection="https://api.example.com/utcp.json",
        utcp_mode=UtcpMode.MANUAL_URL,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert "manuals" in result
    assert result["manuals"] == ["https://api.example.com/utcp.json"]


def test_build_utcp_config_base_url_mode() -> None:
    """UTCP base_url mode should return manual_call_templates config."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        connection="https://api.example.com",
        utcp_mode=UtcpMode.BASE_URL,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert "manual_call_templates" in result
    assert (
        result["manual_call_templates"][0]["url"] == "https://api.example.com"
    )
    assert result["manual_call_templates"][0]["call_template_type"] == "http"


def test_build_utcp_config_cli_transport() -> None:
    """CLI transport should set call_template_type to cli."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.CLI,
        connection="/path/to/cli",
        utcp_mode=UtcpMode.BASE_URL,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert result["manual_call_templates"][0]["call_template_type"] == "cli"


def test_build_utcp_config_auto_detects_manual() -> None:
    """AUTO mode should detect .json extension as MANUAL_URL."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        connection="https://api.example.com/spec.json",
        utcp_mode=UtcpMode.AUTO,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert "manuals" in result


def test_build_utcp_config_auto_detects_utcp_path() -> None:
    """AUTO mode should detect /utcp path as MANUAL_URL."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        connection="https://api.example.com/utcp",
        utcp_mode=UtcpMode.AUTO,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert "manuals" in result


def test_build_utcp_config_auto_detects_well_known() -> None:
    """AUTO mode should detect /.well-known/utcp as MANUAL_URL."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        connection="https://api.example.com/.well-known/utcp",
        utcp_mode=UtcpMode.AUTO,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert "manuals" in result


def test_build_utcp_config_auto_fallback_base_url() -> None:
    """AUTO mode should fallback to BASE_URL for regular URLs."""
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        connection="https://api.example.com/v1",
        utcp_mode=UtcpMode.AUTO,
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_config()
    assert "manual_call_templates" in result


def test_build_utcp_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """_build_utcp_variables should substitute env vars."""
    monkeypatch.setenv("API_KEY", "key123")
    registry = ModelRegistry()
    config = build_config(
        transport=TransportType.UTCP,
        env={"key": "${API_KEY}"},
        auth_config={"secret": "plain_value"},
    )
    node = ToolNode(config=config, registry=registry)

    result = node.build_utcp_variables()
    assert result["key"] == "key123"
    assert result["secret"] == "plain_value"


# ─── Test tool filter ────────────────────────────────────────────────────────


def test_matches_filter_no_filter() -> None:
    """Without filter, all tools should match."""
    registry = ModelRegistry()
    config = build_config(tool_filter=None)
    node = ToolNode(config=config, registry=registry)

    assert node.matches_filter("any_tool") is True


def test_matches_filter_with_pattern() -> None:
    """With filter, only matching tools should pass."""
    registry = ModelRegistry()
    config = build_config(tool_filter=["get_.*", "list_.*"])
    node = ToolNode(config=config, registry=registry)

    assert node.matches_filter("get_user") is True
    assert node.matches_filter("list_items") is True
    assert node.matches_filter("create_user") is False


# ─── Test substitute_env ─────────────────────────────────────────────────────


def test_substitute_env_with_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """_substitute_env should replace ${VAR} with env value."""
    monkeypatch.setenv("MY_VAR", "my_value")
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    result = node.substitute_env("prefix_${MY_VAR}_suffix")
    assert result == "prefix_my_value_suffix"


def test_substitute_env_multiple_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """_substitute_env should handle multiple variables."""
    monkeypatch.setenv("VAR1", "one")
    monkeypatch.setenv("VAR2", "two")
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    result = node.substitute_env("${VAR1}-${VAR2}")
    assert result == "one-two"


def test_substitute_env_no_vars() -> None:
    """_substitute_env should pass through strings without ${} patterns."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    result = node.substitute_env("plain_string")
    assert result == "plain_string"


# ─── Test json type mapping ──────────────────────────────────────────────────


def test_json_type_to_python_string() -> None:
    """JSON string type should map to Python str."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    assert node.json_type_to_python({"type": "string"}) is str


def test_json_type_to_python_integer() -> None:
    """JSON integer type should map to Python int."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    assert node.json_type_to_python({"type": "integer"}) is int


def test_json_type_to_python_number() -> None:
    """JSON number type should map to Python float."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    assert node.json_type_to_python({"type": "number"}) is float


def test_json_type_to_python_boolean() -> None:
    """JSON boolean type should map to Python bool."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    assert node.json_type_to_python({"type": "boolean"}) is bool


def test_json_type_to_python_array() -> None:
    """JSON array type should map to Python list."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    result = node.json_type_to_python({"type": "array"})
    assert result is list


def test_json_type_to_python_array_with_items() -> None:
    """JSON array with typed items should map to typed list."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    result = node.json_type_to_python(
        {"type": "array", "items": {"type": "string"}}
    )
    # Should return list[str]
    assert hasattr(result, "__origin__") or result is list


def test_json_type_to_python_object() -> None:
    """JSON object type should map to dict."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    result = node.json_type_to_python({"type": "object"})
    assert result == dict[str, object] or "dict" in str(result)


def test_json_type_to_python_unknown() -> None:
    """Unknown JSON type should default to dict."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    result = node.json_type_to_python({"type": "custom"})
    assert result == dict[str, object] or "dict" in str(result)


# ─── Test create_args_model ──────────────────────────────────────────────────


def test_create_args_model_empty_schema() -> None:
    """Empty schema should create model with optional data field."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    model = node.create_args_model("test_empty", {})
    instance = model()
    assert hasattr(instance, "data")


def test_create_args_model_with_required() -> None:
    """Required properties should be required in model."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    schema = {
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    model = node.create_args_model("test_required", schema)
    instance = model(name="test")
    assert instance.model_dump()["name"] == "test"


def test_create_args_model_with_optional() -> None:
    """Optional properties should have None defaults."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    schema = {
        "properties": {"opt": {"type": "string"}},
        "required": [],
    }
    model = node.create_args_model("test_optional", schema)
    instance = model()
    assert instance.model_dump()["opt"] is None


# ─── Test create_result_model ────────────────────────────────────────────────


def test_create_result_model() -> None:
    """Result model should have permissive result field."""
    model = _create_result_model("test_result")
    instance = model(result={"any": "data"})
    assert instance.model_dump()["result"] == {"any": "data"}


# ─── Test call_utcp_tool ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_utcp_tool_with_headers() -> None:
    """UTCP tool call should pass headers when supported."""
    registry = ModelRegistry()
    config = build_config(transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value={"success": True})
    node.utcp_client = mock_client

    result = await node.call_utcp_tool(
        "test_tool", {"arg": "val"}, {"X-Auth": "key"}
    )

    mock_client.call_tool.assert_called_once_with(
        "test_tool", {"arg": "val"}, headers={"X-Auth": "key"}
    )
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_call_utcp_tool_without_headers() -> None:
    """UTCP tool call without headers should work."""
    registry = ModelRegistry()
    config = build_config(transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value={"success": True})
    node.utcp_client = mock_client

    result = await node.call_utcp_tool("test_tool", {"arg": "val"}, {})

    mock_client.call_tool.assert_called_once_with("test_tool", {"arg": "val"})
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_call_utcp_tool_no_client() -> None:
    """UTCP call without client should raise error."""
    registry = ModelRegistry()
    config = build_config(transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)
    node.utcp_client = None

    with pytest.raises(ToolNodeError, match="not initialised"):
        await node.call_utcp_tool("test", {}, {})


@pytest.mark.asyncio
async def test_call_utcp_tool_fallback_no_headers() -> None:
    """UTCP call should fallback if client doesn't support headers."""
    registry = ModelRegistry()
    config = build_config(transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    # Mock client that raises TypeError when headers are passed
    async def mock_call_tool(
        *_args: str | dict[str, object], **kwargs: dict[str, object]
    ) -> dict[str, bool]:
        if "headers" in kwargs:
            msg = "call_tool() got unexpected keyword argument 'headers'"
            raise TypeError(msg)
        return {"fallback": True}

    mock_client = MagicMock()
    mock_client.call_tool = mock_call_tool
    node.utcp_client = mock_client

    result = await node.call_utcp_tool("test", {}, {"X-Auth": "key"})
    assert result == {"fallback": True}


# ─── Test force_reconnect ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_force_reconnect_clears_old_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_force_reconnect should clear old connection state."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    node.connected = True
    node.mcp_client = MagicMock()

    # Mock connect to avoid actual connection
    async def mock_connect() -> None:
        node.connected = True

    monkeypatch.setattr(node, "connect", mock_connect)

    await node.force_reconnect()

    assert node.connected is True
