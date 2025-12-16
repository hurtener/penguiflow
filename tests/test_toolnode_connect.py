"""Tests for ToolNode connect methods and MCP/UTCP integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.errors import ToolConnectionError, ToolNodeError
from penguiflow.tools.node import ToolNode

pytest.importorskip("tenacity")


def build_config(**overrides):
    base = {
        "name": "test_tool",
        "transport": TransportType.MCP,
        "connection": "npx -y test-server",
    }
    base.update(overrides)
    return ExternalToolConfig(**base)


class FakeMcpTool:
    def __init__(self, name: str, description: str = "", input_schema: dict = None):
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {}


class FakeUtcpTool:
    def __init__(self, name: str, description: str = "", inputs: dict = None):
        self.name = name
        self.description = description
        self.inputs = inputs or {}


# ─── Test connect method ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_already_connected():
    """connect() should be idempotent when already connected."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    node._connected = True
    node._connected_loop = asyncio.get_running_loop()

    await node.connect()  # Should return immediately
    assert node._connected is True


@pytest.mark.asyncio
async def test_connect_unsupported_transport():
    """connect() should raise for unsupported transport."""
    registry = ModelRegistry()
    # Create config with invalid transport
    config = build_config()
    # Manually set an unsupported transport value
    object.__setattr__(config, "transport", MagicMock(value="unsupported"))

    node = ToolNode(config=config, registry=registry)

    with pytest.raises(ToolConnectionError, match="not supported"):
        await node.connect()




# ─── Test UTCP tool conversion ───────────────────────────────────────────────


def test_convert_utcp_tools_basic():
    """_convert_utcp_tools should namespace tools correctly."""
    registry = ModelRegistry()
    config = build_config(name="api", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    utcp_tool = FakeUtcpTool(
        name="create_item",
        description="Create an item",
        inputs={"properties": {"name": {"type": "string"}}},
    )
    specs = node._convert_utcp_tools([utcp_tool])

    assert len(specs) == 1
    assert specs[0].name == "api.create_item"
    assert node._tool_name_map["api.create_item"] == "create_item"


def test_convert_utcp_tools_with_namespace_prefix():
    """_convert_utcp_tools should handle tools with namespace prefix."""
    registry = ModelRegistry()
    config = build_config(name="api", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    # Tool name already has prefix like "manual.create_item"
    utcp_tool = FakeUtcpTool(name="manual.create_item", description="Create")
    specs = node._convert_utcp_tools([utcp_tool])

    assert specs[0].name == "api.create_item"
    assert node._tool_name_map["api.create_item"] == "manual.create_item"


def test_convert_utcp_tools_rejects_duplicates():
    """_convert_utcp_tools should reject duplicate tool names."""
    registry = ModelRegistry()
    config = build_config(name="api", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    tool = FakeUtcpTool(name="dup", description="")
    node._convert_utcp_tools([tool])

    with pytest.raises(ToolNodeError):
        node._convert_utcp_tools([tool])


def test_convert_utcp_tools_respects_filter():
    """_convert_utcp_tools should respect tool_filter."""
    registry = ModelRegistry()
    config = build_config(
        name="api",
        transport=TransportType.UTCP,
        tool_filter=["get_.*"],
    )
    node = ToolNode(config=config, registry=registry)

    tools = [
        FakeUtcpTool(name="get_item", description=""),
        FakeUtcpTool(name="create_item", description=""),
    ]
    specs = node._convert_utcp_tools(tools)

    assert len(specs) == 1
    assert specs[0].name == "api.get_item"


# ─── Test reconnection ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_triggers_reconnect_on_new_loop():
    """call() should reconnect when event loop changes."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Simulate connected on different loop
    node._connected = True
    node._connected_loop = MagicMock()  # Different from current loop

    # Mock reconnection
    reconnect_called = False

    async def mock_force_reconnect():
        nonlocal reconnect_called
        reconnect_called = True
        node._connected = True
        node._connected_loop = asyncio.get_running_loop()
        node._mcp_client = MagicMock()
        node._mcp_client.call_tool = AsyncMock(return_value={"ok": True})
        node._tool_name_map["test.ping"] = "ping"

    node._force_reconnect = mock_force_reconnect
    node._tool_name_map["test.ping"] = "ping"

    class DummyCtx:
        tool_context = {}

        async def pause(self, reason, payload=None):
            pass

    await node.call("test.ping", {}, DummyCtx())
    assert reconnect_called


# ─── Test MCP result serialization edge cases ────────────────────────────────


def test_serialize_mcp_result_content_with_many_texts():
    """Content with multiple text blocks should return list."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)

    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeResult:
        structuredContent = None
        content = [FakeTextBlock("one"), FakeTextBlock("two"), FakeTextBlock("three")]

    result = node._serialize_mcp_result(FakeResult())
    assert result == ["one", "two", "three"]
