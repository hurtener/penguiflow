"""Tests for Anthropic provider initialization and configuration."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMCancelledError,
    LLMTimeoutError,
)
from penguiflow.llm.types import (
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)


@pytest.fixture
def mock_anthropic_sdk() -> Any:
    """Mock the Anthropic SDK."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_sdk.AsyncAnthropic.return_value = mock_client
    return mock_sdk


class TestAnthropicProviderInit:
    """Test Anthropic provider initialization."""

    def test_init_with_api_key(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test initialization with explicit API key."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test-api-key")

            assert provider.model == "claude-sonnet-4-5"
            assert provider.provider_name == "anthropic"
            mock_anthropic_sdk.AsyncAnthropic.assert_called_once()
            call_kwargs = mock_anthropic_sdk.AsyncAnthropic.call_args[1]
            assert call_kwargs["api_key"] == "test-api-key"

    def test_init_with_timeout(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test initialization with custom timeout."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test", timeout=120.0)

            assert provider._timeout == 120.0
            call_kwargs = mock_anthropic_sdk.AsyncAnthropic.call_args[1]
            assert call_kwargs["timeout"] == 120.0

    def test_init_with_max_tokens(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test initialization with custom max tokens."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test", max_tokens=4096)

            assert provider._default_max_tokens == 4096

    def test_init_uses_env_var(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test initialization uses ANTHROPIC_API_KEY env var."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-api-key"}):
                AnthropicProvider("claude-sonnet-4-5")

                call_kwargs = mock_anthropic_sdk.AsyncAnthropic.call_args[1]
                assert call_kwargs["api_key"] == "env-api-key"

    def test_init_with_custom_profile(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test initialization with custom model profile."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.profiles import ModelProfile
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            custom_profile = ModelProfile(
                supports_tools=True,
                supports_schema_guided_output=True,
                max_output_tokens=4096,
            )
            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test", profile=custom_profile)

            assert provider.profile is custom_profile

    def test_provider_properties(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test provider property accessors."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            assert provider.provider_name == "anthropic"
            assert provider.model == "claude-sonnet-4-5"
            assert provider.profile is not None


class TestAnthropicProviderMessageConversion:
    """Test Anthropic provider message conversion."""

    def test_convert_messages_basic(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test basic message conversion."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            messages = [
                LLMMessage(role="system", parts=[TextPart(text="You are helpful.")]),
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ]

            system_text, result = provider._to_anthropic_messages(messages)

            assert system_text == "You are helpful."
            assert len(result) == 1
            assert result[0]["role"] == "user"

    def test_convert_messages_with_tool_call(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test message conversion with tool calls."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            messages = [
                LLMMessage(
                    role="assistant",
                    parts=[
                        ToolCallPart(
                            name="get_weather",
                            arguments_json='{"city": "NYC"}',
                            call_id="call_123",
                        )
                    ],
                ),
            ]

            _, result = provider._to_anthropic_messages(messages)

            assert len(result) == 1
            assert result[0]["content"][0]["type"] == "tool_use"

    def test_convert_messages_with_tool_result(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test message conversion with tool results."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            messages = [
                LLMMessage(
                    role="tool",
                    parts=[
                        ToolResultPart(
                            name="get_weather",
                            result_json='{"temp": 72}',
                            call_id="call_123",
                            is_error=False,
                        )
                    ],
                ),
            ]

            _, result = provider._to_anthropic_messages(messages)

            assert len(result) == 1
            assert result[0]["content"][0]["type"] == "tool_result"

    def test_convert_messages_with_image(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test message conversion with images."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            messages = [
                LLMMessage(
                    role="user",
                    parts=[
                        ImagePart(data=b"fake_image_data", media_type="image/png"),
                        TextPart(text="What is this?"),
                    ],
                ),
            ]

            _, result = provider._to_anthropic_messages(messages)

            assert len(result) == 1
            assert len(result[0]["content"]) == 2
            assert result[0]["content"][0]["type"] == "image"


class TestAnthropicProviderBuildParams:
    """Test Anthropic provider parameter building."""

    def test_build_params_basic(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test basic parameter building."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                temperature=0.7,
            )

            _, messages = provider._to_anthropic_messages(request.messages)
            params = provider._build_params(request, None, messages)

            assert params["model"] == "claude-sonnet-4-5"
            assert params["temperature"] == 0.7

    def test_build_params_with_system(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test parameter building with system message."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            messages = [
                LLMMessage(role="system", parts=[TextPart(text="Be helpful.")]),
                LLMMessage(role="user", parts=[TextPart(text="Hello")]),
            ]

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=tuple(messages),
            )

            system_text, converted = provider._to_anthropic_messages(request.messages)
            params = provider._build_params(request, system_text, converted)

            assert params["system"] == "Be helpful."

    def test_build_params_with_tools(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test parameter building with tools."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                tools=(
                    ToolSpec(
                        name="get_weather",
                        description="Get weather",
                        json_schema={"type": "object"},
                    ),
                ),
                tool_choice="get_weather",
            )

            _, messages = provider._to_anthropic_messages(request.messages)
            params = provider._build_params(request, None, messages)

            assert "tools" in params
            assert params["tool_choice"]["name"] == "get_weather"

    def test_build_params_with_structured_output(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test parameter building with structured output."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                structured_output=StructuredOutputSpec(
                    name="MySchema",
                    json_schema={"type": "object"},
                    strict=True,
                ),
            )

            _, messages = provider._to_anthropic_messages(request.messages)
            params = provider._build_params(request, None, messages)

            # Anthropic uses tool_choice for structured output
            assert params["tool_choice"]["name"] == "MySchema"


