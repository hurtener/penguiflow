"""Tests for ToolNode phase1."""

from __future__ import annotations

import asyncio
import base64
import functools
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Never

import pytest
from pydantic import create_model
from typing_extensions import override

from penguiflow.registry import ModelRegistry
from penguiflow.tools.auth import (
    InMemoryTokenStore,
    OAuthManager,
    OAuthProviderConfig,
)
from penguiflow.tools.config import (
    ArtifactExtractionConfig,
    ArtifactFieldConfig,
    AuthType,
    BinaryDetectionConfig,
    ExternalToolConfig,
    TransportType,
    UtcpMode,
)
from penguiflow.tools.errors import (
    ToolAuthError,
    ToolNodeError,
    ToolServerError,
)
from penguiflow.tools.node import ToolNode, _validate_magic_bytes

from .tool_context_doubles import FakeToolContext

if TYPE_CHECKING:
    from collections.abc import Mapping

    from penguiflow.artifacts import (
        InMemoryArtifactStore,
    )
    from penguiflow.planner.context import PlannerPauseReason, ToolContext

pytest.importorskip("tenacity")


class FakeMcpTool:
    """MCP tool as listed by the server."""

    def __init__(
        self, name: str, description: str, input_schema: dict[str, object]
    ) -> None:
        """Store ``name``, ``description`` and ``input_schema``."""
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class DummyCtx(FakeToolContext):
    """Tool context for ToolNode tests."""

    def __init__(
        self,
        tool_context: dict[str, Any] | None = None,
        artifact_store: InMemoryArtifactStore | None = None,
    ) -> None:
        """Use ``tool_context`` and ``artifact_store`` when given."""
        super().__init__(
            tool_context=tool_context, artifact_store=artifact_store
        )


def build_config(**overrides: object) -> ExternalToolConfig:
    """Build an MCP tool config, with ``overrides`` applied."""
    base: dict[str, object] = {
        "name": "github",
        "transport": TransportType.MCP,
        "connection": "npx -y @modelcontextprotocol/server-github",
    }
    base.update(overrides)
    return ExternalToolConfig.model_validate(base)


def test_convert_mcp_tools_namespaces_and_registers() -> None:
    """Convert MCP tools namespaces and registers."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    tool = FakeMcpTool(
        name="create_issue",
        description="Create an issue",
        input_schema={
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    )

    specs = node.convert_mcp_tools([tool])

    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "github.create_issue"
    args = spec.args_model(title="hello")
    assert args.model_dump()["title"] == "hello"
    assert node.tool_name_map["github.create_issue"] == "create_issue"
    # functools.partial stores bound args in .args tuple
    assert isinstance(spec.node.func, functools.partial)
    assert spec.node.func.args[0] == "github.create_issue"


def test_convert_mcp_tools_rejects_duplicates() -> None:
    """Convert MCP tools rejects duplicates."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    tool = FakeMcpTool(name="dup", description="", input_schema={})
    node.convert_mcp_tools([tool])

    with pytest.raises(ToolNodeError):
        node.convert_mcp_tools([tool])


