"""Tests for OpenRouter provider initialization and configuration.

Tests cover the OpenRouter provider which routes to 500+ models from
multiple providers including OpenAI GPT-5.x, Claude 4.x, Gemini 3.x,
DeepSeek R1, Llama 4, and Qwen3.

Reference: https://openrouter.ai/docs/quickstart
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
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


@pytest.fixture
def mock_openai_sdk() -> Any:
    """Mock the OpenAI SDK (used by OpenRouter)."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_sdk.AsyncOpenAI.return_value = mock_client
    return mock_sdk


class TestOpenRouterProviderInit:
    """Test OpenRouter provider initialization."""

    def test_init_with_api_key(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization with explicit API key."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="openrouter-key-123",
            )

            assert provider.model == "anthropic/claude-sonnet-4.5"
            assert provider.provider_name == "openrouter"
            mock_openai_sdk.AsyncOpenAI.assert_called_once()
            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["api_key"] == "openrouter-key-123"
            assert call_kwargs["base_url"] == "https://openrouter.ai/api/v1"

    def test_init_parses_openrouter_prefix(self, mock_openai_sdk: MagicMock) -> None:
        """Test that openrouter/ prefix is removed from model name."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openrouter/anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            # Model name should have openrouter/ prefix removed
            assert provider.model == "anthropic/claude-sonnet-4.5"

    def test_init_with_app_metadata(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization with app URL and title."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
                app_url="https://myapp.com",
                app_title="My App",
            )

            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["default_headers"]["HTTP-Referer"] == "https://myapp.com"
            assert call_kwargs["default_headers"]["X-Title"] == "My App"

    def test_init_with_timeout(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization with custom timeout."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
                timeout=180.0,
            )

            assert provider._timeout == 180.0
            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["timeout"] == 180.0

    def test_init_uses_env_vars(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization uses environment variables."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            with patch.dict(
                os.environ,
                {
                    "OPENROUTER_API_KEY": "env-key",
                    "OPENROUTER_APP_URL": "https://env-app.com",
                    "OPENROUTER_APP_TITLE": "Env App",
                },
            ):
                OpenRouterProvider("anthropic/claude-sonnet-4.5")

                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert call_kwargs["api_key"] == "env-key"
                assert call_kwargs["default_headers"]["HTTP-Referer"] == "https://env-app.com"
                assert call_kwargs["default_headers"]["X-Title"] == "Env App"

    def test_init_raises_without_api_key(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization raises without API key."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("OPENROUTER_API_KEY", None)

                with pytest.raises(ValueError, match="OpenRouter API key required"):
                    OpenRouterProvider("anthropic/claude-sonnet-4.5")

    def test_init_with_custom_profile(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization with custom model profile."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.profiles import ModelProfile
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            custom_profile = ModelProfile(
                supports_tools=True,
                supports_schema_guided_output=True,
                max_output_tokens=4096,
            )
            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
                profile=custom_profile,
            )

            assert provider.profile is custom_profile

    def test_provider_properties(self, mock_openai_sdk: MagicMock) -> None:
        """Test provider property accessors."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            assert provider.provider_name == "openrouter"
            assert provider.model == "anthropic/claude-sonnet-4.5"
            assert provider.profile is not None