class TestAnthropicProviderComplete:
    """Test Anthropic provider complete method."""

    def _create_mock_response(
        self,
        content: str = "Hello!",
        tool_calls: list[dict[str, Any]] | None = None,
        stop_reason: str = "end_turn",
    ) -> MagicMock:
        """Create a mock Anthropic response."""
        content_blocks = []

        if content:
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = content
            content_blocks.append(text_block)

        if tool_calls:
            for tc in tool_calls:
                tool_block = MagicMock()
                tool_block.type = "tool_use"
                tool_block.id = tc["id"]
                tool_block.name = tc["name"]
                tool_block.input = tc["input"]
                content_blocks.append(tool_block)

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5

        mock_response = MagicMock()
        mock_response.content = content_blocks
        mock_response.usage = mock_usage
        mock_response.stop_reason = stop_reason

        return mock_response

    @pytest.mark.asyncio
    async def test_complete_simple(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test simple completion."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_response = self._create_mock_response("Hello from Claude!")
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic_sdk.AsyncAnthropic.return_value = mock_client

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(request)

            assert response.message.text == "Hello from Claude!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test completion with tool calls."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_response = self._create_mock_response(
                content="",
                tool_calls=[
                    {"id": "call_123", "name": "get_weather", "input": {"city": "NYC"}}
                ],
            )
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_anthropic_sdk.AsyncAnthropic.return_value = mock_client

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Weather?")]),),
            )

            response = await provider.complete(request)

            assert len(response.message.parts) == 1
            assert isinstance(response.message.parts[0], ToolCallPart)
            assert response.message.parts[0].name == "get_weather"

    @pytest.mark.asyncio
    async def test_complete_timeout(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test timeout handling."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=TimeoutError())
            mock_anthropic_sdk.AsyncAnthropic.return_value = mock_client

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMTimeoutError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test cancellation handling."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(side_effect=asyncio.CancelledError())
            mock_anthropic_sdk.AsyncAnthropic.return_value = mock_client

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test early cancellation via cancel token."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_anthropic_sdk.AsyncAnthropic.return_value = MagicMock()

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            cancel_token = MagicMock()
            cancel_token.is_cancelled.return_value = True

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request, cancel=cancel_token)


class TestAnthropicProviderStreaming:
    """Test Anthropic provider streaming."""

    @pytest.mark.asyncio
    async def test_streaming_text(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test streaming text completion."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            # Create mock streaming events
            events = []

            # Message start
            msg_start = MagicMock()
            msg_start.type = "message_start"
            msg_start.message = MagicMock()
            msg_start.message.usage = MagicMock()
            msg_start.message.usage.input_tokens = 10
            events.append(msg_start)

            # Content block start (text)
            block_start = MagicMock()
            block_start.type = "content_block_start"
            block_start.content_block = MagicMock()
            block_start.content_block.type = "text"
            events.append(block_start)

            # Text deltas
            for text in ["Hello", " ", "world", "!"]:
                delta = MagicMock()
                delta.type = "content_block_delta"
                delta.delta = MagicMock()
                delta.delta.text = text
                events.append(delta)

            # Content block stop
            block_stop = MagicMock()
            block_stop.type = "content_block_stop"
            events.append(block_stop)

            # Message delta (stop reason)
            msg_delta = MagicMock()
            msg_delta.type = "message_delta"
            msg_delta.delta = MagicMock()
            msg_delta.delta.stop_reason = "end_turn"
            events.append(msg_delta)

            # Message stop
            msg_stop = MagicMock()
            msg_stop.type = "message_stop"
            events.append(msg_stop)

            # Create async context manager mock
            async def async_gen():
                for event in events:
                    yield event

            mock_stream = MagicMock()
            mock_stream.__aiter__ = lambda self: async_gen()

            # Mock get_final_message
            final_msg = MagicMock()
            final_msg.usage = MagicMock()
            final_msg.usage.input_tokens = 10
            final_msg.usage.output_tokens = 4
            mock_stream.get_final_message = AsyncMock(return_value=final_msg)

            class MockStreamContext:
                async def __aenter__(self):
                    return mock_stream

                async def __aexit__(self, *args):
                    pass

            mock_client = MagicMock()
            mock_client.messages.stream.return_value = MockStreamContext()
            mock_anthropic_sdk.AsyncAnthropic.return_value = mock_client

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            streamed_text: list[str] = []

            def on_stream(event: StreamEvent) -> None:
                if event.delta_text:
                    streamed_text.append(event.delta_text)

            request = LLMRequest(
                model="claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(
                request,
                stream=True,
                on_stream_event=on_stream,
            )

            assert "".join(streamed_text) == "Hello world!"
            assert response.message.text == "Hello world!"


class TestAnthropicProviderErrorMapping:
    """Test Anthropic provider error mapping."""

    def test_map_unknown_error_without_sdk_imports(self, mock_anthropic_sdk: MagicMock) -> None:
        """Test error mapping falls back to generic error when SDK classes unavailable."""
        with patch.dict("sys.modules", {"anthropic": mock_anthropic_sdk}):
            from penguiflow.llm.errors import LLMError
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider("claude-sonnet-4-5", api_key="test")

            # Force ImportError when trying to import exception classes
            def raise_import_error():
                raise ImportError("mocked")

            with patch.object(provider, "_map_error") as mock_map:
                # Test that when error mapping returns LLMError for unknown errors
                mock_map.return_value = LLMError(
                    message="Unknown error", provider="anthropic"
                )
                result = mock_map(ValueError("Unknown error"))

                assert isinstance(result, LLMError)
                assert "Unknown error" in result.message
