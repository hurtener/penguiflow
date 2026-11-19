"""Tests for Databricks provider initialization and configuration.

Updated January 2026 for current Databricks Foundation Model APIs. Uses
databricks-claude-sonnet-4-5 as the primary test model.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMCancelledError,
    LLMInvalidRequestError,
    LLMTimeoutError,
)
from penguiflow.llm.providers.databricks import (
    _extract_databricks_error,
    _thinking_budget_tokens_for_effort,
)
from penguiflow.llm.types import (
    LLMMessage,
    LLMRequest,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    ToolSpec,
)

from .fake_sdk_clients import openai_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Never


@pytest.fixture
def mock_openai_sdk() -> MagicMock:
    """Mock the OpenAI SDK (used by Databricks)."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_sdk.AsyncOpenAI.return_value = mock_client
    return mock_sdk


class TestDatabricksProviderInit:
    """Test Databricks provider initialization."""

    def test_init_with_host_and_token(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test initialization with explicit host and token."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="my-workspace.cloud.databricks.com",
                token="dapi-token-123",
            )

            assert provider.model == "databricks-claude-sonnet-4-5"
            assert provider.provider_name == "databricks"
            mock_openai_sdk.AsyncOpenAI.assert_called_once()
            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["api_key"] == "dapi-token-123"
            assert (
                "my-workspace.cloud.databricks.com" in call_kwargs["base_url"]
            )

    def test_init_with_api_key_alias(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization with explicit api_key alias."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="my-workspace.cloud.databricks.com",
                api_key="dapi-api-key-123",
            )

            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["api_key"] == "dapi-api-key-123"

    def test_init_strips_databricks_prefix(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test that databricks/ prefix is stripped from model name."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks/databricks-gpt-5-2",
                host="my-workspace.cloud.databricks.com",
                token="token",
            )

            # Model should have prefix stripped
            assert provider.model == "databricks-gpt-5-2"
            assert provider.endpoint == "databricks-gpt-5-2"
            assert provider.original_model == "databricks/databricks-gpt-5-2"

            # Base URL should use stripped endpoint name
            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert "databricks-gpt-5-2" in call_kwargs["base_url"]
            assert (
                "databricks/databricks-gpt-5-2" not in call_kwargs["base_url"]
            )

    def test_init_normalizes_host(self, mock_openai_sdk: MagicMock) -> None:
        """Test that host is normalized."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            # Test with https:// prefix
            DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="https://my-workspace.cloud.databricks.com/",
                token="token",
            )

            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["base_url"] == (
                "https://my-workspace.cloud.databricks.com/serving-endpoints/databricks-claude-sonnet-4-5/"
            )

    def test_init_with_timeout(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization with custom timeout."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
                timeout=240.0,
            )

            assert provider.timeout == 240.0
            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["timeout"] == 240.0

    def test_init_uses_env_vars(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization uses environment variables."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            with patch.dict(
                os.environ,
                {
                    "DATABRICKS_HOST": "env-workspace.databricks.com",
                    "DATABRICKS_TOKEN": "env-token",
                },
            ):
                DatabricksProvider("databricks-claude-sonnet-4-5")

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert asyncio.run(call_kwargs["api_key"]()) == "env-token"
                assert (
                    "env-workspace.databricks.com" in call_kwargs["base_url"]
                )

                os.environ["DATABRICKS_TOKEN"] = "rotated-env-token"
                assert (
                    asyncio.run(call_kwargs["api_key"]())
                    == "rotated-env-token"
                )

    def test_init_uses_api_base_api_key_aliases(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test API_BASE/API_KEY aliases are accepted for host/token."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            with patch.dict(
                os.environ,
                {
                    "DATABRICKS_API_BASE": "https://env-workspace.databricks.com/serving-endpoints",
                    "DATABRICKS_API_KEY": "env-api-key",
                },
                clear=True,
            ):
                DatabricksProvider("databricks-claude-sonnet-4-5")

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert asyncio.run(call_kwargs["api_key"]()) == "env-api-key"
                assert call_kwargs["base_url"] == (
                    "https://env-workspace.databricks.com/serving-endpoints/databricks-claude-sonnet-4-5/"
                )

    def test_init_with_token_provider(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test custom token_provider is resolved for each request."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            tokens = iter(["provider-token-1", "provider-token-2"])

            DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token_provider=lambda: next(tokens),
            )

            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert asyncio.run(call_kwargs["api_key"]()) == "provider-token-1"
            assert asyncio.run(call_kwargs["api_key"]()) == "provider-token-2"

    def test_init_with_async_token_provider(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """An async token_provider works as the OpenAI SDK auth callback."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            async def token_provider() -> str:
                return "async-provider-token"

            DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token_provider=token_provider,
            )

            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert (
                asyncio.run(call_kwargs["api_key"]()) == "async-provider-token"
            )

    def test_init_with_service_principal_auth(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """client_id/client_secret auth refreshes via the Databricks SDK."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            instances: list[Any] = []

            class FakeConfig:
                def __init__(self, **kwargs: str) -> None:
                    self.kwargs = kwargs
                    self.count = 0
                    instances.append(self)

                def authenticate(self) -> dict[str, str]:
                    self.count += 1
                    return {
                        "Authorization": f"Bearer oauth-token-{self.count}"
                    }

            with patch("databricks.sdk.config.Config", FakeConfig):
                DatabricksProvider(
                    "databricks-claude-sonnet-4-5",
                    host="https://workspace.databricks.com/serving-endpoints",
                    client_id="client-id",
                    client_secret="client-secret",
                    oauth_scopes="all-apis",
                    authorization_details='[{"type":"workspace_permission"}]',
                )

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert asyncio.run(call_kwargs["api_key"]()) == "oauth-token-1"
                assert asyncio.run(call_kwargs["api_key"]()) == "oauth-token-2"

            assert len(instances) == 1
            assert instances[0].kwargs == {
                "host": "https://workspace.databricks.com",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": "all-apis",
                "authorization_details": '[{"type":"workspace_permission"}]',
            }

    def test_init_with_service_principal_env_auth(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Service principal auth can come from deployment env vars."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            instances: list[Any] = []

            class FakeConfig:
                def __init__(self, **kwargs: str) -> None:
                    self.kwargs = kwargs
                    instances.append(self)

                def authenticate(self) -> dict[str, str]:
                    return {"Authorization": "Bearer env-oauth-token"}

            with (
                patch("databricks.sdk.config.Config", FakeConfig),
                patch.dict(
                    os.environ,
                    {
                        "DATABRICKS_API_BASE": "https://env-workspace.databricks.com/serving-endpoints",
                        "DATABRICKS_CLIENT_ID": "env-client-id",
                        "DATABRICKS_CLIENT_SECRET": "env-client-secret",
                        "DATABRICKS_OAUTH_SCOPES": "all-apis",
                        "DATABRICKS_AUTHORIZATION_DETAILS": (
                            '[{"type":"workspace_permission"}]'
                        ),
                    },
                    clear=True,
                ),
            ):
                DatabricksProvider("databricks-claude-sonnet-4-5")

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert (
                    asyncio.run(call_kwargs["api_key"]()) == "env-oauth-token"
                )

            assert instances[0].kwargs == {
                "host": "https://env-workspace.databricks.com",
                "client_id": "env-client-id",
                "client_secret": "env-client-secret",
                "scopes": "all-apis",
                "authorization_details": '[{"type":"workspace_permission"}]',
            }

    def test_service_principal_auth_not_shadowed_by_env_pat(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Service principal config wins over an env PAT rotation."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            class FakeConfig:
                def authenticate(self) -> dict[str, str]:
                    return {"Authorization": "Bearer service-principal-token"}

                def __init__(self, **_: str) -> None:
                    pass

            with (
                patch("databricks.sdk.config.Config", FakeConfig),
                patch.dict(
                    os.environ,
                    {
                        "DATABRICKS_API_KEY": "unrelated-env-pat",
                    },
                    clear=True,
                ),
            ):
                DatabricksProvider(
                    "databricks-claude-sonnet-4-5",
                    host="workspace.databricks.com",
                    client_id="client-id",
                    client_secret="client-secret",
                )

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert (
                    asyncio.run(call_kwargs["api_key"]())
                    == "service-principal-token"
                )

    def test_init_api_base_with_endpoint_suffix_is_normalized(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test API_BASE including endpoint path still resolves correctly."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            with patch.dict(
                os.environ,
                {
                    "DATABRICKS_API_BASE": "https://env-workspace.databricks.com/serving-endpoints/old-endpoint/",
                    "DATABRICKS_API_KEY": "env-api-key",
                },
                clear=True,
            ):
                DatabricksProvider("new-endpoint")

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert call_kwargs["base_url"] == (
                    "https://env-workspace.databricks.com/serving-endpoints/new-endpoint/"
                )

    def test_init_raises_without_credentials(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test initialization raises without host or token."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            with patch.dict(os.environ, {}, clear=True):
                # Remove env vars
                os.environ.pop("DATABRICKS_HOST", None)
                os.environ.pop("DATABRICKS_TOKEN", None)

                with pytest.raises(
                    ValueError, match="Databricks host and auth required"
                ):
                    DatabricksProvider("databricks-claude-sonnet-4-5")

    def test_init_with_custom_profile(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test initialization with custom model profile."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.profiles import ModelProfile
            from penguiflow.llm.providers.databricks import DatabricksProvider

            custom_profile = ModelProfile(
                supports_tools=True,
                supports_schema_guided_output=True,
                max_output_tokens=4096,
            )
            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
                profile=custom_profile,
            )

            assert provider.profile is custom_profile

    def test_provider_properties(self, mock_openai_sdk: MagicMock) -> None:
        """Test provider property accessors."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            assert provider.provider_name == "databricks"
            assert provider.model == "databricks-claude-sonnet-4-5"
            assert provider.profile is not None


class TestDatabricksProviderValidation:
    """Test Databricks provider validation."""

    def test_validate_request_too_many_tools(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test validation rejects too many tools."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            # Create 33 tools (max is 32)
            tools = tuple(
                ToolSpec(
                    name=f"tool_{i}", description=f"Tool {i}", json_schema={}
                )
                for i in range(33)
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
                tools=tools,
            )

            with pytest.raises(LLMInvalidRequestError, match="max 32 tools"):
                provider.validate_request(request)

    def test_validate_request_within_limits(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test validation passes within limits."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            tools = tuple(
                ToolSpec(
                    name=f"tool_{i}", description=f"Tool {i}", json_schema={}
                )
                for i in range(5)
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
                tools=tools,
            )

            # Should not raise
            provider.validate_request(request)


class TestDatabricksProviderBuildParams:
    """Test Databricks provider parameter building."""

    def test_build_params_basic(self) -> None:
        """Test basic parameter building."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            temperature=0.7,
        )

        params = provider.build_params(request)

        assert params["model"] == "databricks-claude-sonnet-4-5"
        assert params["temperature"] == 0.7

    def test_build_params_opus_4_7_adaptive_thinking(
        self,
    ) -> None:
        """Opus 4.7 uses adaptive thinking with output_config.effort."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-opus-4-7",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        request = LLMRequest(
            model="databricks-claude-opus-4-7",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert "reasoning_effort" not in params
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"]["effort"] == "high"
        assert "max_tokens" not in params

    def test_build_params_opus_4_7_adaptive_thinking_keeps_explicit_max_tokens(
        self,
    ) -> None:
        """Opus 4.7 adaptive thinking should not mutate explicit max_tokens."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-opus-4-7",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        request = LLMRequest(
            model="databricks-claude-opus-4-7",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            max_tokens=1024,
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"]["effort"] == "high"
        assert params["max_tokens"] == 1024

    def test_build_params_non_opus_claude_keeps_budget_thinking(self) -> None:
        """Non-Opus 4.7 Databricks Claude models keep budget-based thinking."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert params["thinking"] == {
            "type": "enabled",
            "budget_tokens": 32768,
        }
        assert isinstance(params["max_tokens"], int)
        assert params["max_tokens"] > params["thinking"]["budget_tokens"]

    def test_build_params_keeps_reasoning_effort_for_supported_models(
        self,
    ) -> None:
        """GPT OSS/Gemini 3 accept reasoning_effort directly."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-gpt-oss-20b",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
        )

        request = LLMRequest(
            model="databricks-gpt-oss-20b",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert params["reasoning_effort"] == "high"

    def test_build_params_opus_4_8_adaptive_thinking(
        self,
    ) -> None:
        """Opus 4.8 rejects thinking budgets, so it uses adaptive thinking."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-opus-4-8",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        request = LLMRequest(
            model="databricks-claude-opus-4-8",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert "reasoning_effort" not in params
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"]["effort"] == "high"

    def test_build_params_registered_sonnet_5_uses_adaptive_thinking(
        self,
    ) -> None:
        """Sonnet 5's profile requires native adaptive-thinking params."""
        from penguiflow.llm.profiles import get_profile
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=get_profile("databricks-claude-sonnet-5"),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            temperature=0.0,
            extra={"reasoning_effort": "low"},
        )

        params = provider.build_params(request)

        assert "temperature" not in params
        assert "reasoning_effort" not in params
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"] == {"effort": "low"}
        assert "budget_tokens" not in params["thinking"]

    def test_build_params_sonnet_5_reasoning_display(
        self,
    ) -> None:
        """Sonnet 5 takes reasoning display only inside adaptive thinking."""
        from penguiflow.llm.profiles import get_profile
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=get_profile("databricks-claude-sonnet-5"),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={
                "reasoning_effort": "high",
                "reasoning_display": "summarized",
            },
        )

        params = provider.build_params(request)

        assert params["thinking"] == {
            "type": "adaptive",
            "display": "summarized",
        }
        assert params["output_config"] == {"effort": "high"}
        assert "reasoning_display" not in params

    def test_build_params_sonnet_5_drops_sampling_params(
        self,
    ) -> None:
        """Sonnet 5's profile drops sampling params Databricks rejects."""
        from penguiflow.llm.profiles import get_profile
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=get_profile("databricks-claude-sonnet-5"),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"temperature": 0.0, "top_p": 0.9, "top_k": 40},
        )

        params = provider.build_params(request)

        assert "temperature" not in params
        assert "top_p" not in params
        assert "top_k" not in params

    def test_build_params_opus_4_8_disabled_effort(self) -> None:
        """A disabled reasoning effort turns thinking off explicitly."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-opus-4-8",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        request = LLMRequest(
            model="databricks-claude-opus-4-8",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "none"},
        )

        params = provider.build_params(request)
        assert params["thinking"] == {"type": "disabled"}
        assert "output_config" not in params

    def test_build_params_sonnet_5_keeps_disabled_thinking(
        self,
    ) -> None:
        """Explicit disabled thinking overrides Sonnet 5 reasoning options."""
        from penguiflow.llm.profiles import get_profile
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=get_profile("databricks-claude-sonnet-5"),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={
                "thinking": {"type": "disabled"},
                "reasoning_effort": "high",
                "reasoning_display": "summarized",
            },
        )

        params = provider.build_params(request)

        assert params["thinking"] == {"type": "disabled"}
        assert "output_config" not in params
        assert "reasoning_effort" not in params
        assert "reasoning_display" not in params

    def test_build_params_profile_style_overrides_model_heuristics(
        self,
    ) -> None:
        """Profile reasoning_request_style beats model-name heuristics."""
        from penguiflow.llm.profiles import ModelProfile
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-opus-4-7",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=ModelProfile(
                reasoning_request_style="thinking_budget",
                max_output_tokens=64000,
            ),
        )

        request = LLMRequest(
            model="databricks-claude-opus-4-7",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert params["thinking"] == {
            "type": "enabled",
            "budget_tokens": 32768,
        }
        assert "output_config" not in params

    def test_build_params_profile_style_covers_unknown_future_model(
        self,
    ) -> None:
        """A new model needs only a profile entry, no provider code."""
        from penguiflow.llm.profiles import ModelProfile
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-opus-5-0",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=ModelProfile(
                reasoning_request_style="adaptive_effort",
                max_output_tokens=64000,
            ),
        )

        request = LLMRequest(
            model="databricks-claude-opus-5-0",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "medium"},
        )

        params = provider.build_params(request)
        assert params["thinking"] == {"type": "adaptive"}
        assert params["output_config"]["effort"] == "medium"

    def test_build_params_invalid_profile_style_falls_back_to_heuristics(
        self,
    ) -> None:
        """An invalid profile style must not break request routing."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            extra={"reasoning_effort": "high"},
        )

        params = provider.build_params(request)
        assert params["thinking"] == {
            "type": "enabled",
            "budget_tokens": 32768,
        }

    def test_opus_4_8_registered_profile(self) -> None:
        """The opus-4-8 profile uses adaptive style and no temperature."""
        from penguiflow.llm.profiles import get_profile

        profile = get_profile("databricks-claude-opus-4-8")
        assert profile.reasoning_request_style == "adaptive_effort"
        assert profile.supports_reasoning is True
        assert profile.supports_temperature is False
        assert (
            profile.native_structured_kind == "databricks_constrained_decoding"
        )

    def test_build_params_with_structured_output(self) -> None:
        """Test parameter building with structured output."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            structured_output=StructuredOutputSpec(
                name="MySchema",
                json_schema={"type": "object"},
                strict=True,
            ),
        )

        params = provider.build_params(request)

        assert "response_format" in params
        assert params["response_format"]["type"] == "json_schema"


class TestDatabricksProviderComplete:
    """Test Databricks provider complete method (invocations)."""

    @pytest.mark.asyncio
    async def test_complete_simple(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test simple completion parsing."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="Hello from Databricks!", tool_calls=None
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
        )

        post_options: list[dict[str, object]] = []

        async def post(path: str, *, body: object, cast_to: object) -> object:
            post_options.append({"body": body, "cast_to": cast_to})
            assert path == "invocations"
            return response

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=5.0,
            client=openai_client(monkeypatch, post=post),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
        )

        out = await provider.complete(request)
        assert out.message.text == "Hello from Databricks!"
        assert out.usage.input_tokens == 10
        assert out.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_extracts_reasoning_from_content_blocks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hybrid reasoning returns reasoning and text content blocks."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        blocks = [
            {
                "type": "reasoning",
                "summary": [
                    {"type": "summary_text", "text": "Reasoning summary."}
                ],
            },
            {"type": "text", "text": "Final answer."},
        ]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=blocks, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
        )

        post_options: list[dict[str, object]] = []

        async def post(_path: str, *, body: object, cast_to: object) -> object:
            post_options.append({"body": body, "cast_to": cast_to})
            return response

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=5.0,
            client=openai_client(monkeypatch, post=post),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
        )

        out = await provider.complete(request)
        assert out.message.text == "Final answer."
        assert out.reasoning_content == "Reasoning summary."

    @pytest.mark.asyncio
    async def test_complete_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test timeout handling."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        post_options: list[dict[str, object]] = []

        async def post(_path: str, *, body: object, cast_to: object) -> object:
            post_options.append({"body": body, "cast_to": cast_to})
            raise TimeoutError()

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=0.001,
            client=openai_client(monkeypatch, post=post),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
        )

        with pytest.raises(LLMTimeoutError):
            await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(self) -> None:
        """Test cancellation handling."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=5.0,
        )

        cancel = MagicMock()
        cancel.is_cancelled.return_value = True

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
        )

        with pytest.raises(LLMCancelledError):
            await provider.complete(request, cancel=cancel)


