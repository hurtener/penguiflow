import asyncio
import time

import pytest
from pydantic import create_model

from penguiflow.registry import ModelRegistry
from penguiflow.tools.auth import InMemoryTokenStore, OAuthManager, OAuthProviderConfig
from penguiflow.tools.config import AuthType, ExternalToolConfig, TransportType, UtcpMode
from penguiflow.tools.errors import ToolAuthError, ToolNodeError, ToolServerError
from penguiflow.tools.node import ToolNode

pytest.importorskip("tenacity")


class FakeMcpTool:
    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class DummyCtx:
    def __init__(self, tool_context: dict[str, str] | None = None):
        self._tool_context = tool_context or {}
        self._llm_context: dict[str, str] = {}
        self._meta: dict[str, str] = {}
        self.paused_payload = None

    @property
    def llm_context(self):
        return self._llm_context

    @property
    def meta(self):
        return self._meta

    @property
    def tool_context(self):
        return self._tool_context

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
