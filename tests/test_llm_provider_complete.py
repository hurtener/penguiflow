"""Tests for provider complete() methods and error handling."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMCancelledError,
    LLMTimeoutError,
)
from penguiflow.llm.types import (
    LLMMessage,
    LLMRequest,
    StreamEvent,
    TextPart,
)


class TestOpenAIProviderComplete:
    """Test OpenAI provider complete method and error mapping."""

    @pytest.fixture
    def mock_openai_sdk(self) -> MagicMock:
        """Create mock OpenAI SDK."""
        mock = MagicMock()
        mock.AsyncOpenAI = MagicMock
        return mock

    def _create_mock_response(
        self,
        content: str = "Hello!",
        tool_calls: list[dict[str, Any]] | None = None,
        finish_reason: str = "stop",
        input_tokens: int = 10,
        output_tokens: int = 5,
    ) -> MagicMock:
        """Create a mock OpenAI response."""
        mock_msg = MagicMock()
        mock_msg.content = content
        mock_msg.tool_calls = None
        mock_msg.reasoning_content = None

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
        mock_usage.prompt_tokens = input_tokens
        mock_usage.completion_tokens = output_tokens
        mock_usage.total_tokens = input_tokens + output_tokens

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        return mock_response

    @pytest.mark.asyncio
    async def test_complete_simple_text(self) -> None:
        """Test simple text completion."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            # Setup mock
            mock_response = self._create_mock_response("Hello from OpenAI!")
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
            provider._timeout = 60.0
            provider._client = mock_client

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(request)

            assert response.message.text == "Hello from OpenAI!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(self) -> None:
        """Test completion with tool calls."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            mock_response = self._create_mock_response(
                content="",
                tool_calls=[
                    {"id": "call_123", "name": "get_weather", "arguments": '{"city": "NYC"}'}
                ],
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
            provider._timeout = 60.0
            provider._client = mock_client

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Weather?")]),),
            )

            response = await provider.complete(request)

            from penguiflow.llm.types import ToolCallPart

            assert len(response.message.parts) == 1
            assert isinstance(response.message.parts[0], ToolCallPart)
            assert response.message.parts[0].name == "get_weather"
            assert response.message.parts[0].call_id == "call_123"

    @pytest.mark.asyncio
    async def test_complete_timeout(self) -> None:
        """Test timeout handling."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=TimeoutError("timeout"))

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
            provider._timeout = 60.0
            provider._client = mock_client

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMTimeoutError) as exc_info:
                await provider.complete(request)

            assert "timed out" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_complete_cancelled(self) -> None:
        """Test cancellation handling."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=asyncio.CancelledError()
            )

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
            provider._timeout = 60.0
            provider._client = mock_client

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(self) -> None:
        """Test early cancellation via cancel token."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
            provider._timeout = 60.0
            provider._client = MagicMock()

            cancel_token = MagicMock()
            cancel_token.is_cancelled.return_value = True

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request, cancel=cancel_token)


