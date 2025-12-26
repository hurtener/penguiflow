import asyncio
import base64
import time

import pytest
from pydantic import create_model

from penguiflow.artifacts import InMemoryArtifactStore
from penguiflow.registry import ModelRegistry
from penguiflow.tools.auth import InMemoryTokenStore, OAuthManager, OAuthProviderConfig
from penguiflow.tools.config import (
    ArtifactExtractionConfig,
    ArtifactFieldConfig,
    AuthType,
    BinaryDetectionConfig,
    ExternalToolConfig,
    TransportType,
    UtcpMode,
)
from penguiflow.tools.errors import ToolAuthError, ToolNodeError, ToolServerError
from penguiflow.tools.node import ToolNode

pytest.importorskip("tenacity")


class FakeMcpTool:
    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class DummyCtx:
    def __init__(
        self,
        tool_context: dict[str, str] | None = None,
        artifact_store: InMemoryArtifactStore | None = None,
    ):
        self._tool_context = tool_context or {}
        self._llm_context: dict[str, str] = {}
        self._meta: dict[str, str] = {}
        self.paused_payload = None
        self._artifacts = artifact_store or InMemoryArtifactStore()

    @property
    def llm_context(self):
        return self._llm_context

    @property
    def meta(self):
        return self._meta

    @property
    def tool_context(self):
        return self._tool_context

    @property
    def artifacts(self):
        return self._artifacts

    async def pause(self, reason, payload=None):  # pragma: no cover - not used in Phase 1 tests
        self.paused_payload = {"reason": reason, "payload": payload}
        return None

    async def emit_chunk(self, stream_id, seq, text, *, done=False, meta=None):  # pragma: no cover
        return None

    async def emit_artifact(self, stream_id, chunk, *, done=False, artifact_type=None, meta=None):  # pragma: no cover
        return None


def build_config(**overrides):
    base = {
        "name": "github",
        "transport": TransportType.MCP,
        "connection": "npx -y @modelcontextprotocol/server-github",
    }
    base.update(overrides)
    return ExternalToolConfig(**base)


def test_convert_mcp_tools_namespaces_and_registers():
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    tool = FakeMcpTool(
        name="create_issue",
        description="Create an issue",
        input_schema={"properties": {"title": {"type": "string"}}, "required": ["title"]},
    )

    specs = node._convert_mcp_tools([tool])

    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "github.create_issue"
    args = spec.args_model(title="hello")
    assert args.title == "hello"
    assert node._tool_name_map["github.create_issue"] == "create_issue"
    # functools.partial stores bound args in .args tuple
    assert spec.node.func.args[0] == "github.create_issue"


def test_convert_mcp_tools_rejects_duplicates():
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    tool = FakeMcpTool(name="dup", description="", input_schema={})
    node._convert_mcp_tools([tool])

    with pytest.raises(ToolNodeError):
        node._convert_mcp_tools([tool])


def test_convert_utcp_tools_namespacing():
    registry = ModelRegistry()
    config = build_config(name="stripe", transport=TransportType.UTCP)
    node = ToolNode(config=config, registry=registry)

    class FakeUtcpTool:
        def __init__(self, name, description, inputs):
            self.name = name
            self.description = description
            self.inputs = inputs

    utcp_tool = FakeUtcpTool("manual.create_charge", "Charge card", {"properties": {"amount": {"type": "number"}}})
    specs = node._convert_utcp_tools([utcp_tool])

    assert specs[0].name == "stripe.create_charge"
    args = specs[0].args_model(amount=3.14)
    assert args.amount == 3.14
    assert node._tool_name_map["stripe.create_charge"] == "manual.create_charge"
    # functools.partial stores bound args in .args tuple
    assert specs[0].node.func.args[0] == "stripe.create_charge"


def test_convert_mcp_tools_skips_existing_registry_entry():
    """When a tool is already registered, skip re-registration (supports reconnection)."""
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)

    DummyArgs = create_model("DummyArgs", foo=(str | None, None))
    DummyOut = create_model("DummyOut", foo=(str | None, None))
    registry.register("github.list", DummyArgs, DummyOut)

    # Should not raise - silently skips re-registration for reconnection support
    specs = node._convert_mcp_tools([FakeMcpTool(name="list", description="test", input_schema={})])
    assert len(specs) == 1
    assert specs[0].name == "github.list"


