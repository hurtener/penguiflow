"""Tests for MCP Apps support.

This module tests:
- AppMetadata, AppCSP, AppPermissions models
- AppsConfig model
- extract_app_metadata from tool objects
- ToolNode app metadata detection during tool discovery
- App HTML fetching and result enrichment
"""

from unittest.mock import AsyncMock

import pytest

from penguiflow.artifacts import ArtifactRef, InMemoryArtifactStore, ScopedArtifacts
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.registry import ModelRegistry
from penguiflow.tools.apps import (
    UI_MIME_TYPE,
    AppCSP,
    AppMetadata,
    AppPermissions,
    extract_app_metadata,
)
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.node import ToolNode

pytest.importorskip("tenacity")


# ─── Fixtures ─────────────────────────────────────────────────────────────────


class DummyCtx:
    """Minimal context for testing."""

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


# ─── AppMetadata / AppCSP / AppPermissions Model Tests ────────────────────────


def test_app_metadata_basic():
    """AppMetadata should accept all fields."""
    meta = AppMetadata(
        resource_uri="ui://test-server/view.html",
        visibility=["app", "model"],
        csp=AppCSP(connect_domains=["https://api.example.com"]),
        permissions=AppPermissions(camera=True),
        prefers_border=True,
    )
    assert meta.resource_uri == "ui://test-server/view.html"
    assert meta.permissions.camera is True
    assert meta.csp.connect_domains == ["https://api.example.com"]
    assert meta.prefers_border is True


def test_app_metadata_minimal():
    """AppMetadata should only require resource_uri."""
    meta = AppMetadata(resource_uri="ui://test/view.html")
    assert meta.resource_uri == "ui://test/view.html"
    assert meta.csp.connect_domains == []
    assert meta.permissions.camera is False
    assert meta.prefers_border is False


def test_app_csp_defaults():
    """AppCSP should have empty list defaults."""
    csp = AppCSP()
    assert csp.connect_domains == []
    assert csp.resource_domains == []
    assert csp.frame_domains == []


def test_app_permissions_defaults():
    """AppPermissions should default to all False."""
    perms = AppPermissions()
    assert perms.camera is False
    assert perms.microphone is False
    assert perms.clipboard_write is False


def test_apps_config_defaults():
    """AppsConfig should have sensible defaults."""
    cfg = build_config()
    assert cfg.apps.enabled is True
    assert cfg.apps.fetch_html_on_call is True


# ─── extract_app_metadata Tests ──────────────────────────────────────────────


class MockTool:
    """Mock MCP Tool with optional meta."""

    def __init__(self, name="test_tool", meta=None):
        self.name = name
        self.description = "Test tool"
        self.inputSchema = {"type": "object", "properties": {}}
        self.meta = meta


def test_extract_app_no_meta():
    """Should return None when tool has no meta."""
    tool = MockTool()
    assert extract_app_metadata(tool) is None


def test_extract_app_no_ui_key():
    """Should return None when meta has no 'ui' key."""
    tool = MockTool(meta={"fastmcp": {"tags": []}})
    assert extract_app_metadata(tool) is None


def test_extract_app_ui_true_only():
    """Should return None when ui=True but no resourceUri."""
    tool = MockTool(meta={"ui": True})
    assert extract_app_metadata(tool) is None


def test_extract_app_ui_dict_no_resource_uri():
    """Should return None when ui dict has no resourceUri."""
    tool = MockTool(meta={"ui": {"visibility": ["app"]}})
    assert extract_app_metadata(tool) is None


