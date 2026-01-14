"""Tests for Google/Gemini provider initialization and configuration.

Tests use gemini-2.5-flash as the default model (recommended for general use).
The provider supports Gemini 2.5/3.0 model families with the google-genai SDK v1.57+.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMError,
    LLMRateLimitError,
)
from penguiflow.llm.types import (
    LLMMessage,
    LLMRequest,
    StructuredOutputSpec,
    TextPart,
)


class TestGoogleProviderInit:
    """Test Google provider initialization."""

    def test_init_with_api_key(self) -> None:
        """Test initialization with explicit API key."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test-api-key")

            assert provider.model == "gemini-2.5-flash"
            assert provider.provider_name == "google"
            # Provider should have created a client
            assert provider._client is not None

    def test_init_with_timeout(self) -> None:
        """Test initialization with custom timeout."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test", timeout=120.0)

            assert provider._timeout == 120.0

    def test_init_uses_env_var(self) -> None:
        """Test initialization uses GOOGLE_API_KEY env var."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            with patch.dict(os.environ, {"GOOGLE_API_KEY": "env-api-key"}):
                provider = GoogleProvider("gemini-2.5-flash")
                # Provider should have created a client
                assert provider._client is not None

    def test_init_with_custom_profile(self) -> None:
        """Test initialization with custom model profile."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.profiles import ModelProfile
            from penguiflow.llm.providers.google import GoogleProvider

            custom_profile = ModelProfile(
                supports_tools=True,
                supports_schema_guided_output=True,
                max_output_tokens=4096,
            )
            provider = GoogleProvider("gemini-2.5-flash", api_key="test", profile=custom_profile)

            assert provider.profile is custom_profile

    def test_provider_properties(self) -> None:
        """Test provider property accessors."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            assert provider.provider_name == "google"
            assert provider.model == "gemini-2.5-flash"
            assert provider.profile is not None


class TestGoogleProviderBuildConfig:
    """Test Google provider configuration building."""

    def test_build_config_basic(self) -> None:
        """Test basic configuration building."""
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            request = LLMRequest(
                model="gemini-2.5-flash",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                temperature=0.7,
            )

            provider._build_config(request)

            mock_types.GenerateContentConfig.assert_called_once()

    def test_build_config_with_max_tokens(self) -> None:
        """Test configuration building with max tokens."""
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            request = LLMRequest(
                model="gemini-2.5-flash",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                max_tokens=500,
            )

            provider._build_config(request)

            call_kwargs = mock_types.GenerateContentConfig.call_args[1]
            assert call_kwargs["max_output_tokens"] == 500

    def test_build_config_structured_output_uses_response_json_schema(self) -> None:
        """Structured output should use response_json_schema to avoid Schema coercion issues."""
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            request = LLMRequest(
                model="gemini-2.5-flash",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                structured_output=StructuredOutputSpec(
                    name="test_schema",
                    json_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                    },
                    strict=True,
                ),
            )

            provider._build_config(request)

            call_kwargs = mock_types.GenerateContentConfig.call_args[1]
            assert call_kwargs["response_mime_type"] == "application/json"
            assert "response_json_schema" in call_kwargs
            assert "response_schema" not in call_kwargs

    def test_build_config_reasoning_effort_does_not_set_level_and_budget(self) -> None:
        """Gemini config rejects setting both thinking_budget and thinking_level."""
        from penguiflow.llm.providers.google import GoogleProvider

        provider = GoogleProvider.__new__(GoogleProvider)

        request = LLMRequest(
            model="gemini-2.5-flash",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            max_tokens=4096,
            extra={"reasoning_effort": "medium"},
        )

        cfg = provider._build_config(request)  # type: ignore[attr-defined]
        assert cfg.thinking_config is not None
        # Only one of these may be set.
        assert not (cfg.thinking_config.thinking_budget and cfg.thinking_config.thinking_level)


class TestGoogleProviderComplete:
    """Test Google provider complete method."""

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(self) -> None:
        """Test early cancellation via cancel token."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            cancel_token = MagicMock()
            cancel_token.is_cancelled.return_value = True

            request = LLMRequest(
                model="gemini-2.5-flash",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request, cancel=cancel_token)


class TestGoogleProviderErrorMapping:
    """Test Google provider error mapping."""

    def test_map_auth_error(self) -> None:
        """Test mapping authentication error."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            exc = ValueError("Invalid API key provided")
            result = provider._map_error(exc)

            assert isinstance(result, LLMAuthError)

    def test_map_rate_limit_error(self) -> None:
        """Test mapping rate limit error."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            exc = ValueError("Rate limit exceeded for quota bucket")
            result = provider._map_error(exc)

            assert isinstance(result, LLMRateLimitError)

    def test_map_unknown_error(self) -> None:
        """Test mapping unknown error."""
        mock_genai = MagicMock()
        mock_genai.types = MagicMock()

        with patch.dict("sys.modules", {"google": MagicMock(), "google.genai": mock_genai}):
            from penguiflow.llm.providers.google import GoogleProvider

            provider = GoogleProvider("gemini-2.5-flash", api_key="test")

            exc = ValueError("Something unexpected")
            result = provider._map_error(exc)

            assert isinstance(result, LLMError)
            assert "Something unexpected" in result.message
