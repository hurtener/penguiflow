from __future__ import annotations

from dataclasses import dataclass

from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.node import ToolNode


@dataclass
class _FakeTool:
    name: str
    description: str
    inputSchema: dict


def test_toolnode_applies_arg_validation_defaults() -> None:
    registry = ModelRegistry()
    config = ExternalToolConfig(
        name="ext",
        transport=TransportType.MCP,
        connection="echo",
    )
    node = ToolNode(config=config, registry=registry)
    tools = [
        _FakeTool(
            name="search",
            description="search docs",
            inputSchema={
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    ]

    specs = node._convert_mcp_tools(tools)
    assert specs
    assert specs[0].extra.get("arg_validation") == config.arg_validation
