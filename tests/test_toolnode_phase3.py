"""Tests for Phase 3: MCP Prompts support.

This module tests:
- PromptInfo and PromptArgumentInfo models
- PromptsConfig model
- ToolNode prompt discovery, generated tools, and handlers
- Prompt content serialization
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.artifacts import InMemoryArtifactStore, ScopedArtifacts
from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import ExternalToolConfig, PromptsConfig, TransportType
from penguiflow.tools.node import ToolNode
from penguiflow.tools.prompts import (
    PromptArgumentInfo,
    PromptInfo,
    serialize_prompt_messages,
)

pytest.importorskip("tenacity")


# ─── Fixtures ─────────────────────────────────────────────────────────────────


class DummyCtx:
    """Minimal context for testing prompt operations."""

    def __init__(self, artifact_store=None):
        self._tool_context: dict[str, str] = {}
        self._llm_context: dict[str, str] = {}
        self._meta: dict[str, str] = {}
        self._artifacts_store = artifact_store or InMemoryArtifactStore()

    @property
    def tool_context(self):
        return self._tool_context

    @property
    def llm_context(self):
        return self._llm_context

    @property
    def meta(self):
        return self._meta

    @property
    def _artifacts(self):
        return self._artifacts_store

    @property
    def artifacts(self):
        return ScopedArtifacts(
            self._artifacts_store,
            tenant_id=None,
            user_id=None,
            session_id=None,
            trace_id=None,
        )


def build_config(**overrides):
    base = {
        "name": "test_server",
        "transport": TransportType.MCP,
        "connection": "npx -y @test/server",
    }
    base.update(overrides)
    return ExternalToolConfig(**base)


@pytest.fixture
def artifact_store():
    return InMemoryArtifactStore()


@pytest.fixture
def registry():
    return ModelRegistry()


# ─── PromptInfo / PromptArgumentInfo Model Tests ──────────────────────────────


def test_prompt_info_basic():
    """PromptInfo should accept basic fields."""
    info = PromptInfo(
        name="summarize",
        description="Summarize a text",
        arguments=[
            PromptArgumentInfo(name="text", description="Text to summarize", required=True),
            PromptArgumentInfo(name="length", description="Target length"),
        ],
    )
    assert info.name == "summarize"
    assert info.description == "Summarize a text"
    assert len(info.arguments) == 2
    assert info.arguments[0].name == "text"
    assert info.arguments[0].required is True
    assert info.arguments[1].required is False


def test_prompt_info_minimal():
    """PromptInfo should only require name."""
    info = PromptInfo(name="test")
    assert info.name == "test"
    assert info.description is None
    assert info.arguments == []


def test_prompt_argument_info_defaults():
    """PromptArgumentInfo should have sensible defaults."""
    arg = PromptArgumentInfo(name="input")
    assert arg.name == "input"
    assert arg.description is None
    assert arg.required is False


def test_prompt_info_serialization():
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


def test_prompts_config_defaults():
    """PromptsConfig should have sensible defaults."""
    config = PromptsConfig()
    assert config.enabled is True
    assert config.generate_tools is True


def test_prompts_config_disabled():
    """PromptsConfig should support disabling."""
    config = PromptsConfig(enabled=False, generate_tools=False)
    assert config.enabled is False
    assert config.generate_tools is False


def test_prompts_config_in_external_tool_config():
    """ExternalToolConfig should include prompts config."""
    cfg = build_config()
    assert cfg.prompts.enabled is True
    assert cfg.prompts.generate_tools is True


# ─── Content Serialization Tests ──────────────────────────────────────────────


class MockTextContent:
    type = "text"
    text = "Hello, world!"


class MockImageContent:
    type = "image"
    data = "iVBORw0KGgo="
    mimeType = "image/png"


class MockAudioContent:
    type = "audio"
    data = "UklGRg=="
    mimeType = "audio/wav"


class MockResourceContent:
    type = "resource"

    class resource:
        uri = "file:///test.txt"
        mimeType = "text/plain"
        text = "file content"
        blob = None


class MockPromptMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_serialize_text_message():
    """Text content should serialize correctly."""
    messages = [MockPromptMessage("user", MockTextContent())]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "Hello, world!"


def test_serialize_image_message():
    """Image content should serialize correctly."""
    messages = [MockPromptMessage("assistant", MockImageContent())]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["type"] == "image"
    assert result[0]["data"] == "iVBORw0KGgo="


def test_serialize_audio_message():
    """Audio content should serialize correctly."""
    messages = [MockPromptMessage("user", MockAudioContent())]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["type"] == "audio"
    assert result[0]["mimeType"] == "audio/wav"


def test_serialize_resource_message():
    """Embedded resource content should serialize correctly."""
    messages = [MockPromptMessage("assistant", MockResourceContent())]
    result = serialize_prompt_messages(messages)
    assert len(result) == 1
    assert result[0]["type"] == "resource"
    assert result[0]["resource"]["uri"] == "file:///test.txt"


def test_serialize_multiple_messages():
    """Multiple messages should all be serialized."""
    messages = [
        MockPromptMessage("user", MockTextContent()),
        MockPromptMessage("assistant", MockTextContent()),
    ]
    result = serialize_prompt_messages(messages)
    assert len(result) == 2


def test_serialize_empty_messages():
    """Empty message list should return empty list."""
    result = serialize_prompt_messages([])
    assert result == []


# ─── ToolNode Prompt Discovery Tests ──────────────────────────────────────────


class MockMCPPrompt:
    """Mock MCP Prompt object from list_prompts()."""

    def __init__(self, name, description=None, arguments=None):
        self.name = name
        self.description = description
        self.arguments = arguments or []


class MockMCPPromptArg:
    """Mock MCP PromptArgument."""

    def __init__(self, name, description=None, required=False):
        self.name = name
        self.description = description
        self.required = required


class MockMCPTool:
    """Mock MCP tool."""

    def __init__(self, name, description="", inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {"type": "object", "properties": {}}
        self.meta = None


@pytest.mark.asyncio
async def test_discover_prompts_success(registry, artifact_store):
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

    with _patch_mcp_connect(node, mock_client):
        pass

    # Simulate connect
    node._mcp_client = mock_client
    node._connected = True
    node._connected_loop = asyncio.get_running_loop()
    node._tools = node._convert_mcp_tools([MockMCPTool("search")])
    await node._discover_mcp_resources()
    await node._discover_mcp_prompts()

    assert node.prompts_supported is True
    assert len(node.prompts) == 2
    assert node.prompts[0].name == "summarize"
    assert node.prompts[0].arguments[0].required is True
    assert node.prompts[1].name == "greet"


@pytest.mark.asyncio
async def test_discover_prompts_not_supported(registry):
    """ToolNode should handle servers that don't support prompts."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(side_effect=Exception("Not supported"))

    node._mcp_client = mock_client
    await node._discover_mcp_prompts()

    assert node.prompts_supported is False
    assert node.prompts == []


