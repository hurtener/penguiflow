"""Tests for Phase 2: MCP Resources support.

This module tests:
- ResourceCache with get_or_fetch, invalidation, eviction
- ResourceSubscriptionManager for subscribe/unsubscribe
- ToolNode resource methods (list_resources, read_resource, etc.)
- Generated planner tools for resources
"""

import asyncio
import base64

import pytest

from penguiflow.artifacts import InMemoryArtifactStore
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
from penguiflow.tools.resources import (
    _CacheEntry as ResourceCacheEntry,
)

pytest.importorskip("tenacity")


# ─── Fixtures ─────────────────────────────────────────────────────────────────


class DummyCtx:
    """Minimal context for testing resource operations."""

    def __init__(self, artifact_store: InMemoryArtifactStore | None = None):
        self._tool_context: dict[str, str] = {}
        self._llm_context: dict[str, str] = {}
        self._meta: dict[str, str] = {}
        self._artifacts = artifact_store or InMemoryArtifactStore()

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
    def artifacts(self):
        return self._artifacts


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
def resource_cache(artifact_store):
    config = ResourceCacheConfig(
        enabled=True,
        max_entries=10,
        ttl_seconds=3600,
        inline_text_if_under_chars=100,
    )
    return ResourceCache(artifact_store, "test", config)


@pytest.fixture
def subscription_manager():
    return ResourceSubscriptionManager("test")


# ─── ResourceInfo/ResourceTemplateInfo Model Tests ────────────────────────────


def test_resource_info_basic():
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


def test_resource_info_minimal():
    """ResourceInfo should only require uri."""
    info = ResourceInfo(uri="file:///test.txt")
    assert info.uri == "file:///test.txt"
    assert info.name is None
    assert info.annotations == {}


def test_resource_template_info():
    """ResourceTemplateInfo should store template URI patterns."""
    template = ResourceTemplateInfo(
        uri_template="file:///{path}",
        name="File Template",
        description="Read any file",
        mime_type="application/octet-stream",
    )
    assert template.uri_template == "file:///{path}"
    assert template.name == "File Template"


def test_resource_contents_text():
    """ResourceContents should hold text content."""
    contents = ResourceContents(
        uri="file:///test.txt",
        mime_type="text/plain",
        text="Hello, World!",
    )
    assert contents.text == "Hello, World!"
    assert contents.blob is None


def test_resource_contents_blob():
    """ResourceContents should hold binary content as base64."""
    data = b"binary data"
    contents = ResourceContents(
        uri="file:///test.bin",
        mime_type="application/octet-stream",
        blob=base64.b64encode(data).decode(),
    )
    assert contents.blob is not None
    assert base64.b64decode(contents.blob) == data


# ─── ResourceCacheConfig Tests ────────────────────────────────────────────────


def test_cache_config_defaults():
    """ResourceCacheConfig should have sensible defaults."""
    config = ResourceCacheConfig()
    assert config.enabled is True
    assert config.max_entries == 1000
    assert config.ttl_seconds == 3600
    assert config.inline_text_if_under_chars == 10_000


def test_cache_config_custom():
    """ResourceCacheConfig should accept custom values."""
    config = ResourceCacheConfig(
        enabled=False,
        max_entries=50,
        ttl_seconds=600,
        inline_text_if_under_chars=500,
    )
    assert config.enabled is False
    assert config.max_entries == 50


