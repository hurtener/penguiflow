"""Tests for the LLM providers module."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from penguiflow.llm.providers.base import OpenAICompatibleProvider
from penguiflow.llm.types import (
    ImagePart,
    LLMMessage,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)


# Create a concrete implementation of OpenAICompatibleProvider for testing
class MockOpenAIProvider(OpenAICompatibleProvider):
    """Mock provider for testing base class methods."""

    def __init__(self, model: str = "test-model") -> None:
        self._model = model

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def profile(self) -> Any:
        from penguiflow.llm.profiles import get_profile

        return get_profile(self._model)

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        request: Any,
        *,
        timeout_s: float | None = None,
        cancel: Any | None = None,
        stream: bool = False,
        on_stream_event: Any | None = None,
    ) -> Any:
        raise NotImplementedError("Use mocked SDK")


class TestOpenAICompatibleProviderConversions:
    """Test OpenAI-compatible provider message conversions."""

    def test_to_openai_messages_simple_text(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(role="user", parts=[TextPart(text="Hello")])
        ]

        result = provider._to_openai_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_to_openai_messages_multipart(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(
                role="user",
                parts=[
                    TextPart(text="Check this image:"),
                    ImagePart(data=b"fake_image_data", media_type="image/png"),
                ],
            )
        ]

        result = provider._to_openai_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert isinstance(result[0]["content"], list)
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][1]["type"] == "image_url"
        assert "data:image/png;base64," in result[0]["content"][1]["image_url"]["url"]

    def test_to_openai_messages_with_tool_calls(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(
                role="assistant",
                parts=[
                    TextPart(text="Let me call a tool"),
                    ToolCallPart(
                        name="get_weather",
                        arguments_json='{"city": "NYC"}',
                        call_id="call_123",
                    ),
                ],
            )
        ]

        result = provider._to_openai_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "tool_calls" in result[0]
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "call_123"
        assert result[0]["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_to_openai_messages_tool_result(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(
                role="tool",
                parts=[
                    ToolResultPart(
                        name="get_weather",
                        call_id="call_123",
                        result_json='{"temperature": 72}',
                    )
                ],
            )
        ]

        result = provider._to_openai_messages(messages)

        # Multiple tool results may create multiple messages
        assert len(result) >= 1
        assert result[0]["role"] == "tool"
        assert result[0].get("tool_call_id") == "call_123"
        assert result[0]["content"] == '{"temperature": 72}'

    def test_to_openai_tools(self) -> None:
        from penguiflow.llm.types import ToolSpec

        provider = MockOpenAIProvider()
        tools = [
            ToolSpec(
                name="get_weather",
                description="Get weather for a city",
                json_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            )
        ]

        result = provider._to_openai_tools(tools)

        assert result is not None
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "get_weather"
        assert result[0]["function"]["description"] == "Get weather for a city"

    def test_to_openai_tools_none(self) -> None:
        provider = MockOpenAIProvider()

        result = provider._to_openai_tools(None)

        assert result is None

    def test_to_openai_response_format(self) -> None:
        from penguiflow.llm.types import StructuredOutputSpec

        provider = MockOpenAIProvider()
        structured_output = StructuredOutputSpec(
            name="my_schema",
            json_schema={"type": "object"},
            strict=True,
        )

        result = provider._to_openai_response_format(structured_output)

        assert result is not None
        assert result["type"] == "json_schema"
        assert result["json_schema"]["name"] == "my_schema"
        assert result["json_schema"]["strict"] is True

    def test_to_openai_response_format_none(self) -> None:
        provider = MockOpenAIProvider()

        result = provider._to_openai_response_format(None)

        assert result is None

    def test_from_openai_response(self) -> None:
        provider = MockOpenAIProvider()

        # Create mock response
        mock_msg = MagicMock()
        mock_msg.content = "Hello back!"
        mock_msg.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_msg

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        message, usage = provider._from_openai_response(mock_response)

        assert message.role == "assistant"
        assert len(message.parts) == 1
        assert isinstance(message.parts[0], TextPart)
        assert message.parts[0].text == "Hello back!"
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5
        assert usage.total_tokens == 15

    def test_from_openai_response_with_tool_calls(self) -> None:
        provider = MockOpenAIProvider()

        # Create mock tool call
        mock_func = MagicMock()
        mock_func.name = "get_weather"
        mock_func.arguments = '{"city": "NYC"}'

        mock_tool_call = MagicMock()
        mock_tool_call.id = "call_456"
        mock_tool_call.function = mock_func

        mock_msg = MagicMock()
        mock_msg.content = None
        mock_msg.tool_calls = [mock_tool_call]

        mock_choice = MagicMock()
        mock_choice.message = mock_msg

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        message, usage = provider._from_openai_response(mock_response)

        assert len(message.parts) == 1
        assert isinstance(message.parts[0], ToolCallPart)
        assert message.parts[0].name == "get_weather"
        assert message.parts[0].call_id == "call_456"


class TestProviderFactory:
    """Test provider factory function."""

    def test_create_openai_provider(self) -> None:
        """Test creating OpenAI provider."""
        # Mock the openai module
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            from penguiflow.llm.providers import create_provider

            provider = create_provider("gpt-4o", api_key="test-key")

            assert provider.model == "gpt-4o"
            assert provider.provider_name == "openai"

    def test_create_anthropic_provider(self) -> None:
        """Test creating Anthropic provider."""
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
            from penguiflow.llm.providers import create_provider

            provider = create_provider("claude-3-5-sonnet-20241022", api_key="test-key")

            assert provider.model == "claude-3-5-sonnet-20241022"
            assert provider.provider_name == "anthropic"

    def test_create_google_provider(self) -> None:
        """Test creating Google provider."""
        mock_genai = MagicMock()
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.dict(sys.modules, {"google.genai": mock_genai, "google": MagicMock()}):
            from penguiflow.llm.providers import create_provider

            provider = create_provider("gemini-2.0-flash", api_key="test-key")

            assert provider.model == "gemini-2.0-flash"
            assert provider.provider_name == "google"

    def test_create_nim_provider_from_nim_prefix(self) -> None:
        """Test creating NIM provider with nim/ prefix."""
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            from penguiflow.llm.providers import create_provider

            provider = create_provider("nim/qwen/qwen3.5-397b-a17b", api_key="test-key")

            assert provider.model == "qwen/qwen3.5-397b-a17b"
            assert provider.provider_name == "nim"

    def test_create_nim_provider_from_nvidia_prefix(self) -> None:
        """Test creating NIM provider with nvidia/ alias prefix."""
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            from penguiflow.llm.providers import create_provider

            provider = create_provider("nvidia/qwen/qwen3.5-397b-a17b", api_key="test-key")

            assert provider.model == "qwen/qwen3.5-397b-a17b"
            assert provider.provider_name == "nim"

    def test_create_openrouter_provider(self) -> None:
        """Test creating OpenRouter provider."""
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            from penguiflow.llm.providers import create_provider

            provider = create_provider("openrouter/openai/gpt-4o", api_key="test-key")

            assert "gpt-4o" in provider.model
            assert provider.provider_name == "openrouter"

    def test_create_databricks_provider(self) -> None:
        """Test creating Databricks provider."""
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            # Test direct instantiation with explicit args (factory uses env vars)
            provider = DatabricksProvider(
                model="databricks-meta-llama-3-1-405b-instruct",
                token="test-token",
                host="https://my-workspace.cloud.databricks.com/serving-endpoints",
            )

            assert "meta-llama" in provider.model
            assert provider.provider_name == "databricks"


class TestProviderRouting:
    """Test that model names route to correct providers."""

    def test_routing_openai_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("gpt-4o") == "openai"
        assert get_provider_for_model("gpt-4") == "openai"
        assert get_provider_for_model("gpt-3.5-turbo") == "openai"
        assert get_provider_for_model("o1") == "openai"
        assert get_provider_for_model("o3-mini") == "openai"

    def test_routing_anthropic_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("claude-3-5-sonnet-20241022") == "anthropic"
        assert get_provider_for_model("claude-3-opus-20240229") == "anthropic"
        assert get_provider_for_model("claude-4-sonnet") == "anthropic"

    def test_routing_google_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("gemini-2.0-flash") == "google"
        assert get_provider_for_model("gemini-1.5-pro") == "google"
        assert get_provider_for_model("gemini-2.5-pro") == "google"

    def test_routing_openrouter_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("openrouter/openai/gpt-4o") == "openrouter"
        assert get_provider_for_model("openrouter/anthropic/claude-3") == "openrouter"

    def test_routing_databricks_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("databricks/databricks-meta-llama-3-1-405b-instruct") == "databricks"
        assert get_provider_for_model("databricks/my-custom-model") == "databricks"

    def test_routing_bedrock_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("bedrock/anthropic.claude-3-sonnet") == "bedrock"
        assert get_provider_for_model("bedrock/amazon.titan-text") == "bedrock"

    def test_routing_nim_models(self) -> None:
        from penguiflow.llm.routing import get_provider_for_model

        assert get_provider_for_model("nim/qwen/qwen3.5-397b-a17b") == "nim"
        assert get_provider_for_model("nvidia/qwen/qwen3.5-397b-a17b") == "nim"


class TestProviderProfiles:
    """Test that providers use correct model profiles."""

    def test_openai_profile(self) -> None:
        from penguiflow.llm.profiles import get_profile

        profile = get_profile("gpt-4o")
        assert profile is not None
        assert profile.supports_tools is True

    def test_anthropic_profile(self) -> None:
        from penguiflow.llm.profiles import get_profile

        profile = get_profile("claude-3-5-sonnet-20241022")
        assert profile is not None
        assert profile.supports_tools is True

    def test_google_profile(self) -> None:
        from penguiflow.llm.profiles import get_profile

        profile = get_profile("gemini-2.0-flash")
        assert profile is not None

    def test_nim_profile(self) -> None:
        from penguiflow.llm.profiles import get_profile

        profile = get_profile("nim/qwen/qwen3.5-397b-a17b")
        assert profile is not None
        assert profile.default_output_mode == "tools"

    def test_default_profile_for_unknown(self) -> None:
        from penguiflow.llm.profiles import get_profile

        profile = get_profile("unknown-model-xyz")
        # Should return a default profile
        assert profile is not None


class TestProviderValidation:
    """Test provider request validation."""

    def test_databricks_tool_limit(self) -> None:
        """Test that Databricks validates tool count limits."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            provider = DatabricksProvider(
                model="databricks/test-model",
                token="test-token",
                host="https://test.cloud.databricks.com/serving-endpoints",
            )

            # Create request with too many tools
            from penguiflow.llm.types import LLMMessage, LLMRequest, TextPart, ToolSpec

            # Databricks MAX_TOOLS is 32, so we need > 32
            tools = tuple(
                ToolSpec(
                    name=f"tool_{i}",
                    description=f"Tool {i}",
                    json_schema={"type": "object"},
                )
                for i in range(35)  # Exceeds Databricks limit of 32
            )

            request = LLMRequest(
                model="test",
                messages=(LLMMessage(role="user", parts=[TextPart(text="test")]),),
                tools=tools,
            )

            from penguiflow.llm.errors import LLMInvalidRequestError

            with pytest.raises(LLMInvalidRequestError, match="max.*32.*tools"):
                provider.validate_request(request)