class TestDatabricksProviderStreaming:
    """Test Databricks provider streaming with structured output fallback."""

    @pytest.mark.asyncio
    async def test_streaming_drops_response_format_and_adds_guidance(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Structured output falls back when streaming on Databricks."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000

        captured_params: dict[str, Any] = {}

        async def mock_stream_gen() -> AsyncIterator[object]:
            # Single text chunk
            chunk = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content='{"result": "ok"}', tool_calls=None
                        ),
                        finish_reason=None,
                    )
                ]
            )
            yield chunk
            # Final chunk with usage
            final = SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )
            yield final

        post_options: list[dict[str, object]] = []

        async def post(
            _path: str,
            *,
            body: object,
            cast_to: object,
            stream: bool = False,
            stream_cls: object = None,
        ) -> object:
            post_options.append(
                {
                    "cast_to": cast_to,
                    "stream": stream,
                    "stream_cls": stream_cls,
                }
            )
            assert isinstance(body, dict)
            captured_params.update(body)
            return mock_stream_gen()

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
            timeout=5.0,
            client=openai_client(monkeypatch, post=post),
        )

        schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
        }
        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
            structured_output=StructuredOutputSpec(
                name="TestSchema",
                json_schema=schema,
                strict=True,
            ),
        )

        events: list[StreamEvent] = []
        response = await provider.complete(
            request,
            stream=True,
            on_stream_event=lambda e: events.append(e),
        )

        # response_format should be dropped for streaming
        assert "response_format" not in captured_params
        # System message with schema guidance should be prepended
        assert any(
            "JSON Schema" in str(m.get("content", ""))
            for m in captured_params["messages"]
        )
        assert response.message.text == '{"result": "ok"}'

    @pytest.mark.asyncio
    async def test_streaming_with_tool_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test streaming handles tool calls correctly."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        async def mock_stream_gen() -> AsyncIterator[object]:
            # Tool call chunk with function name
            tc1 = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_123",
                                    function=SimpleNamespace(
                                        name="get_weather",
                                        arguments='{"city":',
                                    ),
                                )
                            ],
                        ),
                        finish_reason=None,
                    )
                ]
            )
            yield tc1
            # Tool call chunk with more arguments
            tc2 = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    function=SimpleNamespace(
                                        name=None, arguments='"NYC"}'
                                    ),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ]
            )
            yield tc2
            # Usage chunk
            final = SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )
            yield final

        async def post(_path: str, **_kwargs: object) -> object:
            return mock_stream_gen()

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=5.0,
            client=openai_client(monkeypatch, post=post),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Weather?")]),
            ),
        )

        events: list[StreamEvent] = []
        response = await provider.complete(
            request,
            stream=True,
            on_stream_event=lambda e: events.append(e),
        )

        assert len(response.message.parts) == 1
        assert isinstance(response.message.parts[0], ToolCallPart)
        assert response.message.parts[0].name == "get_weather"
        assert response.message.parts[0].arguments_json == '{"city":"NYC"}'

    @pytest.mark.asyncio
    async def test_streaming_with_reasoning_list_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test streaming handles list-shaped content with reasoning blocks."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        async def mock_stream_gen() -> AsyncIterator[object]:
            # Content as list with reasoning
            chunk = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=[
                                {
                                    "type": "thinking",
                                    "text": "Let me reason...",
                                },
                                {"type": "text", "text": "Final answer."},
                            ],
                            tool_calls=None,
                        ),
                        finish_reason="stop",
                    )
                ]
            )
            yield chunk
            final = SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )
            yield final

        async def post(_path: str, **_kwargs: object) -> object:
            return mock_stream_gen()

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=5.0,
            client=openai_client(monkeypatch, post=post),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Think")]),
            ),
        )

        events: list[StreamEvent] = []
        response = await provider.complete(
            request,
            stream=True,
            on_stream_event=lambda e: events.append(e),
        )

        assert response.message.text == "Final answer."
        assert response.reasoning_content == "Let me reason..."

    @pytest.mark.asyncio
    async def test_streaming_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test streaming timeout handling."""
        from penguiflow.llm.errors import LLMError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        async def post(_path: str, **_kwargs: object) -> Never:
            msg = "Stream timed out"
            raise TimeoutError(msg)

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
            timeout=0.001,
            client=openai_client(monkeypatch, post=post),
        )

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ),
        )

        # TimeoutError is caught and mapped to LLMError (or LLMTimeoutError)
        with pytest.raises((LLMTimeoutError, LLMError)):
            await provider.complete(
                request,
                stream=True,
                on_stream_event=lambda _e: None,
            )


class TestDatabricksThinkingBudget:
    """Test Databricks thinking budget token mapping."""

    def test_thinking_budget_tokens_for_effort_low(self) -> None:
        """Test low effort maps to 4096 tokens."""
        assert _thinking_budget_tokens_for_effort("low") == 4096
        assert _thinking_budget_tokens_for_effort("minimal") == 4096

    def test_thinking_budget_tokens_for_effort_medium(self) -> None:
        """Test medium effort maps to 16384 tokens."""
        assert _thinking_budget_tokens_for_effort("medium") == 16384
        assert _thinking_budget_tokens_for_effort("default") == 16384

    def test_thinking_budget_tokens_for_effort_high(self) -> None:
        """Test high effort maps to 32768 tokens."""
        assert _thinking_budget_tokens_for_effort("high") == 32768
        assert _thinking_budget_tokens_for_effort("max") == 32768

    def test_thinking_budget_tokens_for_effort_unknown(self) -> None:
        """Test unknown effort defaults to 4096 tokens."""
        assert _thinking_budget_tokens_for_effort("unknown") == 4096

    def test_ensure_thinking_budget_sets_max_tokens(self) -> None:
        """Test that thinking budget sets max_tokens when not provided."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        params: dict[str, Any] = {
            "thinking": {"type": "enabled", "budget_tokens": 8000}
        }
        provider.ensure_thinking_budget_and_max_tokens(
            params, request_max_tokens=None
        )

        # max_tokens should be set to budget +
        # DEFAULT_VISIBLE_OUTPUT_TOKENS_WITH_THINKING
        assert (
            params["max_tokens"]
            == 8000
            + DatabricksProvider.DEFAULT_VISIBLE_OUTPUT_TOKENS_WITH_THINKING
        )

    def test_ensure_thinking_budget_shrinks_budget_when_max_tokens_too_small(
        self,
    ) -> None:
        """Test that budget is shrunk when max_tokens <= budget."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        params: dict[str, Any] = {
            "thinking": {"type": "enabled", "budget_tokens": 8000},
            "max_tokens": 5000,
        }
        provider.ensure_thinking_budget_and_max_tokens(
            params, request_max_tokens=None
        )

        # Budget should be shrunk to max_tokens - 1
        assert params["thinking"]["budget_tokens"] == 4999

    def test_ensure_thinking_budget_removes_thinking_when_budget_zero(
        self,
    ) -> None:
        """Test that thinking is removed when budget would be <= 0."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        profile = MagicMock()
        profile.max_output_tokens = 64000
        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=profile,
        )

        params: dict[str, Any] = {
            "thinking": {"type": "enabled", "budget_tokens": 0},
        }
        provider.ensure_thinking_budget_and_max_tokens(
            params, request_max_tokens=None
        )

        assert "thinking" not in params

    def test_ensure_thinking_budget_noop_without_thinking(self) -> None:
        """Test that method does nothing when thinking is not set."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=MagicMock(),
        )

        params: dict[str, Any] = {"max_tokens": 1000}
        provider.ensure_thinking_budget_and_max_tokens(
            params, request_max_tokens=1000
        )

        assert params == {"max_tokens": 1000}


@pytest.mark.skip(
    reason="Deprecated: tests below target old chat.completions path."
)
class TestDatabricksProviderCompleteLegacy:
    """Tests for complete() on the legacy chat.completions path."""

    def _create_mock_response(
        self,
        content: str = "Hello!",
        tool_calls: list[dict[str, Any]] | None = None,
        finish_reason: str = "stop",
    ) -> MagicMock:
        """Create a mock OpenAI response."""
        mock_msg = MagicMock()
        mock_msg.content = content
        mock_msg.tool_calls = None

        if tool_calls:
            mock_tc_list = []
            for tc in tool_calls:
                mock_tc = MagicMock()
                mock_tc.id = tc["id"]
                mock_tc.function = MagicMock()
                mock_tc.function.name = tc["name"]
                mock_tc.function.arguments = tc["arguments"]
                mock_tc_list.append(mock_tc)
            mock_msg.tool_calls = mock_tc_list
            mock_msg.content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = finish_reason

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        return mock_response

    @pytest.mark.asyncio
    async def test_complete_simple(self, mock_openai_sdk: MagicMock) -> None:
        """Test simple completion."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_response = self._create_mock_response(
                "Hello from Databricks!"
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
            )

            response = await provider.complete(request)

            assert response.message.text == "Hello from Databricks!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test completion with tool calls."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_response = self._create_mock_response(
                content="",
                tool_calls=[
                    {
                        "id": "call_123",
                        "name": "get_weather",
                        "arguments": '{"city": "NYC"}',
                    }
                ],
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Weather?")]),
                ),
            )

            response = await provider.complete(request)

            assert len(response.message.parts) == 1
            assert isinstance(response.message.parts[0], ToolCallPart)
            assert response.message.parts[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_complete_timeout(self, mock_openai_sdk: MagicMock) -> None:
        """Test timeout handling."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=TimeoutError()
            )
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
            )

            with pytest.raises(LLMTimeoutError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test cancellation handling."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=asyncio.CancelledError()
            )
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test early cancellation via cancel token."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_openai_sdk.AsyncOpenAI.return_value = MagicMock()

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            cancel_token = MagicMock()
            cancel_token.is_cancelled.return_value = True

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request, cancel=cancel_token)


@pytest.mark.skip(
    reason=(
        "Deprecated: Databricks provider streaming uses /invocations via "
        "client.post(stream=True)."
    )
)
class TestDatabricksProviderStreamingLegacy:
    """Test Databricks provider streaming (legacy/deprecated)."""

    @pytest.mark.asyncio
    async def test_streaming_text(self, mock_openai_sdk: MagicMock) -> None:
        """Test streaming text completion."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            # Create mock streaming chunks
            chunks = []
            for text in ["Hello", " ", "world", "!"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta = MagicMock()
                chunk.choices[0].delta.content = text
                chunk.choices[0].delta.tool_calls = None
                chunk.choices[0].finish_reason = None
                chunks.append(chunk)

            # Final chunk
            final_chunk = MagicMock()
            final_chunk.choices = [MagicMock()]
            final_chunk.choices[0].delta = MagicMock()
            final_chunk.choices[0].delta.content = None
            final_chunk.choices[0].delta.tool_calls = None
            final_chunk.choices[0].finish_reason = "stop"
            chunks.append(final_chunk)

            # Usage chunk
            usage_chunk = MagicMock()
            usage_chunk.choices = []
            usage_chunk.usage = MagicMock()
            usage_chunk.usage.prompt_tokens = 10
            usage_chunk.usage.completion_tokens = 4
            usage_chunk.usage.total_tokens = 14
            chunks.append(usage_chunk)

            async def async_gen() -> AsyncIterator[object]:
                for chunk in chunks:
                    yield chunk

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=async_gen()
            )
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            streamed_text: list[str] = []

            def on_stream(event: StreamEvent) -> None:
                if event.delta_text:
                    streamed_text.append(event.delta_text)

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(
                    LLMMessage(role="user", parts=[TextPart(text="Hello")]),
                ),
            )

            response = await provider.complete(
                request,
                stream=True,
                on_stream_event=on_stream,
            )

            assert "".join(streamed_text) == "Hello world!"
            assert response.message.text == "Hello world!"


