"""Tests for Phase 3: MCP Prompts support.

This module tests:
- PromptInfo and PromptArgumentInfo models
- PromptsConfig model
- ToolNode prompt discovery, generated tools, and handlers
- Prompt content serialization
"""

from __future__ import annotations

import asyncio
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import mcp.types as mcp_types
import pytest
from pydantic import AnyUrl

from penguiflow.artifacts import (
    InMemoryArtifactStore,
)
from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import (
    ExternalToolConfig,
    PromptsConfig,
    TransportType,
)
from penguiflow.tools.node import ToolNode
from penguiflow.tools.prompts import (
    PromptArgumentInfo,
    PromptInfo,
    serialize_prompt_messages,
)

from .tool_context_doubles import FakeToolContext

pytest.importorskip("tenacity")


# ─── Fixtures ────────────────────────────────────────────────────────────────


class DummyCtx(FakeToolContext):
    """Tool context for ToolNode tests."""

    def __init__(
        self,
        artifact_store: InMemoryArtifactStore | None = None,
    ) -> None:
        """Store artifacts in ``artifact_store`` when given."""
        super().__init__(artifact_store=artifact_store)


def build_config(**overrides: object) -> ExternalToolConfig:
    """Build an MCP tool config, with ``overrides`` applied."""
    base: dict[str, object] = {
        "name": "test_server",
        "transport": TransportType.MCP,
        "connection": "npx -y @test/server",
    }
    base.update(overrides)
    return ExternalToolConfig.model_validate(base)


@pytest.fixture
def artifact_store() -> InMemoryArtifactStore:
    """Provide an empty in-memory artifact store."""
    return InMemoryArtifactStore()


@pytest.fixture
def registry() -> ModelRegistry:
    """Provide an empty model registry."""
    return ModelRegistry()


# ─── PromptInfo / PromptArgumentInfo Model Tests ─────────────────────────────


def test_prompt_info_basic() -> None:
    """PromptInfo should accept basic fields."""
    info = PromptInfo(
        name="summarize",
        description="Summarize a text",
        arguments=[
            PromptArgumentInfo(
                name="text", description="Text to summarize", required=True
            ),
            PromptArgumentInfo(name="length", description="Target length"),
        ],
    )
    assert info.name == "summarize"
    assert info.description == "Summarize a text"
    assert len(info.arguments) == 2
    assert info.arguments[0].name == "text"
    assert info.arguments[0].required is True
    assert info.arguments[1].required is False


def test_prompt_info_minimal() -> None:
    """PromptInfo should only require name."""
    info = PromptInfo(name="test")
    assert info.name == "test"
    assert info.description is None
    assert info.arguments == []


def test_prompt_argument_info_defaults() -> None:
    """PromptArgumentInfo should have sensible defaults."""
    arg = PromptArgumentInfo(name="input")
    assert arg.name == "input"
    assert arg.description is None
    assert arg.required is False


def test_prompt_info_serialization() -> None:
    """PromptInfo should round-trip through model_dump/model_validate."""
    info = PromptInfo(
        name="code_review",
        description="Review code",
        arguments=[PromptArgumentInfo(name="code", required=True)],
    )
    dumped = info.model_dump()
    restored = PromptInfo.model_validate(dumped)
    assert restored.name == info.name
    assert restored.arguments[0].name == "code"
    assert restored.arguments[0].required is True


# ─── PromptsConfig Tests ─────────────────────────────────────────────────────


def test_prompts_config_defaults() -> None:
    """PromptsConfig should have sensible defaults."""
    config = PromptsConfig()
    assert config.enabled is True
    assert config.generate_tools is True


def test_prompts_config_disabled() -> None:
    """PromptsConfig should support disabling."""
    config = PromptsConfig(enabled=False, generate_tools=False)
    assert config.enabled is False
    assert config.generate_tools is False


def test_prompts_config_in_external_tool_config() -> None:
    """ExternalToolConfig should include prompts config."""
    cfg = build_config()
    assert cfg.prompts.enabled is True
    assert cfg.prompts.generate_tools is True


# ─── Content Serialization Tests ─────────────────────────────────────────────


