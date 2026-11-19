"""Tests for ToolNode connect methods and MCP/UTCP integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.errors import ToolConnectionError, ToolNodeError
from penguiflow.tools.node import ToolNode, _serialize_mcp_result

from .tool_context_doubles import FakeToolContext

pytest.importorskip("tenacity")


def build_config(**overrides: object) -> ExternalToolConfig:
    """Build an MCP tool config, with ``overrides`` applied."""
    base: dict[str, object] = {
        "name": "test_tool",
        "transport": TransportType.MCP,
        "connection": "npx -y test-server",
    }
    base.update(overrides)
    return ExternalToolConfig.model_validate(base)


class FakeMcpTool:
    """MCP tool as listed by the server."""

    def __init__(
        self,
        name: str,
        description: str = "",
        input_schema: dict[str, object] | None = None,
    ) -> None:
        """Store ``name``, ``description`` and ``input_schema``."""
        self.name = name
        self.description = description
        self.inputSchema = input_schema or {}


class FakeUtcpTool:
    """UTCP tool as listed by the manual."""

    def __init__(
        self,
        name: str,
        description: str = "",
        inputs: dict[str, object] | None = None,
    ) -> None:
        """Store ``name``, ``description`` and ``inputs``."""
        self.name = name
        self.description = description
        self.inputs = inputs or {}


# ─── Test connect method ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_already_connected() -> None:
    """connect() should be idempotent when already connected."""
    registry = ModelRegistry()
    node = ToolNode(config=build_config(), registry=registry)
    node.connected = True
    node.connected_loop = asyncio.get_running_loop()

    await node.connect()  # Should return immediately
    assert node.connected is True


@pytest.mark.asyncio
async def test_connect_unsupported_transport() -> None:
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


def test_convert_utcp_tools_basic() -> None:
    """_convert_utcp_tools should namespace tools correctly."""
    registry = ModelRegistry()
    config = build_config(name="api", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    utcp_tool = FakeUtcpTool(
        name="create_item",
        description="Create an item",
        inputs={"properties": {"name": {"type": "string"}}},
    )
    specs = node.convert_utcp_tools([utcp_tool])

    assert len(specs) == 1
    assert specs[0].name == "api.create_item"
    assert node.tool_name_map["api.create_item"] == "create_item"


def test_convert_utcp_tools_with_namespace_prefix() -> None:
    """_convert_utcp_tools should handle tools with namespace prefix."""
    registry = ModelRegistry()
    config = build_config(name="api", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    # Tool name already has prefix like "manual.create_item"
    utcp_tool = FakeUtcpTool(name="manual.create_item", description="Create")
    specs = node.convert_utcp_tools([utcp_tool])

    assert specs[0].name == "api.create_item"
    assert node.tool_name_map["api.create_item"] == "manual.create_item"


def test_convert_utcp_tools_rejects_duplicates() -> None:
    """_convert_utcp_tools should reject duplicate tool names."""
    registry = ModelRegistry()
    config = build_config(name="api", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    tool = FakeUtcpTool(name="dup", description="")
    node.convert_utcp_tools([tool])

    with pytest.raises(ToolNodeError):
        node.convert_utcp_tools([tool])


def test_convert_utcp_tools_respects_filter() -> None:
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
    specs = node.convert_utcp_tools(tools)

    assert len(specs) == 1
    assert specs[0].name == "api.get_item"


# ─── Test reconnection ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_triggers_reconnect_on_new_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call() should reconnect when event loop changes."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Simulate connected on different loop
    node.connected = True
    node.connected_loop = MagicMock()  # Different from current loop

    # Mock reconnection
    reconnect_called = False

    async def mock_force_reconnect() -> None:
        nonlocal reconnect_called
        reconnect_called = True
        node.connected = True
        node.connected_loop = asyncio.get_running_loop()
        node.mcp_client = MagicMock()
        node.mcp_client.call_tool = AsyncMock(return_value={"ok": True})
        node.tool_name_map["test.ping"] = "ping"

    monkeypatch.setattr(node, "force_reconnect", mock_force_reconnect)
    node.tool_name_map["test.ping"] = "ping"

    await node.call("test.ping", {}, FakeToolContext())
    assert reconnect_called


# ─── Test MCP result serialization edge cases ────────────────────────────────


def test_serialize_mcp_result_content_with_many_texts() -> None:
    """Content with multiple text blocks should return list."""

    class FakeTextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    fake_result = SimpleNamespace(
        structuredContent=None,
        content=[
            FakeTextBlock("one"),
            FakeTextBlock("two"),
            FakeTextBlock("three"),
        ],
    )

    result = _serialize_mcp_result(fake_result)
    assert result == ["one", "two", "three"]