def test_convert_utcp_tools_namespacing() -> None:
    """Convert UTCP tools namespacing."""
    registry = ModelRegistry()
    config = build_config(name="stripe", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    class FakeUtcpTool:
        def __init__(
            self,
            name: str,
            description: str,
            inputs: dict[str, dict[str, object]],
        ) -> None:
            self.name = name
            self.description = description
            self.inputs = inputs

    utcp_tool = FakeUtcpTool(
        "manual.create_charge",
        "Charge card",
        {"properties": {"amount": {"type": "number"}}},
    )
    specs = node.convert_utcp_tools([utcp_tool])

    assert specs[0].name == "stripe.create_charge"
    args = specs[0].args_model(amount=3.14)
    assert args.model_dump()["amount"] == 3.14
    assert node.tool_name_map["stripe.create_charge"] == "manual.create_charge"
    # functools.partial stores bound args in .args tuple
    assert isinstance(specs[0].node.func, functools.partial)
    assert specs[0].node.func.args[0] == "stripe.create_charge"


def test_convert_mcp_tools_skips_existing_registry_entry() -> None:
    """Already registered tools are not re-registered on reconnect."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    dummy_args = create_model("DummyArgs", foo=(str | None, None))
    dummy_out = create_model("DummyOut", foo=(str | None, None))
    registry.register("github.list", dummy_args, dummy_out)

    # Should not raise - silently skips re-registration for reconnection
    # support
    specs = node.convert_mcp_tools(
        [FakeMcpTool(name="list", description="test", input_schema={})]
    )
    assert len(specs) == 1
    assert specs[0].name == "github.list"


def test_substitute_env_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substitute env missing var."""
    monkeypatch.delenv("MISSING_ENV", raising=False)
    registry = ModelRegistry()
    config = build_config(
        auth_type=AuthType.API_KEY, auth_config={"api_key": "${MISSING_ENV}"}
    )
    node = ToolNode(config=config, registry=registry)

    with pytest.raises(ToolAuthError):
        node.substitute_env("${MISSING_ENV}")


class _ServerError(Exception):
    """An MCP error carrying an HTTP 500 status."""

    status_code = 500


class FlakyMcpClient:
    """MCP client whose first tool call fails with a server error."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, object]:
        """Fail the first call with a 500; echo the tool name afterwards."""
        self.calls.append((name, args))
        if len(self.calls) == 1:
            msg = "boom"
            raise _ServerError(msg)
        return {"ok": True, "name": name}


@pytest.mark.asyncio
async def test_call_retries_on_retryable_error() -> None:
    """Call retries on retryable error."""
    registry = ModelRegistry()
    config = build_config(
        retry_policy={
            "wait_exponential_min_s": 0.1,
            "wait_exponential_max_s": 0.2,
        }
    )
    node = ToolNode(config=config, registry=registry)
    client = FlakyMcpClient()
    node.mcp_client = client
    node.connected = True
    node.tool_name_map["github.ping"] = "ping"

    result = await node.call("github.ping", {}, DummyCtx())

    # Result is wrapped in {"result": <data>} to match output model schema
    assert result == {"result": {"ok": True, "name": "ping"}}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_call_with_retry_surfaces_tool_error() -> None:
    """Call with retry surfaces tool error."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node.mcp_client = type(
        "FailingClient",
        (),
        {"call_tool": staticmethod(lambda *_, **__: (_raise_status_exc()))},
    )()
    node.connected = True

    with pytest.raises(ToolServerError):
        await node.call_with_retry("ping", {})


def _raise_status_exc() -> Never:
    msg = "HTTP 500 failure"
    raise _ServerError(msg)


def test_build_utcp_config_auto_manual() -> None:
    """Build UTCP config auto manual."""
    registry = ModelRegistry()
    config = build_config(
        name="weather",
        transport=TransportType.UTCP,
        connection="https://api.example.com/.well-known/utcp.json",
        utcp_mode=UtcpMode.AUTO,
    )
    node = ToolNode(config=config, registry=registry)
    config_dict = node.build_utcp_config()
    assert "manuals" in config_dict
    assert config_dict["manuals"][0].endswith(".json")


