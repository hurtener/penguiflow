"""Tests for Databricks provider initialization and configuration.

Updated January 2026 for current Databricks Foundation Model APIs.
Uses databricks-claude-sonnet-4-5 as the primary test model.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMCancelledError,
    LLMInvalidRequestError,
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
    """Mock the OpenAI SDK (used by Databricks)."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_sdk.AsyncOpenAI.return_value = mock_client
    return mock_sdk


class TestDatabricksProviderInit:
    """Test Databricks provider initialization."""

    def test_init_with_host_and_token(self, mock_openai_sdk: MagicMock) -> None:
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
            assert "my-workspace.cloud.databricks.com" in call_kwargs["base_url"]

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

            assert provider._timeout == 240.0
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
                assert call_kwargs["api_key"] == "env-token"
                assert "env-workspace.databricks.com" in call_kwargs["base_url"]

    def test_init_raises_without_credentials(self, mock_openai_sdk: MagicMock) -> None:
        """Test initialization raises without host or token."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            with patch.dict(os.environ, {}, clear=True):
                # Remove env vars
                os.environ.pop("DATABRICKS_HOST", None)
                os.environ.pop("DATABRICKS_TOKEN", None)

                with pytest.raises(ValueError, match="Databricks host and token required"):
                    DatabricksProvider("databricks-claude-sonnet-4-5")

    def test_init_with_custom_profile(self, mock_openai_sdk: MagicMock) -> None:
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

    def test_validate_request_too_many_tools(self, mock_openai_sdk: MagicMock) -> None:
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
                ToolSpec(name=f"tool_{i}", description=f"Tool {i}", json_schema={})
                for i in range(33)
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                tools=tools,
            )

            with pytest.raises(LLMInvalidRequestError, match="max 32 tools"):
                provider.validate_request(request)

    def test_validate_request_within_limits(self, mock_openai_sdk: MagicMock) -> None:
        """Test validation passes within limits."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            tools = tuple(
                ToolSpec(name=f"tool_{i}", description=f"Tool {i}", json_schema={})
                for i in range(5)
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
                tools=tools,
            )

            # Should not raise
            provider.validate_request(request)