class TestOpenRouterProviderModelParsing:
    """Test OpenRouter model string parsing."""

    def test_parse_simple_model(self, mock_openai_sdk: MagicMock) -> None:
        """Test parsing simple model name."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider("gpt-4o", api_key="key")
            model, hint = provider._parse_model("gpt-4o")

            assert model == "gpt-4o"
            assert hint is None

    def test_parse_provider_model(self, mock_openai_sdk: MagicMock) -> None:
        """Test parsing provider/model format."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider("anthropic/claude-sonnet-4.5", api_key="key")
            model, hint = provider._parse_model("anthropic/claude-sonnet-4.5")

            assert model == "anthropic/claude-sonnet-4.5"
            assert hint == "anthropic"

    def test_parse_openrouter_prefix(self, mock_openai_sdk: MagicMock) -> None:
        """Test parsing openrouter/provider/model format."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider("openrouter/openai/gpt-4o", api_key="key")
            model, hint = provider._parse_model("openrouter/openai/gpt-4o")

            assert model == "openai/gpt-4o"
            assert hint == "openai"


class TestOpenRouterProviderBuildParams:
    """Test OpenRouter provider parameter building."""

    def test_build_params_basic(self, mock_openai_sdk: MagicMock) -> None:
        """Test basic parameter building."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                temperature=0.7,
            )

            params = provider._build_params(request)

            assert params["model"] == "anthropic/claude-sonnet-4.5"
            assert params["temperature"] == 0.7

    def test_build_params_with_transforms(self, mock_openai_sdk: MagicMock) -> None:
        """Test parameter building with OpenRouter transforms."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"transforms": ["middle-out"]},
            )

            params = provider._build_params(request)

            assert params["transforms"] == ["middle-out"]

    def test_build_params_with_route(self, mock_openai_sdk: MagicMock) -> None:
        """Test parameter building with OpenRouter route."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"route": "fallback"},
            )

            params = provider._build_params(request)

            assert params["route"] == "fallback"

    def test_build_params_with_provider_preferences(self, mock_openai_sdk: MagicMock) -> None:
        """Test parameter building with OpenRouter provider preferences."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={
                    "provider": {
                        "order": ["Anthropic", "OpenAI"],
                        "allow_fallbacks": True,
                    }
                },
            )

            params = provider._build_params(request)

            assert params["provider"]["order"] == ["Anthropic", "OpenAI"]
            assert params["provider"]["allow_fallbacks"] is True

    def test_build_params_with_fallback_models(self, mock_openai_sdk: MagicMock) -> None:
        """Test parameter building with fallback models list."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openai/gpt-5",
                api_key="key",
            )

            request = LLMRequest(
                model="openai/gpt-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={
                    "models": [
                        "openai/gpt-5",
                        "anthropic/claude-sonnet-4.5",
                        "google/gemini-2.5-pro",
                    ]
                },
            )

            params = provider._build_params(request)

            assert len(params["models"]) == 3
            assert "openai/gpt-5" in params["models"]

    def test_build_params_with_structured_output(self, mock_openai_sdk: MagicMock) -> None:
        """Test parameter building with structured output."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                structured_output=StructuredOutputSpec(
                    name="MySchema",
                    json_schema={"type": "object"},
                    strict=True,
                ),
            )

            params = provider._build_params(request)

            assert "response_format" in params
            assert params["response_format"]["type"] == "json_schema"

    def test_build_params_with_json_object_mode(self, mock_openai_sdk: MagicMock) -> None:
        """Test generic non-strict structured output maps to json_object."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                structured_output=StructuredOutputSpec(
                    name="json_response",
                    json_schema={"type": "object"},
                    strict=False,
                ),
            )

            params = provider._build_params(request)

            assert "response_format" in params
            assert params["response_format"]["type"] == "json_object"

    def test_build_params_xai_maps_reasoning_enabled(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openrouter/x-ai/grok-4.1-fast",
                api_key="key",
            )

            request = LLMRequest(
                model="x-ai/grok-4.1-fast",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"reasoning_enabled": True},
            )

            params = provider._build_params(request)

            assert params["extra_body"]["reasoning"] == {"enabled": True}
            assert "reasoning_enabled" not in params
            assert "reasoning" not in params

    def test_build_params_xai_maps_reasoning_effort_off(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openrouter/x-ai/grok-4.1-fast",
                api_key="key",
            )

            request = LLMRequest(
                model="x-ai/grok-4.1-fast",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"reasoning_effort": "off"},
            )

            params = provider._build_params(request)

            assert params["extra_body"]["reasoning"] == {"enabled": False}
            assert "reasoning_effort" not in params
            assert "reasoning" not in params

    def test_build_params_xai_keeps_explicit_reasoning(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openrouter/x-ai/grok-4.1-fast",
                api_key="key",
            )

            request = LLMRequest(
                model="x-ai/grok-4.1-fast",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"reasoning": {"enabled": False}, "reasoning_enabled": True, "reasoning_effort": "high"},
            )

            params = provider._build_params(request)

            assert params["extra_body"]["reasoning"] == {"enabled": False}
            assert "reasoning_enabled" not in params
            assert "reasoning_effort" not in params
            assert "reasoning" not in params

    def test_build_params_non_xai_keeps_reasoning_effort(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"reasoning_enabled": True, "reasoning_effort": "high"},
            )

            params = provider._build_params(request)

            assert params["reasoning_effort"] == "high"
            assert "reasoning_enabled" not in params

    def test_build_params_sets_max_tokens_tools_and_tool_choice(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openai/gpt-5",
                api_key="key",
            )

            request = LLMRequest(
                model="openai/gpt-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                max_tokens=128,
                tools=(
                    ToolSpec(
                        name="get_weather",
                        description="Get weather",
                        json_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                    ),
                ),
                tool_choice="get_weather",
            )

            params = provider._build_params(request)

            assert params["max_tokens"] == 128
            assert params["tools"][0]["type"] == "function"
            assert params["tools"][0]["function"]["name"] == "get_weather"
            assert params["tool_choice"]["function"]["name"] == "get_weather"

    def test_build_params_xai_empty_reasoning_values_skip_mapping(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openrouter/x-ai/grok-4.1-fast",
                api_key="key",
            )

            request = LLMRequest(
                model="x-ai/grok-4.1-fast",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                extra={"reasoning_effort": "", "reasoning_enabled": "maybe"},
            )

            params = provider._build_params(request)

            assert "extra_body" not in params

    def test_reasoning_helpers_cover_non_boolean_cases(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "openrouter/x-ai/grok-4.1-fast",
                api_key="key",
            )

            assert provider._coerce_reasoning_enabled(1) is True
            assert provider._coerce_reasoning_enabled("enabled") is True
            assert provider._coerce_reasoning_enabled("disabled") is False
            assert provider._coerce_reasoning_enabled(object()) is None
            assert provider._reasoning_enabled_from_effort(None) is None
            assert provider._reasoning_enabled_from_effort("") is None
            assert provider._reasoning_enabled_from_effort("OFF") is False
            assert provider._reasoning_enabled_from_effort("high") is True