@pytest.mark.asyncio
async def test_oauth_flow_pause_and_resume() -> None:
    """OAuth flow pause and resume."""
    registry = ModelRegistry()
    store = InMemoryTokenStore()
    manager = OAuthManager(
        providers={
            "github": OAuthProviderConfig(
                name="github",
                display_name="GitHub",
                auth_url="https://github.com/login/oauth/authorize",
                token_url="https://github.com/login/oauth/access_token",
                client_id="id",
                client_secret="secret",
                redirect_uri="https://example.com/callback",
                scopes=["repo"],
            )
        },
        token_store=store,
    )

    config = build_config(auth_type=AuthType.OAUTH2_USER)
    node = ToolNode(config=config, registry=registry, auth_manager=manager)

    class _ConsentingCtx(DummyCtx):
        """Context whose pause stands for the user completing OAuth."""

        @override
        async def pause(
            self,
            reason: PlannerPauseReason,
            payload: Mapping[str, Any] | None = None,
        ) -> object:
            await store.store("u1", "github", "token123", None)
            return await super().pause(reason, payload)

    ctx = _ConsentingCtx(tool_context={"user_id": "u1", "trace_id": "t1"})

    headers = await node.resolve_auth(ctx)
    assert headers == {"Authorization": "Bearer token123"}


@pytest.mark.asyncio
async def test_cancelled_error_not_retried() -> None:
    """Cancelled error not retried."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node.mcp_client = type(
        "CancelClient", (), {"call_tool": staticmethod(_raise_cancel)}
    )()
    node.connected = True
    node.tool_name_map["github.ping"] = "ping"

    with pytest.raises(asyncio.CancelledError):
        await node.call("github.ping", {}, DummyCtx())


def _raise_cancel(*_args: str | dict[str, object], **_kwargs: object) -> Never:
    raise asyncio.CancelledError()


def test_oauth_pending_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """OAuth pending cleanup."""
    provider = OAuthProviderConfig(
        name="github",
        display_name="GitHub",
        auth_url="https://auth",
        token_url="https://token",
        client_id="id",
        client_secret="secret",
        redirect_uri="https://cb",
        scopes=["repo"],
    )
    manager = OAuthManager(providers={"github": provider})

    # Seed a pending state that is past the 600s TTL.
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 700)
    old_state = manager.get_auth_request("github", "u", "t")["state"]
    monkeypatch.setattr(time, "time", lambda: now)

    # New request should cleanup expired state
    req = manager.get_auth_request("github", "u2", "t2")
    assert req["state"] != old_state
    assert old_state not in manager.pending_states


# =============================================================================
# Phase 1 Tests: Artifact Extraction and Binary Detection
# =============================================================================


class TestBinarySignatureDetection:
    """Tests for binary content detection via base64 signatures."""

    def test_detect_pdf_signature(self) -> None:
        """Test detection of PDF base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # PDF starts with %PDF- which is JVBERi in base64
        pdf_base64 = "JVBERi0xLjQKJeLjz9MKMSAwIG9iag=="
        result = node.detect_binary_signature(pdf_base64)

        assert result is not None
        assert result == ("pdf", "application/pdf")

    def test_detect_png_signature(self) -> None:
        """Test detection of PNG base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # PNG magic bytes base64 encoded
        png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        result = node.detect_binary_signature(png_base64)

        assert result is not None
        assert result == ("png", "image/png")

    def test_detect_jpeg_signature(self) -> None:
        """Test detection of JPEG base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # JPEG magic bytes base64 encoded
        jpeg_base64 = "/9j/4AAQSkZJRgABAQEAYABgAAD"
        result = node.detect_binary_signature(jpeg_base64)

        assert result is not None
        assert result == ("jpeg", "image/jpeg")

    def test_detect_zip_signature(self) -> None:
        """Test detection of ZIP base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # ZIP magic bytes base64 encoded
        zip_base64 = "UEsDBBQAAAAIAA=="
        result = node.detect_binary_signature(zip_base64)

        assert result is not None
        assert result == ("zip", "application/zip")

    def test_no_signature_for_regular_text(self) -> None:
        """Test that regular text doesn't trigger binary detection."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        regular_text = "Hello, this is just regular text content"
        result = node.detect_binary_signature(regular_text)

        assert result is None