def test_substitute_env_missing_var(monkeypatch):
    monkeypatch.delenv("MISSING_ENV", raising=False)
    registry = ModelRegistry()
    config = build_config(auth_type=AuthType.API_KEY, auth_config={"api_key": "${MISSING_ENV}"})
    node = ToolNode(config=config, registry=registry)

    with pytest.raises(ToolAuthError):
        node._substitute_env("${MISSING_ENV}")


class FlakyMcpClient:
    def __init__(self):
        self.calls = 0

    async def call_tool(self, name, args):
        self.calls += 1
        if self.calls == 1:
            exc = Exception("boom")
            exc.status_code = 500
            raise exc
        return {"ok": True, "name": name}


@pytest.mark.asyncio
async def test_call_retries_on_retryable_error():
    registry = ModelRegistry()
    config = build_config(retry_policy={"wait_exponential_min_s": 0.1, "wait_exponential_max_s": 0.2})
    node = ToolNode(config=config, registry=registry)
    node._mcp_client = FlakyMcpClient()
    node._connected = True
    node._tool_name_map["github.ping"] = "ping"

    result = await node.call("github.ping", {}, DummyCtx())

    # Result is wrapped in {"result": <data>} to match output model schema
    assert result == {"result": {"ok": True, "name": "ping"}}
    assert node._mcp_client.calls == 2


@pytest.mark.asyncio
async def test_call_with_retry_surfaces_tool_error():
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node._mcp_client = type(
        "FailingClient",
        (),
        {"call_tool": staticmethod(lambda *_, **__: (_raise_status_exc()))},
    )()
    node._connected = True

    with pytest.raises(ToolServerError):
        await node._call_with_retry("ping", {})


def _build_status_exc():
    err = Exception("HTTP 500 failure")
    err.status_code = 500
    return err


def _raise_status_exc():
    raise _build_status_exc()


def test_build_utcp_config_auto_manual():
    registry = ModelRegistry()
    config = build_config(
        name="weather",
        transport=TransportType.UTCP,
        connection="https://api.example.com/.well-known/utcp.json",
        utcp_mode=UtcpMode.AUTO,
    )
    node = ToolNode(config=config, registry=registry)
    config_dict = node._build_utcp_config()
    assert "manuals" in config_dict
    assert config_dict["manuals"][0].endswith(".json")


@pytest.mark.asyncio
async def test_oauth_flow_pause_and_resume(monkeypatch):
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

    async def fake_get_token(user_id: str, provider: str) -> str | None:
        return await store.get(user_id, provider)

    manager.get_token = fake_get_token  # type: ignore[assignment]

    config = build_config(auth_type=AuthType.OAUTH2_USER)
    node = ToolNode(config=config, registry=registry, auth_manager=manager)
    ctx = DummyCtx(tool_context={"user_id": "u1", "trace_id": "t1"})

    # Arrange pause to inject token as if user completed OAuth
    async def pause(reason, payload=None):
        await store.store("u1", "github", "token123", None)
        return None

    ctx.pause = pause  # type: ignore[assignment]

    headers = await node._resolve_auth(ctx)
    assert headers == {"Authorization": "Bearer token123"}


@pytest.mark.asyncio
async def test_cancelled_error_not_retried():
    registry = ModelRegistry()
    config = build_config()
    node = ToolNode(config=config, registry=registry)
    node._mcp_client = type("CancelClient", (), {"call_tool": staticmethod(_raise_cancel)})()
    node._connected = True
    node._tool_name_map["github.ping"] = "ping"

    with pytest.raises(asyncio.CancelledError):
        await node.call("github.ping", {}, DummyCtx())


def _raise_cancel(*args, **kwargs):
    raise asyncio.CancelledError()


def test_oauth_pending_cleanup(monkeypatch):
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

    # Seed an expired pending state
    old_state = "old"
    manager._pending[old_state] = {
        "user_id": "u",
        "trace_id": "t",
        "provider": "github",
        "created_at": time.time() - 700,
    }

    # New request should cleanup expired state
    req = manager.get_auth_request("github", "u2", "t2")
    assert req["state"] != old_state
    assert old_state not in manager._pending


# =============================================================================
# Phase 1 Tests: Artifact Extraction and Binary Detection
# =============================================================================


