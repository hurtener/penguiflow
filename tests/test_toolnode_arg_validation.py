"""Tests for ToolNode arg validation."""

from __future__ import annotations

import mcp.types as mcp_types

from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.node import ToolNode


def test_toolnode_applies_arg_validation_defaults() -> None:
    """ToolNode applies arg validation defaults."""
    registry = ModelRegistry()
    config = ExternalToolConfig(
        name="ext",
        transport=TransportType.MCP,
        connection="echo",
    )
    node = ToolNode(config=config, registry=registry)
    tools = [
        mcp_types.Tool(
            name="search",
            description="search docs",
            inputSchema={
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    ]

    specs = node.convert_mcp_tools(tools)
    assert specs
    assert specs[0].extra.get("arg_validation") == config.arg_validation