class TestMagicBytesValidation:
    """Tests for magic bytes validation after base64 decode."""

    def test_validate_pdf_magic_bytes(self) -> None:
        """Test PDF magic bytes validation."""
        pdf_data = b"%PDF-1.4\n%\xe2\xe3\xd3\xd4"
        assert _validate_magic_bytes(pdf_data, "pdf") is True

        invalid_data = b"Not a PDF file"
        assert _validate_magic_bytes(invalid_data, "pdf") is False

    def test_validate_png_magic_bytes(self) -> None:
        """Test PNG magic bytes validation."""
        png_data = b"\x89PNG\r\n\x1a\n"
        assert _validate_magic_bytes(png_data, "png") is True

        invalid_data = b"Not PNG"
        assert _validate_magic_bytes(invalid_data, "png") is False

    def test_unknown_extension_passes(self) -> None:
        """Test that unknown extensions always pass validation."""
        any_data = b"Any random data"
        assert _validate_magic_bytes(any_data, "unknown") is True


class TestOutputTransformation:
    """Tests for the layered output transformation pipeline."""

    @pytest.mark.asyncio
    async def test_transform_passthrough_for_small_content(self) -> None:
        """Test that small content passes through unchanged."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {"message": "Hello world", "status": "ok"}

        transformed = await node.transform_output("test_tool", result, ctx)

        assert transformed == result

    @pytest.mark.asyncio
    async def test_binary_extraction_from_base64(self) -> None:
        """Test that base64 binary content is extracted to artifact."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()

        # Create a large enough base64 PDF string to trigger detection
        pdf_header = b"%PDF-1.4\n%\xe2\xe3\xd3\xd4"
        pdf_content = pdf_header + b"x" * 2000  # Make it large
        pdf_base64 = base64.b64encode(pdf_content).decode("utf-8")

        result = {"pdf_data": pdf_base64}

        transformed = await node.transform_output("test_tool", result, ctx)
        assert isinstance(transformed, dict)

        # Should have extracted the binary content
        assert "pdf_data" in transformed
        assert "artifact" in transformed["pdf_data"]
        assert "summary" in transformed["pdf_data"]

    @pytest.mark.asyncio
    async def test_size_limit_triggers_artifact_extraction(self) -> None:
        """Test that oversized text content is stored as artifact."""
        registry = ModelRegistry()
        config = build_config(
            artifact_extraction=ArtifactExtractionConfig(max_inline_size=100)
        )
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()

        # Create content larger than the limit
        large_text = "x" * 500

        transformed = await node.transform_output("test_tool", large_text, ctx)

        assert isinstance(transformed, dict)
        assert "artifact" in transformed
        assert "summary" in transformed
        assert "preview" in transformed

    @pytest.mark.asyncio
    async def test_custom_transformer_override(self) -> None:
        """Test that custom transformer takes precedence."""
        registry = ModelRegistry()

        def custom_transformer(
            _tool_name: str, result: dict[str, str], _ctx: ToolContext
        ) -> dict[str, object]:
            return {"custom": True, "original": result}

        config = build_config(output_transformer=custom_transformer)
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {"data": "test"}

        transformed = await node.transform_output("test_tool", result, ctx)

        assert transformed == {"custom": True, "original": result}

    @pytest.mark.asyncio
    async def test_async_custom_transformer(self) -> None:
        """Test that async custom transformer works."""
        registry = ModelRegistry()

        async def async_transformer(
            tool_name: str, _result: dict[str, str], _ctx: ToolContext
        ) -> dict[str, bool | str]:
            await asyncio.sleep(0.001)  # Simulate async work
            return {"async": True, "tool": tool_name}

        config = build_config(output_transformer=async_transformer)
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {"data": "test"}

        transformed = await node.transform_output("test_tool", result, ctx)

        assert transformed == {"async": True, "tool": "github.test_tool"}

    @pytest.mark.asyncio
    async def test_resource_link_transformation(self) -> None:
        """Test that resource_link types are transformed."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {
            "file": {
                "type": "resource_link",
                "uri": "mcp://resource/file123",
            }
        }

        transformed = await node.transform_output("test_tool", result, ctx)
        assert isinstance(transformed, dict)

        assert transformed["file"]["type"] == "artifact_stub"
        assert "resource_uri" in transformed["file"]
        assert "summary" in transformed["file"]

    @pytest.mark.asyncio
    async def test_nested_dict_binary_extraction(self) -> None:
        """Test binary extraction in nested dict structures."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()

        # Create nested structure with binary content
        png_data = b"\x89PNG\r\n\x1a\n" + b"x" * 2000
        png_base64 = base64.b64encode(png_data).decode("utf-8")

        result = {
            "response": {
                "status": "ok",
                "image": png_base64,
            }
        }

        transformed = await node.transform_output("test_tool", result, ctx)
        assert isinstance(transformed, dict)

        assert "response" in transformed
        assert "image" in transformed["response"]
        assert "artifact" in transformed["response"]["image"]