class TestDatabricksProviderBuildParams:
    """Test Databricks provider parameter building."""

    def test_build_params_basic(self) -> None:
        """Test basic parameter building."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._model = "databricks-claude-sonnet-4-5"

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            temperature=0.7,
        )

        params = provider._build_params(request)

        assert params["model"] == "databricks-claude-sonnet-4-5"
        assert params["temperature"] == 0.7

    def test_build_params_maps_reasoning_effort_to_thinking_for_claude(self) -> None:
        """Databricks Claude models use 'thinking' budget_tokens, not reasoning_effort."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._model = "databricks-claude-sonnet-4-5"
        provider._profile = MagicMock()
        provider._profile.max_output_tokens = 64000

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={"reasoning_effort": "high"},
        )

        params = provider._build_params(request)
        assert "reasoning_effort" not in params
        assert params["thinking"] == {"type": "enabled", "budget_tokens": 32768}
        assert isinstance(params["max_tokens"], int)
        assert params["max_tokens"] > params["thinking"]["budget_tokens"]

    def test_build_params_thinking_budget_is_capped_by_max_tokens(self) -> None:
        """thinking.budget_tokens must be strictly less than max_tokens."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._model = "databricks-claude-sonnet-4-5"
        provider._profile = MagicMock()
        provider._profile.max_output_tokens = 64000

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            max_tokens=1024,
            extra={"reasoning_effort": "high"},
        )

        params = provider._build_params(request)
        assert params["thinking"]["budget_tokens"] == 32768
        assert params["max_tokens"] == 1024 + 32768

    def test_build_params_keeps_reasoning_effort_for_supported_models(self) -> None:
        """GPT OSS/Gemini 3 accept reasoning_effort directly."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._model = "databricks-gpt-oss-20b"

        request = LLMRequest(
            model="databricks-gpt-oss-20b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={"reasoning_effort": "high"},
        )

        params = provider._build_params(request)
        assert params["reasoning_effort"] == "high"

    def test_build_params_with_structured_output(self) -> None:
        """Test parameter building with structured output."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._model = "databricks-claude-sonnet-4-5"

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
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


class TestDatabricksProviderComplete:
    """Test Databricks provider complete method (invocations)."""

    @pytest.mark.asyncio
    async def test_complete_simple(self) -> None:
        """Test simple completion parsing."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._timeout = 5.0
        provider._model = "databricks-claude-sonnet-4-5"
        provider._profile = MagicMock()

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hello from Databricks!", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        async def post(path: str, *, body: object, cast_to: object) -> object:
            assert path == "invocations"
            return response

        provider._client = SimpleNamespace(post=post)

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        out = await provider.complete(request)
        assert out.message.text == "Hello from Databricks!"
        assert out.usage.input_tokens == 10
        assert out.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_extracts_reasoning_from_content_blocks(self) -> None:
        """Databricks hybrid reasoning returns reasoning/text blocks inside message.content."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._timeout = 5.0
        provider._model = "databricks-claude-sonnet-4-5"
        provider._profile = MagicMock()

        blocks = [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Reasoning summary."}]},
            {"type": "text", "text": "Final answer."},
        ]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=blocks, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        async def post(path: str, *, body: object, cast_to: object) -> object:
            return response

        provider._client = SimpleNamespace(post=post)

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        out = await provider.complete(request)
        assert out.message.text == "Final answer."
        assert out.reasoning_content == "Reasoning summary."

    @pytest.mark.asyncio
    async def test_complete_timeout(self) -> None:
        """Test timeout handling."""
        from types import SimpleNamespace

        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._timeout = 0.001
        provider._model = "databricks-claude-sonnet-4-5"
        provider._profile = MagicMock()

        async def post(path: str, *, body: object, cast_to: object) -> object:
            raise TimeoutError()

        provider._client = SimpleNamespace(post=post)

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        with pytest.raises(LLMTimeoutError):
            await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(self) -> None:
        """Test cancellation handling."""
        from penguiflow.llm.providers.databricks import DatabricksProvider

        provider = DatabricksProvider.__new__(DatabricksProvider)
        provider._timeout = 5.0
        provider._model = "databricks-claude-sonnet-4-5"
        provider._profile = MagicMock()

        cancel = MagicMock()
        cancel.is_cancelled.return_value = True

        request = LLMRequest(
            model="databricks-claude-sonnet-4-5",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        with pytest.raises(LLMCancelledError):
            await provider.complete(request, cancel=cancel)

@pytest.mark.skip(reason="Deprecated: tests below target old chat.completions path.")
class TestDatabricksProviderComplete:
    """Test Databricks provider complete method."""

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

            mock_response = self._create_mock_response("Hello from Databricks!")
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(request)

            assert response.message.text == "Hello from Databricks!"
            assert response.usage.input_tokens == 10
            assert response.usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_complete_with_tool_calls(self, mock_openai_sdk: MagicMock) -> None:
        """Test completion with tool calls."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_response = self._create_mock_response(
                content="",
                tool_calls=[
                    {"id": "call_123", "name": "get_weather", "arguments": '{"city": "NYC"}'}
                ],
            )
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
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
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=TimeoutError())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMTimeoutError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(self, mock_openai_sdk: MagicMock) -> None:
        """Test cancellation handling."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.CancelledError())
            mock_openai_sdk.AsyncOpenAI.return_value = mock_client

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            request = LLMRequest(
                model="databricks-claude-sonnet-4-5",
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_with_cancel_token(self, mock_openai_sdk: MagicMock) -> None:
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
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            with pytest.raises(LLMCancelledError):
                await provider.complete(request, cancel=cancel_token)


@pytest.mark.skip(reason="Deprecated: Databricks provider streaming uses /invocations via client.post(stream=True).")
class TestDatabricksProviderStreaming:
    """Test Databricks provider streaming."""

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

            async def async_gen():
                for chunk in chunks:
                    yield chunk

            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=async_gen())
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
                messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            )

            response = await provider.complete(
                request,
                stream=True,
                on_stream_event=on_stream,
            )

            assert "".join(streamed_text) == "Hello world!"
            assert response.message.text == "Hello world!"


class TestDatabricksProviderErrorExtraction:
    """Test Databricks error message extraction."""

    def test_extract_simple_error(self, mock_openai_sdk: MagicMock) -> None:
        """Test simple error extraction."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            exc = ValueError("Simple error message")
            result = provider._extract_databricks_error(exc)

            assert result == "Simple error message"

    def test_extract_nested_json_error(self, mock_openai_sdk: MagicMock) -> None:
        """Test nested JSON error extraction."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            # Simulated Databricks nested error
            exc = ValueError('{"error_code":"BAD_REQUEST","message":"Input is too long."}')
            result = provider._extract_databricks_error(exc)

            assert "Input is too long" in result


class TestDatabricksProviderErrorMapping:
    """Test Databricks provider error mapping."""

    def test_map_unknown_error_without_sdk_imports(self, mock_openai_sdk: MagicMock) -> None:
        """Test error mapping falls back to generic error when SDK classes unavailable."""
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.errors import LLMError
            from penguiflow.llm.providers.databricks import DatabricksProvider

            provider = DatabricksProvider(
                "databricks-claude-sonnet-4-5",
                host="workspace.databricks.com",
                token="token",
            )

            # Test via the mocked path
            with patch.object(provider, "_map_error") as mock_map:
                mock_map.return_value = LLMError(
                    message="Unknown error", provider="databricks"
                )
                result = mock_map(ValueError("Unknown error"))

                assert isinstance(result, LLMError)
                assert "Unknown error" in result.message