_TEXT = mcp_types.TextContent(type="text", text="Hello, world!")
_IMAGE = mcp_types.ImageContent(
    type="image", data="iVBORw0KGgo=", mimeType="image/png"
)
_AUDIO = mcp_types.AudioContent(
    type="audio", data="UklGRg==", mimeType="audio/wav"
)
_RESOURCE = mcp_types.EmbeddedResource(
    type="resource",
    resource=mcp_types.TextResourceContents(
        uri=AnyUrl("file:///test.txt"),
        mimeType="text/plain",
        text="file content",
    ),
)


def _message(
    role: Literal["user", "assistant"],
    content: mcp_types.TextContent
    | mcp_types.ImageContent
    | mcp_types.AudioContent
    | mcp_types.EmbeddedResource,
) -> mcp_types.PromptMessage:
    """Build a prompt message from ``role`` with ``content``."""
    return mcp_types.PromptMessage(role=role, content=content)


def test_serialize_text_message() -> None:
    """Text content should serialize correctly."""
    messages = [_message("user", _TEXT)]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "Hello, world!"


def test_serialize_image_message() -> None:
    """Image content should serialize correctly."""
    messages = [_message("assistant", _IMAGE)]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["type"] == "image"
    assert result[0]["data"] == "iVBORw0KGgo="


def test_serialize_audio_message() -> None:
    """Audio content should serialize correctly."""
    messages = [_message("user", _AUDIO)]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["type"] == "audio"
    assert result[0]["mimeType"] == "audio/wav"


def test_serialize_resource_message() -> None:
    """Embedded resource content should serialize correctly."""
    messages = [_message("assistant", _RESOURCE)]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["type"] == "resource"
    assert result[0]["resource"]["uri"] == "file:///test.txt"


def test_serialize_multiple_messages() -> None:
    """Multiple messages should all be serialized."""
    messages = [
        _message("user", _TEXT),
        _message("assistant", _TEXT),
    ]
    result = serialize_prompt_messages(messages)
    assert len(result) == 2


def test_serialize_empty_messages() -> None:
    """Empty message list should return empty list."""
    result = serialize_prompt_messages([])
    assert result == []


# ─── ToolNode Prompt Discovery Tests ─────────────────────────────────────────


class MockMCPPrompt:
    """Mock MCP Prompt object from list_prompts()."""

    def __init__(
        self,
        name: str,
        description: str | None = None,
        arguments: list[MockMCPPromptArg] | None = None,
    ) -> None:
        """Store ``name``, ``description`` and ``arguments``."""
        self.name = name
        self.description = description
        self.arguments = arguments or []


class MockMCPPromptArg:
    """Mock MCP PromptArgument."""

    def __init__(
        self, name: str, description: str | None = None, required: bool = False
    ) -> None:
        """Store ``name``, ``description`` and ``required``."""
        self.name = name
        self.description = description
        self.required = required


class MockMCPTool:
    """Mock MCP tool."""

    def __init__(self, name: str, description: str = "") -> None:
        """Store ``name`` and ``description``."""
        self.name = name
        self.description = description
        self.inputSchema = {"type": "object", "properties": {}}
        self.meta = None


@pytest.mark.asyncio
async def test_discover_prompts_success(registry: ModelRegistry) -> None:
    """ToolNode should discover prompts from MCP server."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Set up mock MCP client
    mock_client = AsyncMock()
    mock_client.list_tools = AsyncMock(
        return_value=[
            MockMCPTool("search", "Search tool"),
        ]
    )
    mock_client.list_prompts = AsyncMock(
        return_value=[
            MockMCPPrompt(
                "summarize",
                "Summarize text",
                [MockMCPPromptArg("text", "The text", True)],
            ),
            MockMCPPrompt("greet", "Generate greeting"),
        ]
    )
    mock_client.list_resources = AsyncMock(return_value=[])
    mock_client.list_resource_templates = AsyncMock(return_value=[])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    # Simulate connect
    node.mcp_client = mock_client
    node.connected = True
    node.connected_loop = asyncio.get_running_loop()
    node.tools = node.convert_mcp_tools([MockMCPTool("search")])
    await node.discover_mcp_resources()
    await node.discover_mcp_prompts()

    assert node.prompts_supported is True
    assert len(node.prompts) == 2
    assert node.prompts[0].name == "summarize"
    assert node.prompts[0].arguments[0].required is True
    assert node.prompts[1].name == "greet"


@pytest.mark.asyncio
async def test_discover_prompts_not_supported(registry: ModelRegistry) -> None:
    """ToolNode should handle servers that don't support prompts."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(
        side_effect=Exception("Not supported")
    )

    node.mcp_client = mock_client
    await node.discover_mcp_prompts()

    assert node.prompts_supported is False
    assert node.prompts == []