def test_openrouter_stepfun_profile_disables_json_output_modes() -> None:
    from penguiflow.llm.profiles.openrouter import get_openrouter_profile

    profile = get_openrouter_profile("openrouter/stepfun/step-3.5-flash")
    assert profile.supports_schema_guided_output is False
    assert profile.supports_json_only_output is False


def test_openrouter_xai_profile_supports_reasoning() -> None:
    from penguiflow.llm.profiles.openrouter import get_openrouter_profile

    profile = get_openrouter_profile("openrouter/x-ai/grok-4.1-fast:thinking")
    assert profile.supports_reasoning is True
    assert profile.max_context_tokens == 2000000


def test_openrouter_anthropic_profile_fallback_match() -> None:
    from penguiflow.llm.profiles.openrouter import get_openrouter_profile

    profile = get_openrouter_profile("openrouter/anthropic/claude-sonnet-4-5-20260101")
    assert profile.supports_schema_guided_output is True
    assert profile.supports_tools is True


def test_openrouter_google_profile_fallback_match() -> None:
    from penguiflow.llm.profiles.openrouter import get_openrouter_profile

    profile = get_openrouter_profile("openrouter/google/gemini-2.5-pro-preview")
    assert profile.supports_schema_guided_output is True
    assert profile.supports_tools is True


def test_openrouter_stepfun_profile_fallback_match() -> None:
    from penguiflow.llm.profiles.openrouter import get_openrouter_profile

    profile = get_openrouter_profile("openrouter/stepfun/step-3.5-flash:online")
    assert profile.supports_schema_guided_output is False
    assert profile.supports_json_only_output is False