class TestOpenAIProviderErrorMapping:
    """Test OpenAI provider error mapping."""

    def _create_provider(self) -> Any:
        """Create a provider for testing error mapping."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock()
            return provider

    def test_map_unknown_error(self) -> None:
        """Test mapping unknown error."""
        provider = self._create_provider()

        exc = ValueError("Unknown error")

        from penguiflow.llm.errors import LLMError

        result = provider._map_error(exc)
        assert isinstance(result, LLMError)
        assert "Unknown error" in result.message

    def test_map_error_import_failure(self) -> None:
        """Test error mapping when openai import fails."""
        provider = self._create_provider()

        exc = RuntimeError("Some runtime error")

        # With no openai module, should return generic LLMError
        from penguiflow.llm.errors import LLMError

        result = provider._map_error(exc)
        assert isinstance(result, LLMError)
        assert "Some runtime error" in result.message


class TestAnthropicProviderComplete:
    """Test Anthropic provider complete method."""

    def _create_mock_response(
        self,
        content: str = "Hello from Claude!",
        tool_use: dict[str, Any] | None = None,
        stop_reason: str = "end_turn",
    ) -> MagicMock:
        """Create a mock Anthropic response."""
        mock_content = []

        if content:
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = content
            mock_content.append(text_block)

        if tool_use:
            tool_block = MagicMock()
            tool_block.type = "tool_use"
            tool_block.id = tool_use["id"]
            tool_block.name = tool_use["name"]
            tool_block.input = tool_use["input"]
            mock_content.append(tool_block)

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5

        mock_response = MagicMock()
        mock_response.content = mock_content
        mock_response.usage = mock_usage
        mock_response.stop_reason = stop_reason

        return mock_response

    @pytest.mark.asyncio
    async def test_complete_simple_text(self) -> None:
        """Test simple text completion."""
        with patch.dict("sys.modules", {"anthropic": MagicMock()}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_response = self._create_mock_response("Hello from Claude!")
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            provider = AnthropicProvider.__new__(AnthropicProvider)
            provider._model = "claude-3-5-sonnet"
            provider._profile = MagicMock()
            provider._timeout = 60.0
            provider._default_max_tokens = 8192
            provider._client = mock_client

            request = LLMRequest(
                model="claude-3-5-sonnet",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(request)

            assert response.message.text == "Hello from Claude!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_use(self) -> None:
        """Test completion with tool use."""
        with patch.dict("sys.modules", {"anthropic": MagicMock()}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            mock_response = self._create_mock_response(
                content="",
                tool_use={
                    "id": "toolu_123",
                    "name": "get_weather",
                    "input": {"city": "NYC"},
                },
            )
            mock_client = MagicMock()
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            provider = AnthropicProvider.__new__(AnthropicProvider)
            provider._model = "claude-3-5-sonnet"
            provider._profile = MagicMock()
            provider._timeout = 60.0
            provider._default_max_tokens = 8192
            provider._client = mock_client

            request = LLMRequest(
                model="claude-3-5-sonnet",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Weather?")]),),
            )

            response = await provider.complete(request)

            from penguiflow.llm.types import ToolCallPart

            assert len(response.message.parts) == 1
            assert isinstance(response.message.parts[0], ToolCallPart)
            assert response.message.parts[0].name == "get_weather"
            assert response.message.parts[0].call_id == "toolu_123"


class TestAnthropicProviderMessageConversion:
    """Test Anthropic message conversion."""

    def _create_provider(self) -> Any:
        """Create a provider for testing."""
        with patch.dict("sys.modules", {"anthropic": MagicMock()}):
            from penguiflow.llm.providers.anthropic import AnthropicProvider

            provider = AnthropicProvider.__new__(AnthropicProvider)
            provider._model = "claude-3-5-sonnet"
            return provider

    def test_convert_system_message(self) -> None:
        """Test system message extraction."""
        provider = self._create_provider()

        messages = [
            LLMMessage(role="system", parts=[TextPart(text="You are helpful")]),
            LLMMessage(role="user", parts=[TextPart(text="Hello")]),
        ]

        system_text, converted = provider._to_anthropic_messages(messages)

        assert system_text == "You are helpful"
        assert len(converted) == 1
        assert converted[0]["role"] == "user"

    def test_convert_image_message(self) -> None:
        """Test image message conversion."""
        from penguiflow.llm.types import ImagePart

        provider = self._create_provider()

        messages = [
            LLMMessage(
                role="user",
                parts=[
                    TextPart(text="Describe this:"),
                    ImagePart(data=b"fake_image", media_type="image/png"),
                ],
            ),
        ]

        system_text, converted = provider._to_anthropic_messages(messages)

        assert system_text is None
        assert len(converted) == 1
        assert len(converted[0]["content"]) == 2
        assert converted[0]["content"][0]["type"] == "text"
        assert converted[0]["content"][1]["type"] == "image"

    def test_convert_tool_result(self) -> None:
        """Test tool result message conversion."""
        from penguiflow.llm.types import ToolResultPart

        provider = self._create_provider()

        messages = [
            LLMMessage(
                role="tool",
                parts=[
                    ToolResultPart(
                        name="get_weather",
                        call_id="call_123",
                        result_json='{"temp": 72}',
                    )
                ],
            ),
        ]

        system_text, converted = provider._to_anthropic_messages(messages)

        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        assert converted[0]["content"][0]["type"] == "tool_result"
        assert converted[0]["content"][0]["tool_use_id"] == "call_123"


class TestOpenAIStreamingComplete:
    """Test OpenAI streaming completion."""

    @pytest.mark.asyncio
    async def test_streaming_complete_text(self) -> None:
        """Test streaming text completion."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            # Create mock streaming chunks
            chunks = []

            # Text chunks
            for text in ["Hello", " ", "world", "!"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta = MagicMock()
                chunk.choices[0].delta.content = text
                chunk.choices[0].delta.tool_calls = None
                chunk.choices[0].finish_reason = None
                chunks.append(chunk)

            # Final chunk with finish reason
            final_chunk = MagicMock()
            final_chunk.choices = [MagicMock()]
            final_chunk.choices[0].delta = MagicMock()
            final_chunk.choices[0].delta.content = None
            final_chunk.choices[0].delta.tool_calls = None
            final_chunk.choices[0].finish_reason = "stop"
            chunks.append(final_chunk)

            # Usage chunk (no choices)
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

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
            provider._timeout = 60.0
            provider._client = mock_client

            streamed_text = []

            def on_stream(event: StreamEvent) -> None:
                if event.delta_text:
                    streamed_text.append(event.delta_text)

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(
                request,
                stream=True,
                on_stream_event=on_stream,
            )

            assert "".join(streamed_text) == "Hello world!"
            assert response.message.text == "Hello world!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 4


class TestProviderBuildParams:
    """Test provider parameter building."""

    def test_openai_build_params_with_tools(self) -> None:
        """Test OpenAI parameter building with tools."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider
            from penguiflow.llm.types import ToolSpec

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                tools=(
                    ToolSpec(
                        name="get_weather",
                        description="Get weather",
                        json_schema={"type": "object"},
                    ),
                ),
                tool_choice="get_weather",
                temperature=0.7,
                max_tokens=100,
            )

            params = provider._build_params(request)

            assert params["model"] == "gpt-4o"
            assert params["temperature"] == 0.7
            assert params["max_tokens"] == 100
            assert "tools" in params
            assert params["tool_choice"]["function"]["name"] == "get_weather"

    def test_openai_build_params_with_structured_output(self) -> None:
        """Test OpenAI parameter building with structured output."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider
            from penguiflow.llm.types import StructuredOutputSpec

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                structured_output=StructuredOutputSpec(
                    name="MySchema",
                    json_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                    strict=True,
                ),
            )

            params = provider._build_params(request)

            assert "response_format" in params
            assert params["response_format"]["type"] == "json_schema"


class TestProviderValidation:
    """Test provider request validation."""

    def test_openai_validate_request_success(self) -> None:
        """Test successful request validation."""
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            from penguiflow.llm.providers.openai import OpenAIProvider

            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider._model = "gpt-4o"
            provider._profile = MagicMock()

            request = LLMRequest(
                model="gpt-4o",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            # Should not raise
            provider.validate_request(request)

    def test_base_validate_request_is_noop(self) -> None:
        """Test base class validation is a no-op by default."""
        from penguiflow.llm.providers.base import Provider

        # Create a concrete test provider
        class TestProvider(Provider):
            @property
            def provider_name(self) -> str:
                return "test"

            @property
            def profile(self) -> Any:
                return MagicMock()

            @property
            def model(self) -> str:
                return "test-model"

            async def complete(self, request: Any, **kwargs: Any) -> Any:
                pass

        provider = TestProvider()

        request = LLMRequest(
            model="test-model",
            messages=(),  # Empty messages - base class doesn't validate
        )

        # Base class validate_request is a no-op, should not raise
        provider.validate_request(request)