class TestBinarySignatureDetection:
    """Tests for binary content detection via base64 signatures."""

    def test_detect_pdf_signature(self):
        """Test detection of PDF base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # PDF starts with %PDF- which is JVBERi in base64
        pdf_base64 = "JVBERi0xLjQKJeLjz9MKMSAwIG9iag=="
        result = node._detect_binary_signature(pdf_base64)

        assert result is not None
        assert result == ("pdf", "application/pdf")

    def test_detect_png_signature(self):
        """Test detection of PNG base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # PNG magic bytes base64 encoded
        png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
        result = node._detect_binary_signature(png_base64)

        assert result is not None
        assert result == ("png", "image/png")

    def test_detect_jpeg_signature(self):
        """Test detection of JPEG base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # JPEG magic bytes base64 encoded
        jpeg_base64 = "/9j/4AAQSkZJRgABAQEAYABgAAD"
        result = node._detect_binary_signature(jpeg_base64)

        assert result is not None
        assert result == ("jpeg", "image/jpeg")

    def test_detect_zip_signature(self):
        """Test detection of ZIP base64 signature."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        # ZIP magic bytes base64 encoded
        zip_base64 = "UEsDBBQAAAAIAA=="
        result = node._detect_binary_signature(zip_base64)

        assert result is not None
        assert result == ("zip", "application/zip")

    def test_no_signature_for_regular_text(self):
        """Test that regular text doesn't trigger binary detection."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        regular_text = "Hello, this is just regular text content"
        result = node._detect_binary_signature(regular_text)

        assert result is None


class TestMagicBytesValidation:
    """Tests for magic bytes validation after base64 decode."""

    def test_validate_pdf_magic_bytes(self):
        """Test PDF magic bytes validation."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        pdf_data = b"%PDF-1.4\n%\xe2\xe3\xd3\xd4"
        assert node._validate_magic_bytes(pdf_data, "pdf") is True

        invalid_data = b"Not a PDF file"
        assert node._validate_magic_bytes(invalid_data, "pdf") is False

    def test_validate_png_magic_bytes(self):
        """Test PNG magic bytes validation."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        png_data = b"\x89PNG\r\n\x1a\n"
        assert node._validate_magic_bytes(png_data, "png") is True

        invalid_data = b"Not PNG"
        assert node._validate_magic_bytes(invalid_data, "png") is False

    def test_unknown_extension_passes(self):
        """Test that unknown extensions always pass validation."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)

        any_data = b"Any random data"
        assert node._validate_magic_bytes(any_data, "unknown") is True


