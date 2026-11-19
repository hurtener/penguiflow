"""Tests for MCP Apps support.

This module tests:
- AppMetadata, AppCSP, AppPermissions models
- AppsConfig model
- extract_app_metadata from tool objects
- ToolNode app metadata detection during tool discovery
- App HTML fetching and result enrichment
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import anyio
import mcp.types as mcp_types
import pytest
from mcp import ClientSession
from mcp.shared.message import MessageMetadata, SessionMessage
from typing_extensions import override

from penguiflow.artifacts import (
    ArtifactRef,
    InMemoryArtifactStore,
)
from penguiflow.planner.artifact_registry import ArtifactRegistry
from penguiflow.registry import ModelRegistry
from penguiflow.tools.apps import (
    UI_COMPAT_EXTENSION_IDS,
    UI_MIME_TYPE,
    AppCSP,
    AppMetadata,
    AppPermissions,
    extract_app_metadata,
)
from penguiflow.tools.config import ExternalToolConfig, TransportType
from penguiflow.tools.node import ToolNode, _extract_text_from_resource

from .tool_context_doubles import FakeToolContext

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping
    from datetime import timedelta

    from mcp.shared.context import RequestContext
    from mcp.shared.session import ProgressFnT, ReceiveResultT
    from mcp.types import InitializeResult

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


# ─── AppMetadata / AppCSP / AppPermissions Model Tests ───────────────────────


def test_app_metadata_basic() -> None:
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


def test_app_metadata_minimal() -> None:
    """AppMetadata should only require resource_uri."""
    meta = AppMetadata(resource_uri="ui://test/view.html")
    assert meta.resource_uri == "ui://test/view.html"
    assert meta.csp.connect_domains == []
    assert meta.permissions.camera is False
    assert meta.prefers_border is False


def test_app_csp_defaults() -> None:
    """AppCSP should have empty list defaults."""
    csp = AppCSP()
    assert csp.connect_domains == []
    assert csp.resource_domains == []
    assert csp.frame_domains == []


def test_app_permissions_defaults() -> None:
    """AppPermissions should default to all False."""
    perms = AppPermissions()
    assert perms.camera is False
    assert perms.microphone is False
    assert perms.clipboard_write is False


def test_apps_config_defaults() -> None:
    """AppsConfig should have sensible defaults."""
    cfg = build_config()
    assert cfg.apps.enabled is True
    assert cfg.apps.fetch_html_on_call is True


# ─── extract_app_metadata Tests ──────────────────────────────────────────────


class MockTool:
    """Mock MCP Tool with optional meta."""

    def __init__(
        self,
        name: str = "test_tool",
        meta: Mapping[str, object] | None = None,
    ) -> None:
        """Store ``name`` and ``meta``."""
        self.name = name
        self.description = "Test tool"
        self.inputSchema = {"type": "object", "properties": {}}
        self.meta = meta


def test_extract_app_no_meta() -> None:
    """Should return None when tool has no meta."""
    tool = MockTool()
    assert extract_app_metadata(tool) is None


def test_extract_app_no_ui_key() -> None:
    """Should return None when meta has no 'ui' key."""
    tool = MockTool(meta={"fastmcp": {"tags": []}})
    assert extract_app_metadata(tool) is None


def test_extract_app_ui_true_only() -> None:
    """Should return None when ui=True but no resourceUri."""
    tool = MockTool(meta={"ui": True})
    assert extract_app_metadata(tool) is None


def test_extract_app_ui_dict_no_resource_uri() -> None:
    """Should return None when ui dict has no resourceUri."""
    tool = MockTool(meta={"ui": {"visibility": ["app"]}})
    assert extract_app_metadata(tool) is None


def test_extract_app_ui_with_resource_uri() -> None:
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


def test_extract_app_ui_with_csp() -> None:
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


def test_extract_app_ui_with_permissions() -> None:
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


def test_extract_app_snake_case_keys() -> None:
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


def test_extract_app_from_dict() -> None:
    """Should work with dict-like tools (not just objects)."""
    tool = {"meta": {"ui": {"resourceUri": "ui://test/view.html"}}}
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/view.html"


def test_extract_app_from_flat_ui_keys() -> None:
    """Should parse FastMCP-style flat ui/* metadata keys."""
    tool = MockTool(
        meta={
            "ui/resourceUri": "ui://test/flat.html",
            "ui/prefersBorder": True,
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/flat.html"
    assert result.prefers_border is True


def test_extract_app_from_extension_id_key() -> None:
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


def test_extract_app_from_compat_extension_id_key() -> None:
    """Should parse compatibility extension-id keyed metadata payload."""
    tool = MockTool(
        meta={
            "io.modelcontextprotocol/apps": {
                "resourceUri": "ui://test/compat.html",
                "visibility": ["app"],
            }
        }
    )
    result = extract_app_metadata(tool)
    assert result is not None
    assert result.resource_uri == "ui://test/compat.html"
    assert result.visibility == ["app"]


# ─── ToolNode App Detection Tests ────────────────────────────────────────────


class MockMCPTool:
    """Mock MCP tool for ToolNode testing."""

    def __init__(
        self,
        name: str,
        description: str = "",
        meta: dict[str, dict[str, object]] | None = None,
    ) -> None:
        """Store ``name``, ``description`` and ``meta``."""
        self.name = name
        self.description = description
        self.inputSchema = {"type": "object", "properties": {}}
        self.meta = meta


def test_convert_tools_detects_app_metadata(registry: ModelRegistry) -> None:
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

    specs = node.convert_mcp_tools(tools)
    assert len(specs) == 2

    # Regular tool should not have app metadata
    regular = next(s for s in specs if s.name == "test_server.regular_tool")
    assert "has_app" not in regular.extra

    # App tool should have app metadata
    app = next(s for s in specs if s.name == "test_server.app_tool")
    assert app.extra.get("has_app") is True
    assert app.extra["app_metadata"]["resource_uri"] == "ui://test/view.html"

    # Should be in _app_metadata dict
    assert node.has_app("test_server.app_tool")
    assert not node.has_app("test_server.regular_tool")


def test_convert_tools_apps_disabled(registry: ModelRegistry) -> None:
    """App detection should be skipped when apps.enabled is False."""
    config = build_config()
    config.apps.enabled = False
    node = ToolNode(config=config, registry=registry)

    tools = [
        MockMCPTool(
            "app_tool", meta={"ui": {"resourceUri": "ui://test/view.html"}}
        )
    ]
    specs = node.convert_mcp_tools(tools)

    assert len(specs) == 1
    assert "has_app" not in specs[0].extra
    assert not node.has_app("test_server.app_tool")


# ─── App HTML Fetching Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_app_html_success(registry: ModelRegistry) -> None:
    """_fetch_app_html should extract text from resource contents."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Mock content with text attribute
    class MockContent:
        text = "<html><body>Hello</body></html>"

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(return_value=[MockContent()])
    node.mcp_client = mock_client

    html = await node.fetch_app_html("ui://test/view.html")
    assert html is not None
    assert "<html>" in html


@pytest.mark.asyncio
async def test_fetch_app_html_failure(registry: ModelRegistry) -> None:
    """_fetch_app_html should return None on error."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(side_effect=Exception("Not found"))
    node.mcp_client = mock_client

    html = await node.fetch_app_html("ui://test/view.html")
    assert html is None


@pytest.mark.asyncio
async def test_fetch_app_html_no_client(registry: ModelRegistry) -> None:
    """_fetch_app_html should return None when no MCP client."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node.mcp_client = None

    html = await node.fetch_app_html("ui://test/view.html")
    assert html is None


class _RecordingSession(ClientSession):
    """MCP client session that answers ``initialize`` locally.

    It is a real ``ClientSession`` over unused in-memory streams, so the
    capability probing in ``ToolNode.initialize_mcp_client`` sees the same
    attributes as in production. Requests and notifications are recorded.
    """

    def __init__(self) -> None:
        """Open the in-memory streams and record nothing yet."""
        read_send, read_recv = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](1)
        write_send, write_recv = anyio.create_memory_object_stream[
            SessionMessage
        ](1)
        self._streams = (read_send, write_recv)
        super().__init__(
            read_recv,
            write_send,
            client_info=mcp_types.Implementation(
                name="penguiflow-test", version="1.0"
            ),
            sampling_callback=_sample,
            elicitation_callback=_elicit,
            list_roots_callback=_list_roots,
        )
        self.sent_request: mcp_types.ClientRequest | None = None
        self.sent_notification: mcp_types.ClientNotification | None = None

    @override
    async def send_request(
        self,
        request: mcp_types.ClientRequest,
        result_type: type[ReceiveResultT],
        request_read_timeout_seconds: timedelta | None = None,
        metadata: MessageMetadata = None,
        progress_callback: ProgressFnT | None = None,
    ) -> ReceiveResultT:
        self.sent_request = request
        return result_type.model_validate(
            {
                "protocolVersion": mcp_types.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "serverInfo": {"name": "mock-server", "version": "1.0"},
            }
        )

    @override
    async def send_notification(
        self,
        notification: mcp_types.ClientNotification,
        related_request_id: str | int | None = None,
    ) -> None:
        self.sent_notification = notification


async def _sample(
    context: RequestContext[ClientSession, Any],
    params: mcp_types.CreateMessageRequestParams,
) -> mcp_types.ErrorData:
    """Sampling callback stub; its presence enables the capability."""
    return mcp_types.ErrorData(
        code=-1,
        message=f"request {context.request_id}: no sampling "
        f"({params.maxTokens} tokens)",
    )


async def _elicit(
    context: RequestContext[ClientSession, Any],
    params: mcp_types.ElicitRequestParams,
) -> mcp_types.ErrorData:
    """Elicitation callback stub; its presence enables the capability."""
    return mcp_types.ErrorData(
        code=-1,
        message=f"request {context.request_id}: no elicitation "
        f"({params.message})",
    )


async def _list_roots(
    context: RequestContext[ClientSession, Any],
) -> mcp_types.ErrorData:
    """Roots callback stub; its presence enables the capability."""
    return mcp_types.ErrorData(
        code=-1, message=f"request {context.request_id}: no roots"
    )


class _RecordingClient:
    """The parts of ``fastmcp.Client`` that MCP initialization uses."""

    def __init__(self) -> None:
        """Wrap a fresh recording session; not initialized yet."""
        self._session = _RecordingSession()
        self._init_timeout: float | None = None

    @property
    def session(self) -> _RecordingSession:
        """The recording session."""
        return self._session

    @property
    def initialize_result(self) -> InitializeResult | None:
        """No initialize result: the handshake has not run."""
        return None

    async def initialize(self) -> InitializeResult:
        """Fail: Apps-aware init runs its own handshake."""
        msg = "initialize() not expected when Apps are enabled"
        raise AssertionError(msg)


class _MonitoringClient(_RecordingClient):
    """Client exposing FastMCP-style session monitoring."""

    def __init__(self) -> None:
        """Start with no monitored calls."""
        super().__init__()
        self.monitor_calls = 0

    async def _await_with_session_monitoring(
        self, coro: Awaitable[InitializeResult]
    ) -> InitializeResult:
        self.monitor_calls += 1
        return await coro


@pytest.mark.asyncio
async def test_initialize_mcp_client_advertises_apps(
    registry: ModelRegistry,
) -> None:
    """MCP initialize advertises HTML apps in both shapes."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    client = _RecordingClient()

    result = await node.initialize_mcp_client(client)

    assert result.protocolVersion == mcp_types.LATEST_PROTOCOL_VERSION
    sent = client.session.sent_request
    assert sent is not None
    request = sent.root
    assert isinstance(request, mcp_types.InitializeRequest)
    capabilities = request.params.capabilities.model_dump()
    for extension_id in UI_COMPAT_EXTENSION_IDS:
        assert capabilities["extensions"][extension_id]["mimeTypes"] == [
            UI_MIME_TYPE
        ]
        assert capabilities["experimental"][extension_id]["mimeTypes"] == [
            UI_MIME_TYPE
        ]
    notified = client.session.sent_notification
    assert notified is not None
    assert notified.root.method == "notifications/initialized"


def test_mcp_initialization_messages_serialize_wire_aliases() -> None:
    """Manual initialization messages must retain MCP wire aliases."""
    request = mcp_types.ClientRequest(
        mcp_types.InitializeRequest(
            params=mcp_types.InitializeRequestParams(
                protocolVersion=mcp_types.LATEST_PROTOCOL_VERSION,
                capabilities=mcp_types.ClientCapabilities(
                    roots=mcp_types.RootsCapability(listChanged=True)
                ),
                clientInfo=mcp_types.Implementation(
                    name="penguiflow-test", version="1.0"
                ),
            )
        )
    )
    notification = mcp_types.ClientNotification(
        mcp_types.InitializedNotification()
    )

    assert request.model_dump(by_alias=True, exclude_none=True) == {
        "method": "initialize",
        "params": {
            "protocolVersion": mcp_types.LATEST_PROTOCOL_VERSION,
            "capabilities": {"roots": {"listChanged": True}},
            "clientInfo": {"name": "penguiflow-test", "version": "1.0"},
        },
    }
    assert notification.model_dump(by_alias=True, exclude_none=True) == {
        "method": "notifications/initialized"
    }


@pytest.mark.asyncio
async def test_initialize_mcp_client_uses_session_monitoring(
    registry: ModelRegistry,
) -> None:
    """Apps-aware init should use FastMCP session monitoring when available."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    client = _MonitoringClient()

    await node.initialize_mcp_client(client)

    assert client.monitor_calls == 1


# ─── Text Extraction Tests ───────────────────────────────────────────────────


def test_extract_text_from_list() -> None:
    """Should extract text from list of content items."""

    class Item:
        text = "hello"

    result = _extract_text_from_resource([Item()])
    assert result == "hello"


def test_extract_text_from_dict() -> None:
    """Should extract text from dict."""
    result = _extract_text_from_resource({"text": "world"})
    assert result == "world"


def test_extract_text_from_single_object() -> None:
    """Should extract text from single object."""

    class Obj:
        text = "content"

    result = _extract_text_from_resource(Obj())
    assert result == "content"


def test_extract_text_no_text() -> None:
    """Should return None when no text found."""
    result = _extract_text_from_resource({"binary": b"data"})
    assert result is None


# ─── Result Enrichment Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_with_app_html(
    registry: ModelRegistry, artifact_store: InMemoryArtifactStore
) -> None:
    """_enrich_with_app_html should package result with app HTML artifact."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    # Mock resource fetch
    class MockContent:
        text = "<html><body>App UI</body></html>"

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(return_value=[MockContent()])
    node.mcp_client = mock_client

    app_meta = AppMetadata(
        resource_uri="ui://test/view.html",
        csp=AppCSP(connect_domains=["https://api.example.com"]),
        prefers_border=True,
    )

    ctx = DummyCtx(artifact_store)
    ctx.tool_context["session_id"] = "sess-123"
    original_result = {"data": "tool output"}

    enriched = await node.enrich_with_app_html(
        original_result,
        app_meta,
        {"deck_id": "deck-123"},
        ctx,
    )

    assert isinstance(enriched, dict)
    assert "__mcp_app__" in enriched
    assert enriched["data"] == "tool output"  # Original data preserved
    assert enriched["__mcp_app__"]["resource_uri"] == "ui://test/view.html"
    assert enriched["__mcp_app__"]["prefers_border"] is True
    assert enriched["__mcp_app__"]["csp"]["connect_domains"] == [
        "https://api.example.com"
    ]
    assert enriched["__mcp_app__"]["artifact_id"] is not None

    ref = await artifact_store.get_ref(enriched["__mcp_app__"]["artifact_id"])
    assert ref is not None
    assert ref.mime_type == UI_MIME_TYPE
    assert ref.source["namespace"] == "test_server"
    assert ref.source["session_id"] == "sess-123"
    assert ref.source["csp"]["connect_domains"] == ["https://api.example.com"]
    assert ref.source["tool_input"] == {"deck_id": "deck-123"}


@pytest.mark.asyncio
async def test_call_preserves_structured_mcp_result_for_app_payload(
    registry: ModelRegistry,
    artifact_store: InMemoryArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App tools keep the raw CallToolResult for the app host."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node.connected = True
    node.connected_loop = asyncio.get_running_loop()
    node.mcp_client = object()
    node.app_metadata["test_server.open_deck_editor"] = AppMetadata(
        resource_uri="ui://deck-editor/index.html"
    )

    raw_result = mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text",
                text=(
                    'Opening deck editor for "Q2 2026 Platform Roadmap: '
                    'AI & Agent Deployment"'
                ),
            )
        ],
        structuredContent={
            "editor_state": {
                "deck": {
                    "id": "deck-123",
                    "title": "Q2 2026 Platform Roadmap: AI & Agent Deployment",
                },
                "selectedSlide": {
                    "slideId": "slide-1",
                    "revisionHash": "abc123",
                    "metadata": {"title": "Intro"},
                    "html": "<section>Intro</section>",
                },
            }
        },
        isError=False,
    )

    monkeypatch.setattr(node, "resolve_auth", AsyncMock(return_value={}))
    call_with_retry = AsyncMock(return_value=raw_result)
    monkeypatch.setattr(node, "call_with_retry", call_with_retry)
    transform_output = AsyncMock(
        return_value=(
            'Opening deck editor for "Q2 2026 Platform Roadmap: AI & '
            'Agent Deployment"'
        )
    )
    monkeypatch.setattr(node, "transform_output", transform_output)
    monkeypatch.setattr(
        node,
        "fetch_app_html",
        AsyncMock(return_value="<html><body>Deck Editor</body></html>"),
    )

    ctx = DummyCtx(artifact_store)
    ctx.tool_context["session_id"] = "sess-123"

    result = await node.call(
        "test_server.open_deck_editor",
        {"deck_id": "deck-123"},
        ctx,
    )
    assert isinstance(result, dict)

    app_payload = result["result"]["__mcp_app__"]
    assert result["result"]["value"] == (
        'Opening deck editor for "Q2 2026 Platform Roadmap: AI & '
        'Agent Deployment"'
    )
    assert app_payload["tool_input"] == {"deck_id": "deck-123"}
    assert (
        app_payload["tool_data"]["structuredContent"]["editor_state"]["deck"][
            "id"
        ]
        == "deck-123"
    )
    assert app_payload["tool_data"]["content"][0]["text"].startswith(
        "Opening deck editor"
    )
    transform_output.assert_awaited_once_with(
        "open_deck_editor",
        'Opening deck editor for "Q2 2026 Platform Roadmap: AI & '
        'Agent Deployment"',
        ctx,
    )
    call_with_retry.assert_awaited_once_with(
        "open_deck_editor",
        {"deck_id": "deck-123"},
        {},
        raw_mcp=True,
    )

    ref = await artifact_store.get_ref(app_payload["artifact_id"])
    assert ref is not None
    assert (
        ref.source["tool_data"]["structuredContent"]["editor_state"][
            "selectedSlide"
        ]["slideId"]
        == "slide-1"
    )


@pytest.mark.asyncio
async def test_enrich_with_app_html_fetch_failure(
    registry: ModelRegistry, artifact_store: InMemoryArtifactStore
) -> None:
    """Should return original result when HTML fetch fails."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    mock_client = AsyncMock()
    mock_client.read_resource = AsyncMock(side_effect=Exception("Fail"))
    node.mcp_client = mock_client

    app_meta = AppMetadata(resource_uri="ui://test/view.html")
    ctx = DummyCtx(artifact_store)
    original_result = {"data": "tool output"}

    enriched = await node.enrich_with_app_html(
        original_result, app_meta, {}, ctx
    )
    assert isinstance(enriched, dict)

    # Should return original result unchanged
    assert enriched == original_result
    assert "__mcp_app__" not in enriched


# ─── Public API Tests ────────────────────────────────────────────────────────


def test_app_tools_property(registry: ModelRegistry) -> None:
    """app_tools should return all app metadata."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    meta = AppMetadata(resource_uri="ui://test/view.html")
    node.app_metadata["test_server.tool1"] = meta

    tools = node.app_tools
    assert len(tools) == 1
    assert "test_server.tool1" in tools


def test_get_app_metadata(registry: ModelRegistry) -> None:
    """get_app_metadata should return metadata for known tools."""
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    meta = AppMetadata(resource_uri="ui://test/view.html")
    node.app_metadata["test_server.tool1"] = meta

    assert node.get_app_metadata("test_server.tool1") is not None
    assert node.get_app_metadata("test_server.unknown") is None


def test_artifact_registry_preserves_mcp_app_metadata() -> None:
    """MCP App binary records should retain structured renderer metadata."""
    ref = ArtifactRef(
        id="art_1",
        mime_type=UI_MIME_TYPE,
        source={
            "csp": {"connect_domains": ["https://api.example.com"]},
            "permissions": {"camera": True},
            "tool_data": {"hello": "world"},
            "tool_input": {"deck_id": "deck-1"},
            "prefers_border": True,
            "namespace": "test_ns",
            "session_id": "sess-1",
            "sandbox": "allow-scripts",
        },
    )
    registry = ArtifactRegistry()
    registry.register_binary_artifact(
        ref, source_tool="test_ns.tool", step_index=0
    )

    payload = registry.resolve_ref("art_1", session_id="sess-1")
    assert payload is not None
    assert payload["component"] == "mcp_app"
    assert payload["props"]["namespace"] == "test_ns"
    assert payload["props"]["session_id"] == "sess-1"
    assert payload["props"]["sandbox"] == "allow-scripts"
    assert payload["props"]["csp"]["connect_domains"] == [
        "https://api.example.com"
    ]
    assert payload["props"]["tool_input"] == {"deck_id": "deck-1"}