class TestImagePartConversion:
    """Test image part handling in message conversion."""

    def test_image_with_detail_setting(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(
                role="user",
                parts=[
                    ImagePart(data=b"test", media_type="image/jpeg", detail="high"),
                ],
            )
        ]

        result = provider._to_openai_messages(messages)

        assert result[0]["content"][0]["image_url"]["detail"] == "high"

    def test_image_default_detail(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(
                role="user",
                parts=[
                    ImagePart(data=b"test", media_type="image/png"),
                ],
            )
        ]

        result = provider._to_openai_messages(messages)

        assert result[0]["content"][0]["image_url"]["detail"] == "auto"


class TestToolCallIdGeneration:
    """Test automatic tool call ID generation."""

    def test_tool_call_without_id(self) -> None:
        provider = MockOpenAIProvider()
        messages = [
            LLMMessage(
                role="assistant",
                parts=[
                    ToolCallPart(
                        name="my_tool",
                        arguments_json="{}",
                        call_id=None,  # No explicit ID
                    ),
                ],
            )
        ]

        result = provider._to_openai_messages(messages)

        # Should generate a unique ID (UUID-based)
        assert "id" in result[0]["tool_calls"][0]
        generated_id = result[0]["tool_calls"][0]["id"]
        assert generated_id.startswith("call_")
        # UUID hex is 32 chars, we use 16 chars
        assert len(generated_id) == len("call_") + 16