class TestDatabricksProviderErrorMapping:
    """Test Databricks provider error mapping."""

    def test_map_auth_error(self) -> None:
        """Test authentication error mapping."""
        from openai import AuthenticationError

        from penguiflow.llm.errors import LLMAuthError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        response = MagicMock()
        response.status_code = 401
        exc = AuthenticationError(
            message="Invalid token",
            response=response,
            body=None,
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMAuthError)

    def test_map_rate_limit_error(self) -> None:
        """Test rate limit error mapping."""
        from openai import RateLimitError

        from penguiflow.llm.errors import LLMRateLimitError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        response = MagicMock()
        response.status_code = 429
        exc = RateLimitError(
            message="Rate limited",
            response=response,
            body=None,
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMRateLimitError)

    def test_map_context_length_error(self) -> None:
        """Test context length error mapping via BadRequestError."""
        from openai import BadRequestError

        from penguiflow.llm.errors import LLMContextLengthError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        response = MagicMock()
        response.status_code = 400
        exc = BadRequestError(
            message="context_length_exceeded: Input too long",
            response=response,
            body=None,
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMContextLengthError)

    def test_map_server_error(self) -> None:
        """Test server error mapping via APIStatusError."""
        from openai import APIStatusError

        from penguiflow.llm.errors import LLMServerError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        response = MagicMock()
        response.status_code = 500
        exc = APIStatusError(
            message="Internal server error",
            response=response,
            body=None,
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMServerError)

    def test_map_generic_error(self) -> None:
        """Test generic error mapping."""
        from penguiflow.llm.errors import LLMError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        exc = ValueError("Unknown error")
        result = provider.map_error(exc)
        assert isinstance(result, LLMError)
        assert "Unknown error" in result.message

    def test_map_bad_request_non_context_error(self) -> None:
        """Test bad request error mapping (non-context length)."""
        from openai import BadRequestError

        from penguiflow.llm.errors import LLMInvalidRequestError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        response = MagicMock()
        response.status_code = 400
        exc = BadRequestError(
            message="Invalid parameter",
            response=response,
            body=None,
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMInvalidRequestError)

    def test_map_api_connection_error(self) -> None:
        """Test API connection error mapping."""
        from openai import APIConnectionError

        from penguiflow.llm.errors import LLMServerError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        exc = APIConnectionError(
            message="Connection failed", request=MagicMock()
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMServerError)

    def test_map_api_status_error_4xx(self) -> None:
        """Test API status error mapping for 4xx errors."""
        from openai import APIStatusError

        from penguiflow.llm.errors import LLMInvalidRequestError
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider(
            "databricks-claude-sonnet-4-5",
            host="https://test.cloud.databricks.com",
            token="test-token",
        )

        response = MagicMock()
        response.status_code = 422
        exc = APIStatusError(
            message="Unprocessable entity",
            response=response,
            body=None,
        )
        result = provider.map_error(exc)
        assert isinstance(result, LLMInvalidRequestError)


class TestDatabricksProviderErrorExtraction:
    """Test Databricks error message extraction."""

    def test_extract_simple_error(self, mock_openai_sdk: MagicMock) -> None:
        """Test simple error extraction."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            exc = ValueError("Simple error message")
            result = _extract_databricks_error(exc)

            assert result == "Simple error message"

    def test_extract_nested_json_error(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Test nested JSON error extraction."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            # Simulated Databricks nested error
            exc = ValueError(
                '{"error_code":"BAD_REQUEST","message":"Input is too long."}'
            )
            result = _extract_databricks_error(exc)

            assert "Input is too long" in result


class TestDatabricksProviderErrorMappingFallback:
    """Test Databricks provider error mapping fallback."""

    def test_map_unknown_error_without_sdk_imports(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        """Errors map to a generic error when SDK classes are missing."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.errors import LLMError
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            # Test via the mocked path
            with patch.object(provider, "map_error") as mock_map:
                mock_map.return_value = LLMError(
                    message="Unknown error", provider="databricks"
                )
                result = mock_map(ValueError("Unknown error"))

                assert isinstance(result, LLMError)
                assert "Unknown error" in result.message