@pytest.mark.asyncio
async def test_discover_prompts_disabled(registry: ModelRegistry) -> None:
    """ToolNode should skip prompt discovery when disabled."""
    config = build_config()
    config.prompts.enabled = False
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(return_value=[])

    node.mcp_client = mock_client
    await node.discover_mcp_prompts()

    # list_prompts should not have been called
    mock_client.list_prompts.assert_not_called()
    assert node.prompts_supported is False


@pytest.mark.asyncio
async def test_generate_prompt_tools(registry: ModelRegistry) -> None:
    """ToolNode should generate prompt tools when prompts are discovered."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Simulate prompts discovered
    node.prompts = [
        PromptInfo(name="summarize", description="Summarize"),
        PromptInfo(name="translate", description="Translate"),
    ]
    node.prompts_supported = True

    tools = node.generate_prompt_tools()

    assert len(tools) == 2
    names = [t.name for t in tools]
    assert "test_server.prompts_list" in names
    assert "test_server.prompts_get" in names


@pytest.mark.asyncio
async def test_handle_prompts_list(
    registry: ModelRegistry, artifact_store: InMemoryArtifactStore
) -> None:
    """prompts_list handler should return prompt info."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    node.prompts = [
        PromptInfo(name="summarize", description="Summarize"),
    ]
    node.prompts_supported = True
    node.connected = True
    node.connected_loop = asyncio.get_running_loop()

    ctx = DummyCtx(artifact_store)
    result = await node.handle_prompts_list(None, ctx)

    assert result["count"] == 1
    assert result["prompts"][0]["name"] == "summarize"


@pytest.mark.asyncio
async def test_handle_prompts_get(
    registry: ModelRegistry, artifact_store: InMemoryArtifactStore
) -> None:
    """prompts_get handler should call get_prompt and return messages."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Mock MCP client
    mock_result = MagicMock()
    mock_result.description = "A summary"
    mock_msg = _message("assistant", _TEXT)
    mock_result.messages = [mock_msg]

    mock_client = AsyncMock()
    mock_client.get_prompt = AsyncMock(return_value=mock_result)

    node.mcp_client = mock_client
    node.prompts_supported = True
    node.connected = True
    node.connected_loop = asyncio.get_running_loop()

    # Call handler
    args = MagicMock()
    args.name = "summarize"
    args.arguments = {"text": "Hello"}

    ctx = DummyCtx(artifact_store)
    result = await node.handle_prompts_get(args, ctx)

    assert result["description"] == "A summary"
    assert len(result["messages"]) == 1
    assert result["messages"][0]["text"] == "Hello, world!"


@pytest.mark.asyncio
async def test_list_prompts_refresh(registry: ModelRegistry) -> None:
    """list_prompts with refresh=True should re-fetch from server."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(
        return_value=[
            MockMCPPrompt("new_prompt", "New prompt"),
        ]
    )

    node.mcp_client = mock_client
    node.prompts_supported = True
    node.connected = True
    node.connected_loop = asyncio.get_running_loop()
    node.prompts = [PromptInfo(name="old")]

    result = await node.list_prompts(refresh=True)
    assert len(result) == 1
    assert result[0].name == "new_prompt"


def test_handle_prompts_changed(registry: ModelRegistry) -> None:
    """handle_prompts_changed should invalidate cache."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    node.prompts = [PromptInfo(name="test")]
    node.prompts_supported = True

    node.handle_prompts_changed()

    assert node.prompts == []
    assert node.prompts_supported is True
    assert node.prompts_stale is True


@pytest.mark.asyncio
async def test_list_prompts_refreshes_when_cache_is_stale(
    registry: ModelRegistry,
) -> None:
    """A prompts/list_changed invalidation should refresh on next access."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(
        return_value=[MockMCPPrompt("fresh", "Fresh prompt")]
    )

    node.mcp_client = mock_client
    node.connected = True
    node.prompts_supported = True
    node.prompts = [PromptInfo(name="stale")]
    node.handle_prompts_changed()

    refreshed = await node.list_prompts()
    assert len(refreshed) == 1
    assert refreshed[0].name == "fresh"
    assert node.prompts_stale is False


# ─── Helpers ─────────────────────────────────────────────────────────────────