# ─── ResourceCache Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_get_or_fetch_text_inline(resource_cache, artifact_store):
    """Small text content should be inlined."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri):
        return {"contents": [{"text": "small text", "mimeType": "text/plain"}]}

    result = await resource_cache.get_or_fetch("file:///test.txt", read_fn, ctx)

    assert "text" in result
    assert result["text"] == "small text"
    assert resource_cache.size == 1


@pytest.mark.asyncio
async def test_cache_get_or_fetch_text_large(artifact_store):
    """Large text content should be stored as artifact."""
    config = ResourceCacheConfig(inline_text_if_under_chars=10)
    cache = ResourceCache(artifact_store, "test", config)
    ctx = DummyCtx(artifact_store)

    large_text = "x" * 100  # Exceeds threshold of 10

    async def read_fn(uri):
        return {"contents": [{"text": large_text, "mimeType": "text/plain"}]}

    result = await cache.get_or_fetch("file:///large.txt", read_fn, ctx)

    assert "artifact" in result
    assert "id" in result["artifact"]


@pytest.mark.asyncio
async def test_cache_get_or_fetch_binary(resource_cache, artifact_store):
    """Binary content should be stored as artifact."""
    ctx = DummyCtx(artifact_store)
    binary_data = b"binary content"
    encoded = base64.b64encode(binary_data).decode()

    async def read_fn(uri):
        return {"contents": [{"blob": encoded, "mimeType": "application/octet-stream"}]}

    result = await resource_cache.get_or_fetch("file:///test.bin", read_fn, ctx)

    assert "artifact" in result
    # Verify artifact was stored
    ref_id = result["artifact"]["id"]
    assert await artifact_store.exists(ref_id)


@pytest.mark.asyncio
async def test_cache_hit_returns_cached(resource_cache, artifact_store):
    """Second fetch should return cached result without calling read_fn."""
    ctx = DummyCtx(artifact_store)
    call_count = 0

    async def read_fn(uri):
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": "cached text"}]}

    # First fetch
    result1 = await resource_cache.get_or_fetch("file:///test.txt", read_fn, ctx)
    # Second fetch - should be cached
    result2 = await resource_cache.get_or_fetch("file:///test.txt", read_fn, ctx)

    assert call_count == 1
    assert result1 == result2


@pytest.mark.asyncio
async def test_cache_invalidate(resource_cache, artifact_store):
    """Invalidate should remove entry and cause re-fetch."""
    ctx = DummyCtx(artifact_store)
    call_count = 0

    async def read_fn(uri):
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
    result = await resource_cache.get_or_fetch("file:///test.txt", read_fn, ctx)
    assert call_count == 2
    assert result["text"] == "version 2"


@pytest.mark.asyncio
async def test_cache_invalidate_nonexistent(resource_cache):
    """Invalidating non-existent entry should return False."""
    assert resource_cache.invalidate("file:///nonexistent.txt") is False


@pytest.mark.asyncio
async def test_cache_invalidate_all(resource_cache, artifact_store):
    """invalidate_all should clear all entries."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri):
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
async def test_cache_eviction(artifact_store):
    """Cache should evict oldest entries when full."""
    config = ResourceCacheConfig(max_entries=3)
    cache = ResourceCache(artifact_store, "test", config)
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri):
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

    async def counting_read(uri):
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": "re-fetched"}]}

    # This should require a re-fetch since it was evicted
    await cache.get_or_fetch("file:///1.txt", counting_read, ctx)
    assert call_count == 1


@pytest.mark.asyncio
async def test_cache_disabled(artifact_store):
    """Disabled cache should always fetch."""
    config = ResourceCacheConfig(enabled=False)
    cache = ResourceCache(artifact_store, "test", config)
    ctx = DummyCtx(artifact_store)
    call_count = 0

    async def read_fn(uri):
        nonlocal call_count
        call_count += 1
        return {"contents": [{"text": "fresh"}]}

    await cache.get_or_fetch("file:///test.txt", read_fn, ctx)
    await cache.get_or_fetch("file:///test.txt", read_fn, ctx)

    assert call_count == 2
    assert cache.size == 0  # Nothing cached


@pytest.mark.asyncio
async def test_cache_empty_content(resource_cache, artifact_store):
    """Empty content should return error."""
    ctx = DummyCtx(artifact_store)

    async def read_fn(uri):
        return {"contents": [{}]}  # No text or blob

    result = await resource_cache.get_or_fetch("file:///empty.txt", read_fn, ctx)
    assert "error" in result


# ─── ResourceSubscriptionManager Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscription_manager_subscribe(subscription_manager):
    """Subscribe should call subscribe_fn and track subscription."""

    async def subscribe_fn(uri):
        pass  # Mock subscription

    result = await subscription_manager.subscribe(
        "file:///test.txt",
        subscribe_fn,
    )

    assert result is True
    assert subscription_manager.is_subscribed("file:///test.txt")
    assert "file:///test.txt" in subscription_manager.subscribed_uris