class TestOutputTransformation:
    """Tests for the layered output transformation pipeline."""

    @pytest.mark.asyncio
    async def test_transform_passthrough_for_small_content(self):
        """Test that small content passes through unchanged."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node._connected = True

        ctx = DummyCtx()
        result = {"message": "Hello world", "status": "ok"}

        transformed = await node._transform_output("test_tool", result, ctx)

        assert transformed == result

    @pytest.mark.asyncio
    async def test_binary_extraction_from_base64(self):
        """Test that base64 binary content is extracted to artifact."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node._connected = True

        ctx = DummyCtx()

        # Create a large enough base64 PDF string to trigger detection
        pdf_header = b"%PDF-1.4\n%\xe2\xe3\xd3\xd4"
        pdf_content = pdf_header + b"x" * 2000  # Make it large
        pdf_base64 = base64.b64encode(pdf_content).decode("utf-8")

        result = {"pdf_data": pdf_base64}

        transformed = await node._transform_output("test_tool", result, ctx)

        # Should have extracted the binary content
        assert "pdf_data" in transformed
        assert "artifact" in transformed["pdf_data"]
        assert "summary" in transformed["pdf_data"]

    @pytest.mark.asyncio
    async def test_size_limit_triggers_artifact_extraction(self):
        """Test that oversized text content is stored as artifact."""
        registry = ModelRegistry()
        config = build_config(
            artifact_extraction=ArtifactExtractionConfig(max_inline_size=100)
        )
        node = ToolNode(config=config, registry=registry)
        node._connected = True

        ctx = DummyCtx()

        # Create content larger than the limit
        large_text = "x" * 500

        transformed = await node._transform_output("test_tool", large_text, ctx)

        assert isinstance(transformed, dict)
        assert "artifact" in transformed
        assert "summary" in transformed
        assert "preview" in transformed

    @pytest.mark.asyncio
    async def test_custom_transformer_override(self):
        """Test that custom transformer takes precedence."""
        registry = ModelRegistry()

        def custom_transformer(tool_name, result, ctx):
            return {"custom": True, "original": result}

        config = build_config(output_transformer=custom_transformer)
        node = ToolNode(config=config, registry=registry)
        node._connected = True

        ctx = DummyCtx()
        result = {"data": "test"}

        transformed = await node._transform_output("test_tool", result, ctx)

        assert transformed == {"custom": True, "original": result}

    @pytest.mark.asyncio
    async def test_async_custom_transformer(self):
        """Test that async custom transformer works."""
        registry = ModelRegistry()

        async def async_transformer(tool_name, result, ctx):
            await asyncio.sleep(0.001)  # Simulate async work
            return {"async": True, "tool": tool_name}

        config = build_config(output_transformer=async_transformer)
        node = ToolNode(config=config, registry=registry)
        node._connected = True

        ctx = DummyCtx()
        result = {"data": "test"}

        transformed = await node._transform_output("test_tool", result, ctx)

        assert transformed == {"async": True, "tool": "github.test_tool"}

    @pytest.mark.asyncio
    async def test_resource_link_transformation(self):
        """Test that resource_link types are transformed."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node._connected = True

        ctx = DummyCtx()
        result = {
            "file": {
                "type": "resource_link",
                "uri": "mcp://resource/file123",
            }
        }

        transformed = await node._transform_output("test_tool", result, ctx)

        assert transformed["file"]["type"] == "artifact_stub"
        assert "resource_uri" in transformed["file"]
        assert "summary" in transformed["file"]

    @pytest.mark.asyncio
    async def test_nested_dict_binary_extraction(self):
        """Test binary extraction in nested dict structures."""
        registry = ModelRegistry()
        config = build_config()
        node = ToolNode(config=config, registry=registry)
        node._connected = True

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

        transformed = await node._transform_output("test_tool", result, ctx)

        assert "response" in transformed
        assert "image" in transformed["response"]
        assert "artifact" in transformed["response"]["image"]


class TestFieldExtraction:
    """Tests for per-tool field extraction configuration."""

    @pytest.mark.asyncio
    async def test_field_extraction_by_path(self):
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
        node._connected = True

        ctx = DummyCtx()
        result = {
            "data": {
                "content": "x" * 500,  # Large enough to trigger extraction
                "metadata": "keep this",
            }
        }

        transformed = await node._transform_output("test_tool", result, ctx)

        # The content field should be transformed
        assert "artifact" in transformed["data"]["content"]
        # Other fields preserved
        assert transformed["data"]["metadata"] == "keep this"


class TestBinaryDetectionConfig:
    """Tests for binary detection configuration."""

    def test_default_signatures(self):
        """Test default binary signatures are loaded."""
        from penguiflow.tools.config import DEFAULT_BINARY_SIGNATURES

        assert "JVBERi" in DEFAULT_BINARY_SIGNATURES  # PDF
        assert "iVBORw" in DEFAULT_BINARY_SIGNATURES  # PNG
        assert "/9j/" in DEFAULT_BINARY_SIGNATURES  # JPEG
        assert "UEsDB" in DEFAULT_BINARY_SIGNATURES  # ZIP

    def test_custom_signatures(self):
        """Test custom signatures can be added."""
        custom_sigs = {"CUSTOM": ("custom", "application/x-custom")}
        config = BinaryDetectionConfig(signatures=custom_sigs)

        assert "CUSTOM" in config.signatures
        assert config.signatures["CUSTOM"] == ("custom", "application/x-custom")

    def test_detection_disabled(self):
        """Test detection can be disabled."""
        config = BinaryDetectionConfig(enabled=False)
        assert config.enabled is False


class TestArtifactExtractionConfig:
    """Tests for artifact extraction configuration."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ArtifactExtractionConfig()

        assert config.max_inline_size == 10_000
        assert config.auto_artifact_large_content is True
        assert config.binary_detection.enabled is True

    def test_custom_size_limits(self):
        """Test custom size limit configuration."""
        config = ArtifactExtractionConfig(max_inline_size=5000)

        assert config.max_inline_size == 5000

    def test_tool_fields_configuration(self):
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
        assert config.tool_fields["tableau.download"][0].field_path == "pdf_content"