@pytest.mark.asyncio
async def test_discover_prompts_disabled(registry):
    """ToolNode should skip prompt discovery when disabled."""
    config = build_config()
    config.prompts.enabled = False
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(return_value=[])

    node._mcp_client = mock_client
    await node._discover_mcp_prompts()

    # list_prompts should not have been called
    mock_client.list_prompts.assert_not_called()
    assert node.prompts_supported is False


@pytest.mark.asyncio
async def test_generate_prompt_tools(registry):
    """ToolNode should generate prompt tools when prompts are discovered."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Simulate prompts discovered
    node._prompts = [
        PromptInfo(name="summarize", description="Summarize"),
        PromptInfo(name="translate", description="Translate"),
    ]
    node._prompts_supported = True

    tools = node._generate_prompt_tools()

    assert len(tools) == 2
    names = [t.name for t in tools]
    assert "test_server.prompts_list" in names
    assert "test_server.prompts_get" in names


@pytest.mark.asyncio
async def test_handle_prompts_list(registry, artifact_store):
    """prompts_list handler should return prompt info."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    node._prompts = [
        PromptInfo(name="summarize", description="Summarize"),
    ]
    node._prompts_supported = True
    node._connected = True
    node._connected_loop = asyncio.get_running_loop()

    ctx = DummyCtx(artifact_store)
    result = await node._handle_prompts_list(None, ctx)

    assert result["count"] == 1
    assert result["prompts"][0]["name"] == "summarize"


@pytest.mark.asyncio
async def test_handle_prompts_get(registry, artifact_store):
    """prompts_get handler should call get_prompt and return messages."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Mock MCP client
    mock_result = MagicMock()
    mock_result.description = "A summary"
    mock_msg = MockPromptMessage("assistant", MockTextContent())
    mock_result.messages = [mock_msg]

    mock_client = AsyncMock()
    mock_client.get_prompt = AsyncMock(return_value=mock_result)

    node._mcp_client = mock_client
    node._prompts_supported = True
    node._connected = True
    node._connected_loop = asyncio.get_running_loop()

    # Call handler
    args = MagicMock()
    args.name = "summarize"
    args.arguments = {"text": "Hello"}

    ctx = DummyCtx(artifact_store)
    result = await node._handle_prompts_get(args, ctx)

    assert result["description"] == "A summary"
    assert len(result["messages"]) == 1
    assert result["messages"][0]["text"] == "Hello, world!"


@pytest.mark.asyncio
async def test_list_prompts_refresh(registry):
    """list_prompts with refresh=True should re-fetch from server."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(
        return_value=[
            MockMCPPrompt("new_prompt", "New prompt"),
        ]
    )

    node._mcp_client = mock_client
    node._prompts_supported = True
    node._connected = True
    node._connected_loop = asyncio.get_running_loop()
    node._prompts = [PromptInfo(name="old")]

    result = await node.list_prompts(refresh=True)
    assert len(result) == 1
    assert result[0].name == "new_prompt"


def test_handle_prompts_changed(registry):
    """handle_prompts_changed should invalidate cache."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    node._prompts = [PromptInfo(name="test")]
    node._prompts_supported = True

    node.handle_prompts_changed()

    assert node.prompts == []
    assert node.prompts_supported is True
    assert node._prompts_stale is True


@pytest.mark.asyncio
async def test_list_prompts_refreshes_when_cache_is_stale(registry):
    """A prompts/list_changed invalidation should refresh on next access."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.list_prompts = AsyncMock(return_value=[MockMCPPrompt("fresh", "Fresh prompt")])

    node._mcp_client = mock_client
    node._connected = True
    node._prompts_supported = True
    node._prompts = [PromptInfo(name="stale")]
    node.handle_prompts_changed()

    refreshed = await node.list_prompts()
    assert len(refreshed) == 1
    assert refreshed[0].name == "fresh"
    assert node._prompts_stale is False


# ─── Helpers ──────────────────────────────────────────────────────────────────


class _patch_mcp_connect:
    """Context manager that does nothing (placeholder for complex connect patching)."""

    def __init__(self, node, client):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