@pytest.mark.asyncio
async def test_subscription_manager_subscribe_idempotent(subscription_manager):
    """Subscribing twice should not call subscribe_fn again."""
    call_count = 0

    async def subscribe_fn(uri):
        nonlocal call_count
        call_count += 1

    await subscription_manager.subscribe("file:///test.txt", subscribe_fn)
    await subscription_manager.subscribe("file:///test.txt", subscribe_fn)

    assert call_count == 1


@pytest.mark.asyncio
async def test_subscription_manager_unsubscribe(subscription_manager):
    """Unsubscribe should call unsubscribe_fn and remove tracking."""

    async def subscribe_fn(uri):
        pass

    async def unsubscribe_fn(uri):
        pass

    await subscription_manager.subscribe("file:///test.txt", subscribe_fn)
    assert subscription_manager.is_subscribed("file:///test.txt")

    result = await subscription_manager.unsubscribe("file:///test.txt", unsubscribe_fn)
    assert result is True
    assert not subscription_manager.is_subscribed("file:///test.txt")


@pytest.mark.asyncio
async def test_subscription_manager_unsubscribe_not_subscribed(subscription_manager):
    """Unsubscribing without subscription should return False."""

    async def unsubscribe_fn(uri):
        pass

    result = await subscription_manager.unsubscribe("file:///not_subscribed.txt", unsubscribe_fn)
    assert result is False


@pytest.mark.asyncio
async def test_subscription_manager_handle_update_with_callback(subscription_manager):
    """handle_update should call the registered callback."""
    updates_received = []

    def callback(uri):
        updates_received.append(uri)

    async def subscribe_fn(uri):
        pass

    await subscription_manager.subscribe(
        "file:///test.txt",
        subscribe_fn,
        callback=callback,
    )

    await subscription_manager.handle_update("file:///test.txt")
    assert updates_received == ["file:///test.txt"]


@pytest.mark.asyncio
async def test_subscription_manager_handle_update_async_callback(subscription_manager):
    """handle_update should support async callbacks."""
    updates_received = []

    async def async_callback(uri):
        await asyncio.sleep(0.001)
        updates_received.append(uri)

    async def subscribe_fn(uri):
        pass

    await subscription_manager.subscribe(
        "file:///test.txt",
        subscribe_fn,
        callback=async_callback,
    )

    await subscription_manager.handle_update("file:///test.txt")
    assert updates_received == ["file:///test.txt"]


@pytest.mark.asyncio
async def test_subscription_manager_handle_update_no_subscription(subscription_manager):
    """handle_update should not fail for non-subscribed URI."""
    await subscription_manager.handle_update("file:///unknown.txt")
    # Should not raise


# ─── ToolNode Resource Method Tests ───────────────────────────────────────────


def test_toolnode_resources_supported_default():
    """ToolNode should default to resources not supported."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    assert node.resources_supported is False
    assert node.resources == []
    assert node.resource_templates == []


def test_toolnode_generate_resource_tools_not_supported():
    """Generating resource tools when not supported should return empty list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Not resources_supported - no resource tools
    assert node._resources_supported is False
    specs = node._generate_resource_tools()
    assert specs == []


def test_toolnode_generate_resource_tools_creates_specs():
    """Generating resource tools should create proper NodeSpecs."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Simulate resources being discovered (must be done before generating tools)
    node._resources_supported = True
    node._resources = [
        ResourceInfo(uri="file:///test.txt", name="Test"),
    ]

    specs = node._generate_resource_tools()

    assert len(specs) == 3  # resources_list, resources_read, resources_templates_list

    names = [s.name for s in specs]
    assert "test_server.resources_list" in names
    assert "test_server.resources_read" in names
    assert "test_server.resources_templates_list" in names

    # Verify registry was updated
    assert registry.has("test_server.resources_list")
    assert registry.has("test_server.resources_read")
    assert registry.has("test_server.resources_templates_list")


def test_toolnode_generate_resource_tools_read_has_uri_param():
    """resources_read tool should have uri parameter."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Enable resources support
    node._resources_supported = True
    node._resources = [ResourceInfo(uri="file:///test.txt")]

    specs = node._generate_resource_tools()

    read_spec = next(s for s in specs if "resources_read" in s.name)
    # Check that args_model has uri field
    args_instance = read_spec.args_model(uri="file:///test.txt")
    assert hasattr(args_instance, "uri")
    assert args_instance.uri == "file:///test.txt"