class TestOpenRouterProviderComplete:
    """Test OpenRouter provider complete method."""

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
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            mock_response = self._create_mock_response("Hello from OpenRouter!")
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(request)

            assert response.message.text == "Hello from OpenRouter!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(self, mock_openai_sdk: MagicMock) -> None:
        """Test completion with tool calls."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            mock_response = self._create_mock_response(
                content="",
                tool_calls=[{"id": "call_123", "name": "get_weather", "arguments": '{"city": "NYC"}'}],
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Weather?")]),),
            )

            response = await provider.complete(request)

            assert len(response.message.parts) == 1
            assert isinstance(response.message.parts[0], ToolCallPart)
            assert response.message.parts[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_complete_timeout(self, mock_openai_sdk: MagicMock) -> None:
        """Test timeout handling."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=TimeoutError())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMTimeoutError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(self, mock_openai_sdk: MagicMock) -> None:
        """Test cancellation handling."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.CancelledError())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(self, mock_openai_sdk: MagicMock) -> None:
        """Test early cancellation via cancel token."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            mock_openai_sdk.AsyncOpenAI.return_value = MagicMock()

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            cancel_token = MagicMock()
            cancel_token.is_cancelled.return_value = True

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request, cancel=cancel_token)

    @pytest.mark.asyncio
    async def test_complete_maps_unhandled_exception(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=ValueError("boom"))
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider("anthropic/claude-sonnet-4.5", api_key="key")
            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )
            mapped = LLMError(message="mapped", provider="openrouter")

            with patch.object(provider, "_map_error", return_value=mapped) as mock_map:
                with pytest.raises(LLMError, match="mapped"):
                    await provider.complete(request)

            mock_map.assert_called_once()