def test_extract_app_ui_with_resource_uri():
    """Should extract AppMetadata when ui dict has resourceUri."""
    tool = MockTool(
        meta={
            "ui": {
                "resourceUri": "ui://test-server/view.html",
                "visibility": ["app", "model"],
            }
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test-server/view.html"
    assert result.visibility == ["app", "model"]


def test_extract_app_ui_with_csp():
    """Should parse CSP from ui metadata."""
    tool = MockTool(
        meta={
            "ui": {
                "resourceUri": "ui://test/view.html",
                "csp": {
                    "connectDomains": ["https://api.example.com"],
                    "resourceDomains": ["https://cdn.example.com"],
                },
            }
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.csp.connect_domains == ["https://api.example.com"]
    assert result.csp.resource_domains == ["https://cdn.example.com"]


def test_extract_app_ui_with_permissions():
    """Should parse permissions from ui metadata."""
    tool = MockTool(
        meta={
            "ui": {
                "resourceUri": "ui://test/view.html",
                "permissions": {
                    "camera": {},
                    "clipboardWrite": {},
                },
            }
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.permissions.camera is True
    assert result.permissions.clipboard_write is True
    assert result.permissions.microphone is False


def test_extract_app_snake_case_keys():
    """Should handle snake_case keys as well as camelCase."""
    tool = MockTool(
        meta={
            "ui": {
                "resource_uri": "ui://test/view.html",
                "csp": {
                    "connect_domains": ["https://api.example.com"],
                },
                "permissions": {
                    "clipboard_write": {},
                },
                "prefers_border": True,
            }
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/view.html"
    assert result.csp.connect_domains == ["https://api.example.com"]
    assert result.permissions.clipboard_write is True
    assert result.prefers_border is True


def test_extract_app_from_dict():
    """Should work with dict-like tools (not just objects)."""
    tool = {"meta": {"ui": {"resourceUri": "ui://test/view.html"}}}
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/view.html"


def test_extract_app_from_flat_ui_keys():
    """Should parse FastMCP-style flat ui/* metadata keys."""
    tool = MockTool(meta={"ui/resourceUri": "ui://test/flat.html", "ui/prefersBorder": True})
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/flat.html"
    assert result.prefers_border is True


def test_extract_app_from_extension_id_key():
    """Should parse extension-id keyed metadata payload."""
    tool = MockTool(
        meta={
            "io.modelcontextprotocol/ui": {
                "resourceUri": "ui://test/ext.html",
                "visibility": ["app"],
            }
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/ext.html"
    assert result.visibility == ["app"]


# ─── ToolNode App Detection Tests ────────────────────────────────────────────


class MockMCPTool:
    """Mock MCP tool for ToolNode testing."""

    def __init__(self, name, description="", inputSchema=None, meta=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {"type": "object", "properties": {}}
        self.meta = meta


def test_convert_tools_detects_app_metadata(registry):
    """_convert_mcp_tools should detect app metadata in tool meta."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    tools = [
        MockMCPTool("regular_tool", "No app"),
        MockMCPTool(
            "app_tool",
            "Has app",
            meta={
                "ui": {"resourceUri": "ui://test/view.html"},
            },
        ),
    ]

    specs = node._convert_mcp_tools(tools)
    assert len(specs) == 2

    # Regular tool should not have app metadata
    regular = [s for s in specs if s.name == "test_server.regular_tool"][0]
    assert "has_app" not in regular.extra

    # App tool should have app metadata
    app = [s for s in specs if s.name == "test_server.app_tool"][0]
    assert app.extra.get("has_app") is True
    assert app.extra["app_metadata"]["resource_uri"] == "ui://test/view.html"

    # Should be in _app_metadata dict
    assert node.has_app("test_server.app_tool")
    assert not node.has_app("test_server.regular_tool")


def test_convert_tools_apps_disabled(registry):
    """App detection should be skipped when apps.enabled is False."""
    config = build_config()
    config.apps.enabled = False
    node = ToolNode(config=config, registry=registry)

    tools = [MockMCPTool("app_tool", meta={"ui": {"resourceUri": "ui://test/view.html"}})]
    specs = node._convert_mcp_tools(tools)

    assert len(specs) == 1
    assert "has_app" not in specs[0].extra
    assert not node.has_app("test_server.app_tool")


# ─── App HTML Fetching Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_app_html_success(registry):
    """_fetch_app_html should extract text from resource contents."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Mock content with text attribute
    class MockContent:
        text = "<html><body>Hello</body></html>"

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(return_value=[MockContent()])
    node._mcp_client = mock_client

    html = await node._fetch_app_html("ui://test/view.html")
    assert html is not None
    assert "<html>" in html


@pytest.mark.asyncio
async def test_fetch_app_html_failure(registry):
    """_fetch_app_html should return None on error."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(side_effect=Exception("Not found"))
    node._mcp_client = mock_client

    html = await node._fetch_app_html("ui://test/view.html")
    assert html is None


@pytest.mark.asyncio
async def test_fetch_app_html_no_client(registry):
    """_fetch_app_html should return None when no MCP client."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node._mcp_client = None

    html = await node._fetch_app_html("ui://test/view.html")
    assert html is None


# ─── Text Extraction Tests ───────────────────────────────────────────────────


def test_extract_text_from_list():
    """Should extract text from list of content items."""

    class Item:
        text = "hello"

    result = ToolNode._extract_text_from_resource([Item()])
    assert result == "hello"


def test_extract_text_from_dict():
    """Should extract text from dict."""
    result = ToolNode._extract_text_from_resource({"text": "world"})
    assert result == "world"


def test_extract_text_from_single_object():
    """Should extract text from single object."""

    class Obj:
        text = "content"

    result = ToolNode._extract_text_from_resource(Obj())
    assert result == "content"


def test_extract_text_no_text():
    """Should return None when no text found."""
    result = ToolNode._extract_text_from_resource({"binary": b"data"})
    assert result is None


# ─── Result Enrichment Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_with_app_html(registry, artifact_store):
    """_enrich_with_app_html should package result with app HTML artifact."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Mock resource fetch
    class MockContent:
        text = "<html><body>App UI</body></html>"

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(return_value=[MockContent()])
    node._mcp_client = mock_client

    app_meta = AppMetadata(
        resource_uri="ui://test/view.html",
        csp=AppCSP(connect_domains=["https://api.example.com"]),
        prefers_border=True,
    )

    ctx = DummyCtx(artifact_store)
    ctx.tool_context["session_id"] = "sess-123"
    original_result = {"data": "tool output"}

    enriched = await node._enrich_with_app_html(original_result, app_meta, ctx)

    assert isinstance(enriched, dict)
    assert "__mcp_app__" in enriched
    assert enriched["data"] == "tool output"  # Original data preserved
    assert enriched["__mcp_app__"]["resource_uri"] == "ui://test/view.html"
    assert enriched["__mcp_app__"]["prefers_border"] is True
    assert enriched["__mcp_app__"]["csp"]["connect_domains"] == ["https://api.example.com"]
    assert enriched["__mcp_app__"]["artifact_id"] is not None

    ref = await artifact_store.get_ref(enriched["__mcp_app__"]["artifact_id"])
    assert ref is not None
    assert ref.mime_type == UI_MIME_TYPE
    assert ref.source["namespace"] == "test_server"
    assert ref.source["session_id"] == "sess-123"
    assert ref.source["csp"]["connect_domains"] == ["https://api.example.com"]


@pytest.mark.asyncio
async def test_enrich_with_app_html_fetch_failure(registry, artifact_store):
    """Should return original result when HTML fetch fails."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(side_effect=Exception("Fail"))
    node._mcp_client = mock_client

    app_meta = AppMetadata(resource_uri="ui://test/view.html")
    ctx = DummyCtx(artifact_store)
    original_result = {"data": "tool output"}

    enriched = await node._enrich_with_app_html(original_result, app_meta, ctx)

    # Should return original result unchanged
    assert enriched == original_result
    assert "__mcp_app__" not in enriched


# ─── Public API Tests ────────────────────────────────────────────────────────


def test_app_tools_property(registry):
    """app_tools should return all app metadata."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    meta = AppMetadata(resource_uri="ui://test/view.html")
    node._app_metadata["test_server.tool1"] = meta

    tools = node.app_tools
    assert len(tools) == 1
    assert "test_server.tool1" in tools


def test_get_app_metadata(registry):
    """get_app_metadata should return metadata for known tools."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    meta = AppMetadata(resource_uri="ui://test/view.html")
    node._app_metadata["test_server.tool1"] = meta

    assert node.get_app_metadata("test_server.tool1") is not None
    assert node.get_app_metadata("test_server.unknown") is None


def test_artifact_registry_preserves_mcp_app_metadata():
    """MCP App binary records should retain structured renderer metadata."""
    ref = ArtifactRef(
        id="art_1",
        mime_type=UI_MIME_TYPE,
        source={
            "csp": {"connect_domains": ["https://api.example.com"]},
            "permissions": {"camera": True},
            "tool_data": {"hello": "world"},
            "prefers_border": True,
            "namespace": "test_ns",
            "session_id": "sess-1",
            "sandbox": "allow-scripts",
        },
    )
    registry = ArtifactRegistry()
    registry.register_binary_artifact(ref, source_tool="test_ns.tool", step_index=0)

    payload = registry.resolve_ref("art_1", session_id="sess-1")
    assert payload is not None
    assert payload["component"] == "mcp_app"
    assert payload["props"]["namespace"] == "test_ns"
    assert payload["props"]["session_id"] == "sess-1"
    assert payload["props"]["sandbox"] == "allow-scripts"
    assert payload["props"]["csp"]["connect_domains"] == ["https://api.example.com"]