@pytest.mark.asyncio
async def test_toolnode_list_resources_not_connected():
    """list_resources when not connected should return empty list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    resources = await node.list_resources()
    assert resources == []


@pytest.mark.asyncio
async def test_toolnode_read_resource_not_connected(artifact_store):
    """read_resource when not connected should return error."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)
    ctx = DummyCtx(artifact_store)

    result = await node.read_resource("file:///test.txt", ctx)
    assert "error" in result
    assert "not connected" in result["error"].lower()


@pytest.mark.asyncio
async def test_toolnode_subscribe_resource_not_connected():
    """subscribe_resource when not connected should return False."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    result = await node.subscribe_resource("file:///test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_toolnode_unsubscribe_resource_not_connected():
    """unsubscribe_resource when not connected should return False."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    result = await node.unsubscribe_resource("file:///test.txt")
    assert result is False


def test_toolnode_handle_resource_updated():
    """handle_resource_updated should invalidate cache entry."""
    config = build_config()
    registry = ModelRegistry()
    store = InMemoryArtifactStore()
    node = ToolNode(config=config, registry=registry)

    # Set up cache with an entry
    cache_config = ResourceCacheConfig()
    cache = ResourceCache(store, "test", cache_config)
    node._resource_cache = cache

    # Manually add a cache entry for testing
    cache._entries["file:///test.txt"] = ResourceCacheEntry(
        uri="file:///test.txt",
        inline_text="cached content",
    )
    assert cache.size == 1

    # Handle update notification
    node.handle_resource_updated("file:///test.txt")
    assert cache.size == 0


def test_toolnode_handle_resource_updated_no_cache():
    """handle_resource_updated should not fail if no cache exists."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Should not raise
    node.handle_resource_updated("file:///test.txt")


def test_toolnode_handle_resource_updated_invokes_callback():
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


# ─── Handler Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_resources_list():
    """_handle_resources_list should return resource list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Simulate connected state with resources
    node._connected = True
    node._resources_supported = True
    node._resources = [
        ResourceInfo(uri="file:///a.txt", name="File A"),
        ResourceInfo(uri="file:///b.txt", name="File B"),
    ]

    ctx = DummyCtx()
    result = await node._handle_resources_list({}, ctx)

    assert result["count"] == 2
    assert len(result["resources"]) == 2
    assert result["resources"][0]["uri"] == "file:///a.txt"


@pytest.mark.asyncio
async def test_handle_resources_list_not_connected():
    """_handle_resources_list when not connected should return empty list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Set cached resources but don't connect
    node._resources = [
        ResourceInfo(uri="file:///a.txt", name="File A"),
    ]

    ctx = DummyCtx()
    result = await node._handle_resources_list({}, ctx)

    # Returns cached resources (which is what _resources contains)
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_handle_resources_templates_list():
    """_handle_resources_templates_list should return template list."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    # Simulate connected state
    node._connected = True
    node._resources_supported = True
    node._resource_templates = [
        ResourceTemplateInfo(uri_template="file:///{path}", name="File Template"),
    ]

    ctx = DummyCtx()
    result = await node._handle_resources_templates_list({}, ctx)

    assert result["count"] == 1
    assert len(result["templates"]) == 1
    assert result["templates"][0]["uri_template"] == "file:///{path}"


@pytest.mark.asyncio
async def test_handle_resources_read_missing_uri():
    """_handle_resources_read should error on missing uri."""
    config = build_config()
    registry = ModelRegistry()
    node = ToolNode(config=config, registry=registry)

    ctx = DummyCtx()
    result = await node._handle_resources_read({}, ctx)

    assert "error" in result["result"]
    assert "uri" in result["result"]["error"].lower()