class TestOpenRouterProviderStreaming:
    """Test OpenRouter provider streaming."""

    @pytest.mark.asyncio
    async def test_streaming_text(self, mock_openai_sdk: MagicMock) -> None:
        """Test streaming text completion."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

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

            async def async_gen():
                for chunk in chunks:
                    yield chunk

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=async_gen())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            streamed_text: list[str] = []

            def on_stream(event: StreamEvent) -> None:
                if event.delta_text:
                    streamed_text.append(event.delta_text)

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(
                request,
                stream=True,
                on_stream_event=on_stream,
            )

            assert "".join(streamed_text) == "Hello world!"
            assert response.message.text == "Hello world!"

    @pytest.mark.asyncio
    async def test_streaming_accumulates_tool_calls_and_reasoning(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            first = MagicMock()
            first.choices = [MagicMock()]
            first.choices[0].delta = MagicMock()
            first.choices[0].delta.content = None
            first.choices[0].delta.reasoning = "Reasoning A"
            first.choices[0].delta.tool_calls = [
                SimpleNamespace(
                    index=0,
                    id="call_1",
                    function=SimpleNamespace(name="search", arguments='{"q": "hel'),
                )
            ]
            first.choices[0].finish_reason = None

            second = MagicMock()
            second.choices = [MagicMock()]
            second.choices[0].delta = MagicMock()
            second.choices[0].delta.content = None
            second.choices[0].delta.reasoning = " Reasoning B"
            second.choices[0].delta.tool_calls = [
                SimpleNamespace(
                    index=0,
                    id=None,
                    function=SimpleNamespace(name=None, arguments='lo"}'),
                )
            ]
            second.choices[0].finish_reason = "tool_calls"

            usage_chunk = MagicMock()
            usage_chunk.choices = []
            usage_chunk.usage = MagicMock()
            usage_chunk.usage.prompt_tokens = 2
            usage_chunk.usage.completion_tokens = 3
            usage_chunk.usage.total_tokens = 5

            async def async_gen():
                yield first
                yield second
                yield usage_chunk

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=async_gen())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider("anthropic/claude-sonnet-4.5", api_key="key")

            streamed_reasoning: list[str] = []

            def on_stream(event: StreamEvent) -> None:
                if event.delta_reasoning:
                    streamed_reasoning.append(event.delta_reasoning)

            request = LLMRequest(
                model="anthropic/claude-sonnet-4.5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Tools?")]),),
            )

            response = await provider.complete(
                request,
                stream=True,
                on_stream_event=on_stream,
            )

            assert streamed_reasoning == ["Reasoning A", " Reasoning B"]
            assert len(response.message.parts) == 1
            assert isinstance(response.message.parts[0], ToolCallPart)
            assert response.message.parts[0].name == "search"
            assert response.message.parts[0].arguments_json == '{"q": "hello"}'
            assert response.reasoning_content == "Reasoning A Reasoning B"

    @pytest.mark.asyncio
    async def test_streaming_cancel_token_raises_cancelled(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = "x"
            chunk.choices[0].delta.tool_calls = None
            chunk.choices[0].finish_reason = None

            async def async_gen():
                yield chunk

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=async_gen())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = OpenRouterProvider("anthropic/claude-sonnet-4.5", api_key="key")
            cancel = MagicMock()
            cancel.is_cancelled.return_value = True

            with pytest.raises(LLMCancelledError):
                await provider._stream_completion({}, lambda _event: None, 10.0, cancel)


class TestOpenRouterProviderErrorMapping:
    """Test OpenRouter provider error mapping."""

    def test_map_unknown_error_without_sdk_imports(self, mock_openai_sdk: MagicMock) -> None:
        """Test error mapping falls back to generic error when SDK classes unavailable."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.errors import LLMError
            from penguiflow.llm.providers.openrouter import OpenRouterProvider

            provider = OpenRouterProvider(
                "anthropic/claude-sonnet-4.5",
                api_key="key",
            )

            # Test via the mocked path
            with patch.object(provider, "_map_error") as mock_map:
                mock_map.return_value = LLMError(message="Unknown error", provider="openrouter")
                result = mock_map(ValueError("Unknown error"))

                assert isinstance(result, LLMError)
                assert "Unknown error" in result.message

    def _fake_openai_errors(self) -> Any:
        class APIConnectionError(Exception):
            pass

        class APIStatusError(Exception):
            def __init__(self, message: str, status_code: int = 500) -> None:
                super().__init__(message)
                self.status_code = status_code

        class AuthenticationError(Exception):
            pass

        class BadRequestError(Exception):
            pass

        class RateLimitError(Exception):
            pass

        return SimpleNamespace(
            APIConnectionError=APIConnectionError,
            APIStatusError=APIStatusError,
            AuthenticationError=AuthenticationError,
            BadRequestError=BadRequestError,
            RateLimitError=RateLimitError,
        )

    def test_map_error_auth_rate_limit_bad_request_and_connection(self) -> None:
        from penguiflow.llm.providers.openrouter import OpenRouterProvider

        fake_openai = self._fake_openai_errors()
        provider = object.__new__(OpenRouterProvider)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            auth = provider._map_error(fake_openai.AuthenticationError("no auth"))
            rate = provider._map_error(fake_openai.RateLimitError("slow down"))
            context = provider._map_error(fake_openai.BadRequestError("context length exceeded"))
            invalid = provider._map_error(fake_openai.BadRequestError("bad request"))
            conn = provider._map_error(fake_openai.APIConnectionError("network"))

        assert isinstance(auth, LLMAuthError)
        assert isinstance(rate, LLMRateLimitError)
        assert isinstance(context, LLMContextLengthError)
        assert isinstance(invalid, LLMInvalidRequestError)
        assert isinstance(conn, LLMServerError)

    def test_map_error_api_status_and_fallback(self) -> None:
        from penguiflow.llm.providers.openrouter import OpenRouterProvider

        fake_openai = self._fake_openai_errors()
        provider = object.__new__(OpenRouterProvider)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            server = provider._map_error(fake_openai.APIStatusError("bad gateway", status_code=502))
            client = provider._map_error(fake_openai.APIStatusError("bad input", status_code=422))
            unknown = provider._map_error(ValueError("unexpected"))

        assert isinstance(server, LLMServerError)
        assert isinstance(client, LLMInvalidRequestError)
        assert isinstance(unknown, LLMError)

    def test_map_error_handles_openai_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        from penguiflow.llm.providers.openrouter import OpenRouterProvider

        provider = object.__new__(OpenRouterProvider)
        real_import = builtins.__import__

        def _raising_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "openai":
                raise ImportError("missing openai")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _raising_import)
        result = provider._map_error(ValueError("generic"))
        assert isinstance(result, LLMError)