class TestFieldExtraction:
    """Tests for per-tool field extraction configuration."""

    @pytest.mark.asyncio
    async def test_field_extraction_by_path(self) -> None:
        """Test extraction of specific field by path."""
        registry = ModelRegistry()
        field_config = ArtifactFieldConfig(
            field_path="data.content",
            content_type="text",
        )
        config = build_config(
            artifact_extraction=ArtifactExtractionConfig(
                tool_fields={"github.test_tool": [field_config]}
            )
        )
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {
            "data": {
                "content": "x" * 500,  # Large enough to trigger extraction
                "metadata": "keep this",
            }
        }

        transformed = await node.transform_output("test_tool", result, ctx)
        assert isinstance(transformed, dict)

        # The content field should be transformed
        assert "artifact" in transformed["data"]["content"]
        # Other fields preserved
        assert transformed["data"]["metadata"] == "keep this"

    @pytest.mark.asyncio
    async def test_field_extraction_only_for_configured_tools(self) -> None:
        """Configured tool_fields should apply only to matching tool names."""
        registry = ModelRegistry()
        blob = base64.b64encode(b"PK\x03\x04" + b"x" * 256).decode("utf-8")

        field_config = ArtifactFieldConfig(
            field_path="resource.blob",
            content_type="binary",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        config = build_config(
            artifact_extraction=ArtifactExtractionConfig(
                max_inline_size=100_000,
                binary_detection=BinaryDetectionConfig(enabled=False),
                handle_mcp_typed_content=False,
                tool_fields={"github.export_pptx": [field_config]},
            )
        )
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {
            "resource": {"blob": blob, "mimeType": "application/octet-stream"}
        }

        transformed_target = await node.transform_output(
            "export_pptx", deepcopy(result), ctx
        )
        assert isinstance(transformed_target, dict)
        transformed_other = await node.transform_output(
            "list_templates", deepcopy(result), ctx
        )
        assert isinstance(transformed_other, dict)

        assert "artifact" in transformed_target["resource"]["blob"]
        assert isinstance(transformed_other["resource"], dict)
        assert "blob" in transformed_other["resource"]
        assert "artifact" not in transformed_other["resource"]["blob"]

    @pytest.mark.asyncio
    async def test_field_extraction_handles_list_content_blocks(self) -> None:
        """Configured extraction works on a list of content blocks."""
        registry = ModelRegistry()
        blob = base64.b64encode(b"%PDF-1.7\n" + b"x" * 512).decode("utf-8")

        field_config = ArtifactFieldConfig(
            field_path="resource.blob",
            content_type="pdf",
            mime_type="application/pdf",
        )
        config = build_config(
            artifact_extraction=ArtifactExtractionConfig(
                max_inline_size=200,
                binary_detection=BinaryDetectionConfig(enabled=False),
                handle_mcp_typed_content=False,
                tool_fields={"github.export_pdf": [field_config]},
            )
        )
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = [
            {"type": "text", "text": "ok"},
            {
                "type": "resource",
                "resource": {"blob": blob, "mimeType": "application/pdf"},
            },
        ]

        transformed = await node.transform_output(
            "export_pdf", deepcopy(result), ctx
        )
        assert isinstance(transformed, list)

        assert "artifact" in transformed[1]["resource"]["blob"]
        assert (
            transformed[1]["resource"]["blob"]["artifact"]["mime_type"]
            == "application/pdf"
        )

    @pytest.mark.asyncio
    async def test_field_extraction_handles_wrapped_result_payload(
        self,
    ) -> None:
        """Configured extraction works on {'result': ...} wrappers."""
        registry = ModelRegistry()
        blob = base64.b64encode(b"%PDF-1.7\n" + b"x" * 512).decode("utf-8")

        field_config = ArtifactFieldConfig(
            field_path="resource.blob",
            content_type="pdf",
            mime_type="application/pdf",
        )
        config = build_config(
            artifact_extraction=ArtifactExtractionConfig(
                max_inline_size=200,
                binary_detection=BinaryDetectionConfig(enabled=False),
                handle_mcp_typed_content=False,
                tool_fields={"github.export_pdf": [field_config]},
            )
        )
        node = ToolNode(config=config, registry=registry)
        node.connected = True

        ctx = DummyCtx()
        result = {
            "result": {
                "resource": {"blob": blob, "mimeType": "application/pdf"}
            }
        }

        transformed = await node.transform_output(
            "export_pdf", deepcopy(result), ctx
        )
        assert isinstance(transformed, dict)

        assert "artifact" in transformed["result"]["resource"]["blob"]
        assert (
            transformed["result"]["resource"]["blob"]["artifact"]["mime_type"]
            == "application/pdf"
        )


class TestBinaryDetectionConfig:
    """Tests for binary detection configuration."""

    def test_default_signatures(self) -> None:
        """Test default binary signatures are loaded."""
        from penguiflow.tools.config import DEFAULT_BINARY_SIGNATURES

        assert "JVBERi" in DEFAULT_BINARY_SIGNATURES  # PDF
        assert "iVBORw" in DEFAULT_BINARY_SIGNATURES  # PNG
        assert "/9j/" in DEFAULT_BINARY_SIGNATURES  # JPEG
        assert "UEsDB" in DEFAULT_BINARY_SIGNATURES  # ZIP

    def test_custom_signatures(self) -> None:
        """Test custom signatures can be added."""
        custom_sigs = {"CUSTOM": ("custom", "application/x-custom")}
        config = BinaryDetectionConfig(signatures=custom_sigs)

        assert "CUSTOM" in config.signatures
        assert config.signatures["CUSTOM"] == (
            "custom",
            "application/x-custom",
        )

    def test_detection_disabled(self) -> None:
        """Test detection can be disabled."""
        config = BinaryDetectionConfig(enabled=False)
        assert config.enabled is False


class TestArtifactExtractionConfig:
    """Tests for artifact extraction configuration."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = ArtifactExtractionConfig()

        assert config.max_inline_size == 10_000
        assert config.auto_artifact_large_content is True
        assert config.binary_detection.enabled is True
        assert config.handle_mcp_typed_content is True

    def test_custom_size_limits(self) -> None:
        """Test custom size limit configuration."""
        config = ArtifactExtractionConfig(max_inline_size=5000)

        assert config.max_inline_size == 5000

    def test_disable_typed_content_handling(self) -> None:
        """MCP typed-content handling can be disabled via config."""
        config = ArtifactExtractionConfig(handle_mcp_typed_content=False)
        assert config.handle_mcp_typed_content is False

    def test_tool_fields_configuration(self) -> None:
        """Test per-tool field configuration."""
        field = ArtifactFieldConfig(
            field_path="pdf_content",
            content_type="pdf",
            mime_type="application/pdf",
        )
        config = ArtifactExtractionConfig(
            tool_fields={"tableau.download": [field]}
        )

        assert "tableau.download" in config.tool_fields
        assert (
            config.tool_fields["tableau.download"][0].field_path
            == "pdf_content"
        )
