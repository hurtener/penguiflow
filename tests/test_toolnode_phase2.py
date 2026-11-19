"""Tests for Phase 2: MCP Resources support.

This module tests:
- ResourceCache with get_or_fetch, invalidation, eviction
- ResourceSubscriptionManager for subscribe/unsubscribe
- ToolNode resource methods (list_resources, read_resource, etc.)
- Generated planner tools for resources
"""

from __future__ import annotations

import asyncio
import base64

import mcp.types as mcp_types
import pytest
from pydantic import AnyUrl

from penguiflow.artifacts import (
    InMemoryArtifactStore,
)
from penguiflow.registry import ModelRegistry
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.node import ToolNode
from penguiflow.tools.resources import (
    ResourceCache,
    ResourceCacheConfig,
    ResourceContents,
    ResourceInfo,
    ResourceSubscriptionManager,
    ResourceTemplateInfo,
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
def resource_cache() -> ResourceCache:
    """Provide a resource cache with small limits."""
    config = ResourceCacheConfig(
        enabled=True,
        max_entries=10,
        ttl_seconds=3600,
        inline_text_if_under_chars=100,
    )
    return ResourceCache("test", config)


@pytest.fixture
def subscription_manager() -> ResourceSubscriptionManager:
    """Provide a resource subscription manager."""
    return ResourceSubscriptionManager("test")


# ─── ResourceInfo/ResourceTemplateInfo Model Tests ───────────────────────────


def test_resource_info_basic() -> None:
    """ResourceInfo should accept basic fields."""
    info = ResourceInfo(
        uri="file:///test.txt",
        name="Test File",
        description="A test file",
        mime_type="text/plain",
        size_bytes=100,
    )
    assert info.uri == "file:///test.txt"
    assert info.name == "Test File"
    assert info.mime_type == "text/plain"


def test_resource_info_minimal() -> None:
    """ResourceInfo should only require uri."""
    info = ResourceInfo(uri="file:///test.txt")
    assert info.uri == "file:///test.txt"
    assert info.name is None
    assert info.annotations == {}


def test_resource_template_info() -> None:
    """ResourceTemplateInfo should store template URI patterns."""
    template = ResourceTemplateInfo(
        uri_template="file:///{path}",
        name="File Template",
        description="Read any file",
        mime_type="application/octet-stream",
    )
    assert template.uri_template == "file:///{path}"
    assert template.name == "File Template"


def test_resource_contents_text() -> None:
    """ResourceContents should hold text content."""
    contents = ResourceContents(
        uri="file:///test.txt",
        mime_type="text/plain",
        text="Hello, World!",
    )
    assert contents.text == "Hello, World!"
    assert contents.blob is None


def test_resource_contents_blob() -> None:
    """ResourceContents should hold binary content as base64."""
    data = b"binary data"
    contents = ResourceContents(
        uri="file:///test.bin",
        mime_type="application/octet-stream",
        blob=base64.b64encode(data).decode(),
    )
    assert contents.blob is not None
    assert base64.b64decode(contents.blob) == data


# ─── ResourceCacheConfig Tests ───────────────────────────────────────────────


def test_cache_config_defaults() -> None:
    """ResourceCacheConfig should have sensible defaults."""
    config = ResourceCacheConfig()
    assert config.enabled is True
    assert config.max_entries == 1000
    assert config.ttl_seconds == 3600
    assert config.inline_text_if_under_chars == 10_000


def test_cache_config_custom() -> None:
    """ResourceCacheConfig should accept custom values."""
    config = ResourceCacheConfig(
        enabled=False,
        max_entries=50,
        ttl_seconds=600,
        inline_text_if_under_chars=500,
    )
    assert config.enabled is False
    assert config.max_entries == 50


# ─── ResourceCache Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_get_or_fetch_text_inline(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """Small text content should be inlined."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        return {"contents": [{"text": "small text", "mimeType": "text/plain"}]}

    result = await resource_cache.get_or_fetch(
        "file:///test.txt", read_fn, ctx
    )

    assert "text" in result
    assert result["text"] == "small text"
    assert resource_cache.size == 1


@pytest.mark.asyncio
async def test_cache_get_or_fetch_text_inline_from_list_response(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """List-based read_resource responses should inline text correctly."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri: str) -> list[mcp_types.TextResourceContents]:
        return [
            mcp_types.TextResourceContents(
                uri=AnyUrl(uri), text="small text", mimeType="text/plain"
            )
        ]

    result = await resource_cache.get_or_fetch(
        "file:///test-list.txt", read_fn, ctx
    )

    assert result == {"text": "small text"}
    assert resource_cache.size == 1


@pytest.mark.asyncio
async def test_cache_get_or_fetch_text_large(
    artifact_store: InMemoryArtifactStore,
) -> None:
    """Large text content should be stored as artifact."""
    config = ResourceCacheConfig(inline_text_if_under_chars=10)
    cache = ResourceCache("test", config)
    ctx = DummyCtx(artifact_store)

    large_text = "x" * 100  # Exceeds threshold of 10

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        return {"contents": [{"text": large_text, "mimeType": "text/plain"}]}

    result = await cache.get_or_fetch("file:///large.txt", read_fn, ctx)

    assert "artifact" in result
    assert "id" in result["artifact"]


@pytest.mark.asyncio
async def test_cache_get_or_fetch_binary(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """Binary content should be stored as artifact."""
    ctx = DummyCtx(artifact_store)
    binary_data = b"binary content"
    encoded = base64.b64encode(binary_data).decode()

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        return {
            "contents": [
                {"blob": encoded, "mimeType": "application/octet-stream"}
            ]
        }

    result = await resource_cache.get_or_fetch(
        "file:///test.bin", read_fn, ctx
    )

    assert "artifact" in result
    # Verify artifact was stored
    ref_id = result["artifact"]["id"]
    assert await artifact_store.exists(ref_id)


@pytest.mark.asyncio
async def test_cache_hit_returns_cached(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """Second fetch should return cached result without calling read_fn."""
    ctx = DummyCtx(artifact_store)
    call_count = 0

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": "cached text"}]}

    # First fetch
    result1 = await resource_cache.get_or_fetch(
        "file:///test.txt", read_fn, ctx
    )
    # Second fetch - should be cached
    result2 = await resource_cache.get_or_fetch(
        "file:///test.txt", read_fn, ctx
    )

    assert call_count == 1
    assert result1 == result2


@pytest.mark.asyncio
async def test_cache_invalidate(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """Invalidate should remove entry and cause re-fetch."""
    ctx = DummyCtx(artifact_store)
    call_count = 0

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": f"version {call_count}"}]}

    # First fetch
    await resource_cache.get_or_fetch("file:///test.txt", read_fn, ctx)
    assert call_count == 1

    # Invalidate
    assert resource_cache.invalidate("file:///test.txt") is True
    assert resource_cache.size == 0

    # Fetch again - should call read_fn
    result = await resource_cache.get_or_fetch(
        "file:///test.txt", read_fn, ctx
    )
    assert call_count == 2
    assert result["text"] == "version 2"


@pytest.mark.asyncio
async def test_cache_invalidate_nonexistent(
    resource_cache: ResourceCache,
) -> None:
    """Invalidating non-existent entry should return False."""
    assert resource_cache.invalidate("file:///nonexistent.txt") is False


@pytest.mark.asyncio
async def test_cache_invalidate_all(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """invalidate_all should clear all entries."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri: str) -> dict[str, list[object]]:
        return {"contents": [{"text": f"content for {uri}"}]}

    # Populate cache with multiple entries
    await resource_cache.get_or_fetch("file:///a.txt", read_fn, ctx)
    await resource_cache.get_or_fetch("file:///b.txt", read_fn, ctx)
    await resource_cache.get_or_fetch("file:///c.txt", read_fn, ctx)

    assert resource_cache.size == 3

    count = resource_cache.invalidate_all()
    assert count == 3
    assert resource_cache.size == 0


@pytest.mark.asyncio
async def test_cache_eviction(artifact_store: InMemoryArtifactStore) -> None:
    """Cache should evict oldest entries when full."""
    config = ResourceCacheConfig(max_entries=3)
    cache = ResourceCache("test", config)
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri: str) -> dict[str, list[object]]:
        return {"contents": [{"text": f"content for {uri}"}]}

    # Fill cache
    await cache.get_or_fetch("file:///1.txt", read_fn, ctx)
    await cache.get_or_fetch("file:///2.txt", read_fn, ctx)
    await cache.get_or_fetch("file:///3.txt", read_fn, ctx)
    assert cache.size == 3

    # Add one more - should evict oldest
    await cache.get_or_fetch("file:///4.txt", read_fn, ctx)
    assert cache.size == 3  # Still at max

    # First entry should have been evicted
    call_count = 0

    async def counting_read(_uri: str) -> dict[str, list[object]]:
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": "re-fetched"}]}

    # This should require a re-fetch since it was evicted
    await cache.get_or_fetch("file:///1.txt", counting_read, ctx)
    assert call_count == 1


@pytest.mark.asyncio
async def test_cache_disabled(artifact_store: InMemoryArtifactStore) -> None:
    """Disabled cache should always fetch."""
    config = ResourceCacheConfig(enabled=False)
    cache = ResourceCache("test", config)
    ctx = DummyCtx(artifact_store)
    call_count = 0

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": "fresh"}]}

    await cache.get_or_fetch("file:///test.txt", read_fn, ctx)
    await cache.get_or_fetch("file:///test.txt", read_fn, ctx)

    assert call_count == 2
    assert cache.size == 0  # Nothing cached


@pytest.mark.asyncio
async def test_cache_empty_content(
    resource_cache: ResourceCache, artifact_store: InMemoryArtifactStore
) -> None:
    """Empty content should return error."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(_uri: str) -> dict[str, list[object]]:
        return {"contents": [{}]}  # No text or blob

    result = await resource_cache.get_or_fetch(
        "file:///empty.txt", read_fn, ctx
    )
    assert "error" in result


# ─── ResourceSubscriptionManager Tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_subscription_manager_subscribe(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """Subscribe should call subscribe_fn and track subscription."""

    async def subscribe_fn(uri: str) -> None:
        pass  # Mock subscription

    result = await subscription_manager.subscribe(
        "file:///test.txt",
        subscribe_fn,
    )

    assert result is True
    assert subscription_manager.is_subscribed("file:///test.txt")
    assert "file:///test.txt" in subscription_manager.subscribed_uris


@pytest.mark.asyncio
async def test_subscription_manager_subscribe_idempotent(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """Subscribing twice should not call subscribe_fn again."""
    call_count = 0

    async def subscribe_fn(_uri: str) -> None:
        nonlocal call_count
        call_count += 1

    await subscription_manager.subscribe("file:///test.txt", subscribe_fn)
    await subscription_manager.subscribe("file:///test.txt", subscribe_fn)

    assert call_count == 1


@pytest.mark.asyncio
async def test_subscription_manager_unsubscribe(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """Unsubscribe should call unsubscribe_fn and remove tracking."""

    async def subscribe_fn(uri: str) -> None:
        pass

    async def unsubscribe_fn(uri: str) -> None:
        pass

    await subscription_manager.subscribe("file:///test.txt", subscribe_fn)
    assert subscription_manager.is_subscribed("file:///test.txt")

    result = await subscription_manager.unsubscribe(
        "file:///test.txt", unsubscribe_fn
    )
    assert result is True
    assert not subscription_manager.is_subscribed("file:///test.txt")


@pytest.mark.asyncio
async def test_subscription_manager_unsubscribe_not_subscribed(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """Unsubscribing without subscription should return False."""

    async def unsubscribe_fn(uri: str) -> None:
        pass

    result = await subscription_manager.unsubscribe(
        "file:///not_subscribed.txt", unsubscribe_fn
    )
    assert result is False


@pytest.mark.asyncio
async def test_subscription_manager_handle_update_with_callback(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """handle_update should call the registered callback."""
    updates_received = []

    def callback(uri: str) -> None:
        updates_received.append(uri)

    async def subscribe_fn(uri: str) -> None:
        pass

    await subscription_manager.subscribe(
        "file:///test.txt",
        subscribe_fn,
        callback=callback,
    )

    await subscription_manager.handle_update("file:///test.txt")
    assert updates_received == ["file:///test.txt"]


@pytest.mark.asyncio
async def test_subscription_manager_handle_update_async_callback(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """handle_update should support async callbacks."""
    updates_received = []

    async def async_callback(uri: str) -> None:
        await asyncio.sleep(0.001)
        updates_received.append(uri)

    async def subscribe_fn(uri: str) -> None:
        pass

    await subscription_manager.subscribe(
        "file:///test.txt",
        subscribe_fn,
        callback=async_callback,
    )

    await subscription_manager.handle_update("file:///test.txt")
    assert updates_received == ["file:///test.txt"]


@pytest.mark.asyncio
async def test_subscription_manager_handle_update_no_subscription(
    subscription_manager: ResourceSubscriptionManager,
) -> None:
    """handle_update should not fail for non-subscribed URI."""
    await subscription_manager.handle_update("file:///unknown.txt")
    # Should not raise


# ─── ToolNode Resource Method Tests ──────────────────────────────────────────


def test_toolnode_resources_supported_default() -> None:
    """ToolNode should default to resources not supported."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    assert node.resources_supported is False
    assert node.resources == []
    assert node.resource_templates == []


def test_toolnode_generate_resource_tools_not_supported() -> None:
    """No resource tools are generated when unsupported."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Not resources_supported - no resource tools
    assert node.resources_supported is False
    specs = node.generate_resource_tools()
    assert specs == []


def test_toolnode_generate_resource_tools_creates_specs() -> None:
    """Generating resource tools should create proper NodeSpecs."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Simulate resources being discovered (must be done before generating
    # tools)
    node.resources_supported = True
    node.resources = [
        ResourceInfo(uri="file:///test.txt", name="Test"),
    ]

    specs = node.generate_resource_tools()

    assert (
        len(specs) == 3
    )  # resources_list, resources_read, resources_templates_list

    names = [s.name for s in specs]
    assert "test_server.resources_list" in names
    assert "test_server.resources_read" in names
    assert "test_server.resources_templates_list" in names

    # Verify registry was updated
    assert registry.has("test_server.resources_list")
    assert registry.has("test_server.resources_read")
    assert registry.has("test_server.resources_templates_list")


def test_toolnode_generate_resource_tools_read_has_uri_param() -> None:
    """resources_read tool should have uri parameter."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Enable resources support
    node.resources_supported = True
    node.resources = [ResourceInfo(uri="file:///test.txt")]

    specs = node.generate_resource_tools()

    read_spec = next(s for s in specs if "resources_read" in s.name)
    # Check that args_model has uri field
    args_instance = read_spec.args_model(uri="file:///test.txt")
    assert hasattr(args_instance, "uri")
    assert args_instance.uri == "file:///test.txt"


@pytest.mark.asyncio
async def test_toolnode_list_resources_not_connected() -> None:
    """list_resources when not connected should return empty list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    resources = await node.list_resources()
    assert resources == []


@pytest.mark.asyncio
async def test_toolnode_read_resource_not_connected(
    artifact_store: InMemoryArtifactStore,
) -> None:
    """read_resource when not connected should return error."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)
    ctx = DummyCtx(artifact_store)

    result = await node.read_resource("file:///test.txt", ctx)
    assert "error" in result
    assert "not connected" in result["error"].lower()


@pytest.mark.asyncio
async def test_toolnode_read_resource_list_shape_inline_text(
    artifact_store: InMemoryArtifactStore,
) -> None:
    """read_resource should support list-shaped MCP content responses."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    mock_client = type("MockClient", (), {})()

    async def _read_resource(
        uri: str,
    ) -> list[mcp_types.TextResourceContents]:
        return [
            mcp_types.TextResourceContents(
                uri=AnyUrl(uri),
                text="resource text from list",
                mimeType="text/plain",
            )
        ]

    mock_client.read_resource = _read_resource

    node.connected = True
    node.resources_supported = True
    node.mcp_client = mock_client

    ctx = DummyCtx(artifact_store)
    result = await node.read_resource(
        "pengui://docs/overview", ctx, use_cache=False
    )

    assert result == {"text": "resource text from list"}


@pytest.mark.asyncio
async def test_toolnode_subscribe_resource_not_connected() -> None:
    """subscribe_resource when not connected should return False."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    result = await node.subscribe_resource("file:///test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_toolnode_unsubscribe_resource_not_connected() -> None:
    """unsubscribe_resource when not connected should return False."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    result = await node.unsubscribe_resource("file:///test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_subscribe_resource_without_client_support() -> None:
    """subscribe_resource is False without client subscribe APIs."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)
    node.connected = True
    node.resources_supported = True
    node.mcp_client = object()

    result = await node.subscribe_resource("file:///test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_unsubscribe_resource_without_client_support() -> None:
    """unsubscribe_resource is False without client APIs."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)
    node.connected = True
    node.resources_supported = True
    node.mcp_client = object()

    result = await node.unsubscribe_resource("file:///test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_toolnode_handle_resource_updated(
    artifact_store: InMemoryArtifactStore,
) -> None:
    """handle_resource_updated should invalidate cache entry."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Set up cache with an entry
    cache_config = ResourceCacheConfig()
    cache = ResourceCache("test", cache_config)
    node.resource_cache = cache

    async def read_fn(uri: str) -> dict[str, object]:
        return {"contents": [{"uri": uri, "text": "cached content"}]}

    await cache.get_or_fetch(
        "file:///test.txt", read_fn, DummyCtx(artifact_store)
    )
    assert cache.size == 1

    # Handle update notification
    node.handle_resource_updated("file:///test.txt")
    assert cache.size == 0


def test_toolnode_handle_resource_updated_no_cache() -> None:
    """handle_resource_updated should not fail if no cache exists."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Should not raise
    node.handle_resource_updated("file:///test.txt")


def test_toolnode_handle_resource_updated_invokes_callback() -> None:
    """handle_resource_updated should call registered callback."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    seen: list[str] = []

    def _callback(uri: str) -> None:
        seen.append(uri)

    node.set_resource_updated_callback(_callback)
    node.handle_resource_updated("file:///callback.txt")

    assert seen == ["file:///callback.txt"]


# ─── Handler Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_resources_list() -> None:
    """_handle_resources_list should return resource list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Simulate connected state with resources
    node.connected = True
    node.resources_supported = True
    node.resources = [
        ResourceInfo(uri="file:///a.txt", name="File A"),
        ResourceInfo(uri="file:///b.txt", name="File B"),
    ]

    ctx = DummyCtx()
    result = await node.handle_resources_list({}, ctx)

    assert result["count"] == 2
    assert len(result["resources"]) == 2
    assert result["resources"][0]["uri"] == "file:///a.txt"


@pytest.mark.asyncio
async def test_handle_resources_list_not_connected() -> None:
    """_handle_resources_list when not connected should return empty list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Set cached resources but don't connect
    node.resources = [
        ResourceInfo(uri="file:///a.txt", name="File A"),
    ]

    ctx = DummyCtx()
    result = await node.handle_resources_list({}, ctx)

    # Returns cached resources (which is what _resources contains)
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_handle_resources_templates_list() -> None:
    """_handle_resources_templates_list should return template list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Simulate connected state
    node.connected = True
    node.resources_supported = True
    node.resource_templates = [
        ResourceTemplateInfo(
            uri_template="file:///{path}", name="File Template"
        ),
    ]

    ctx = DummyCtx()
    result = await node.handle_resources_templates_list({}, ctx)

    assert result["count"] == 1
    assert len(result["templates"]) == 1
    assert result["templates"][0]["uri_template"] == "file:///{path}"


@pytest.mark.asyncio
async def test_handle_resources_read_missing_uri() -> None:
    """_handle_resources_read should error on missing uri."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    ctx = DummyCtx()
    result = await node.handle_resources_read({}, ctx)

    assert "error" in result["result"]
    assert "uri" in result["result"]["error"].lower()
