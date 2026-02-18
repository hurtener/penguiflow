"""Tests for NIM provider initialization, request building, and execution."""

from __future__ import annotations

import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMTimeoutError,
)
from penguiflow.llm.types import (
    LLMMessage,
    LLMRequest,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolSpec,
)


@pytest.fixture
def mock_openai_sdk() -> Any:
    """Mock the OpenAI SDK used by NIM provider."""
    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_sdk.AsyncOpenAI.return_value = mock_client
    return mock_sdk


class TestNIMProviderInit:
    def test_init_with_api_key(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.nim import NIMProvider

            provider = NIMProvider("qwen/qwen3.5-397b-a17b", api_key="nim-key")

            assert provider.model == "qwen/qwen3.5-397b-a17b"
            assert provider.provider_name == "nim"
            call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
            assert call_kwargs["api_key"] == "nim-key"
            assert call_kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"

    def test_init_uses_nim_api_key_env(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.nim import NIMProvider

            with patch.dict(os.environ, {"NIM_API_KEY": "nim-env"}, clear=True):
                NIMProvider("qwen/qwen3.5-397b-a17b")
                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert call_kwargs["api_key"] == "nim-env"

    def test_init_uses_nvidia_api_key_fallback(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.nim import NIMProvider

            with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvidia-env"}, clear=True):
                NIMProvider("qwen/qwen3.5-397b-a17b")
                call_kwargs = mock_openai_sdk.AsyncOpenAI.call_args[1]
                assert call_kwargs["api_key"] == "nvidia-env"

    def test_init_raises_without_api_key(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.nim import NIMProvider

            with patch.dict(os.environ, {}, clear=True):
                with pytest.raises(ValueError, match="NIM API key required"):
                    NIMProvider("qwen/qwen3.5-397b-a17b")

    def test_init_strips_prefixes(self, mock_openai_sdk: MagicMock) -> None:
        with patch.dict("sys.modules", {"openai": mock_openai_sdk}):
            from penguiflow.llm.providers.nim import NIMProvider

            provider_nim = NIMProvider("nim/qwen/qwen3.5-397b-a17b", api_key="x")
            provider_nvidia = NIMProvider("nvidia/qwen/qwen3.5-397b-a17b", api_key="x")

            assert provider_nim.model == "qwen/qwen3.5-397b-a17b"
            assert provider_nvidia.model == "qwen/qwen3.5-397b-a17b"


class TestNIMProviderBuildParams:
    def test_build_params_basic(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            temperature=0.7,
            max_tokens=256,
        )

        params = provider._build_params(request)
        assert params["model"] == "qwen/qwen3.5-397b-a17b"
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 256

    def test_build_params_with_tools_and_structured_output(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            tools=(
                ToolSpec(name="get_weather", description="Get weather", json_schema={"type": "object"}),
            ),
            tool_choice="get_weather",
            structured_output=StructuredOutputSpec(name="Out", json_schema={"type": "object"}, strict=False),
            extra={"custom_flag": True},
        )

        params = provider._build_params(request)
        assert "tools" in params
        assert params["tool_choice"]["function"]["name"] == "get_weather"
        assert params["response_format"]["type"] == "json_schema"
        assert params["custom_flag"] is True

    def test_build_params_maps_reasoning_effort_to_thinking_true(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=True, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={"reasoning_effort": "high"},
        )

        params = provider._build_params(request)
        assert params["extra_body"]["chat_template_kwargs"]["thinking"] is True
        assert "reasoning_effort" not in params

    def test_build_params_maps_reasoning_effort_to_thinking_false(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=True, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={"reasoning_effort": "off"},
        )

        params = provider._build_params(request)
        assert params["extra_body"]["chat_template_kwargs"]["thinking"] is False

    def test_build_params_explicit_extra_body_thinking_overrides_effort(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=True, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={
                "reasoning_effort": "high",
                "extra_body": {"chat_template_kwargs": {"thinking": False}},
            },
        )

        params = provider._build_params(request)
        assert params["extra_body"]["chat_template_kwargs"]["thinking"] is False

    def test_build_params_normalizes_chat_template_kwargs_alias(self, caplog: pytest.LogCaptureFixture) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=True, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={"chat_template_kwargs": {"thinking": True, "foo": "bar"}},
        )

        with caplog.at_level(logging.WARNING, logger="penguiflow.llm.providers.nim"):
            params = provider._build_params(request)

        assert params["extra_body"]["chat_template_kwargs"]["thinking"] is True
        assert params["extra_body"]["chat_template_kwargs"]["foo"] == "bar"
        assert any("nim_chat_template_kwargs_alias_normalized" in r.message for r in caplog.records)

    def test_build_params_ignores_unsupported_budget_controls(self, caplog: pytest.LogCaptureFixture) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=True, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={
                "reasoning_budget_tokens": 8000,
                "thinking": {"budget_tokens": 4096},
                "extra_body": {"chat_template_kwargs": {"thinking_budget": 2048}},
            },
        )

        with caplog.at_level(logging.WARNING, logger="penguiflow.llm.providers.nim"):
            params = provider._build_params(request)

        assert "reasoning_budget_tokens" not in params
        assert "thinking" not in params
        assert "extra_body" not in params
        assert any("nim_unsupported_reasoning_budget" in r.message for r in caplog.records)

    def test_build_params_non_reasoning_extra_passthrough(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=True, reasoning_effort_param=None)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
            extra={"seed": 7, "custom_flag": True},
        )

        params = provider._build_params(request)
        assert params["seed"] == 7
        assert params["custom_flag"] is True


class TestNIMProviderComplete:
    @pytest.mark.asyncio
    async def test_complete_simple_text(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        mock_msg = MagicMock()
        mock_msg.content = "Hello from NIM"
        mock_msg.tool_calls = None
        mock_msg.reasoning_content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "stop"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 12
        mock_usage.completion_tokens = 8
        mock_usage.total_tokens = 20

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
        provider._timeout = 30.0
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        response = await provider.complete(request)
        assert response.message.text == "Hello from NIM"
        assert response.usage.total_tokens == 20

    @pytest.mark.asyncio
    async def test_complete_timeout(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
        provider._timeout = 1.0
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(side_effect=TimeoutError("timeout"))

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        with pytest.raises(LLMTimeoutError):
            await provider.complete(request)

    @pytest.mark.asyncio
    async def test_complete_cancelled(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
        provider._timeout = 30.0
        provider._client = MagicMock()

        cancel_token = MagicMock()
        cancel_token.is_cancelled.return_value = True

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        with pytest.raises(LLMCancelledError):
            await provider.complete(request, cancel=cancel_token)

    @pytest.mark.asyncio
    async def test_streaming(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        chunks: list[MagicMock] = []
        for text in ("Hello", " ", "NIM"):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta = MagicMock()
            chunk.choices[0].delta.content = text
            chunk.choices[0].delta.tool_calls = None
            chunk.choices[0].finish_reason = None
            chunks.append(chunk)

        end_chunk = MagicMock()
        end_chunk.choices = [MagicMock()]
        end_chunk.choices[0].delta = MagicMock()
        end_chunk.choices[0].delta.content = None
        end_chunk.choices[0].delta.tool_calls = None
        end_chunk.choices[0].finish_reason = "stop"
        chunks.append(end_chunk)

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 10
        usage_chunk.usage.completion_tokens = 3
        usage_chunk.usage.total_tokens = 13
        chunks.append(usage_chunk)

        async def _stream() -> Any:
            for chunk in chunks:
                yield chunk

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(supports_reasoning=False, reasoning_effort_param=None)
        provider._timeout = 30.0
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=_stream())

        streamed: list[str] = []

        def on_stream(event: StreamEvent) -> None:
            if event.delta_text:
                streamed.append(event.delta_text)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hi")]),),
        )

        response = await provider.complete(request, stream=True, on_stream_event=on_stream)
        assert "".join(streamed) == "Hello NIM"
        assert response.message.text == "Hello NIM"
        assert response.usage.total_tokens == 13

    @pytest.mark.asyncio
    async def test_complete_moves_think_tags_to_reasoning_content(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        mock_msg = MagicMock()
        mock_msg.content = "<think>Reasoning here.</think>final answer"
        mock_msg.tool_calls = None
        mock_msg.reasoning_content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "stop"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 12
        mock_usage.completion_tokens = 8
        mock_usage.total_tokens = 20

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(
            supports_reasoning=True,
            reasoning_effort_param=None,
            thinking_tags=("<think>", "</think>"),
        )
        provider._timeout = 30.0
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=mock_response)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hello")]),),
        )

        response = await provider.complete(request)
        assert response.message.text == "final answer"
        assert response.reasoning_content == "Reasoning here."

    @pytest.mark.asyncio
    async def test_streaming_moves_think_tags_to_reasoning_content(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = "<think>Reason.</think>done"
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"

        usage_chunk = MagicMock()
        usage_chunk.choices = []
        usage_chunk.usage = MagicMock()
        usage_chunk.usage.prompt_tokens = 10
        usage_chunk.usage.completion_tokens = 3
        usage_chunk.usage.total_tokens = 13

        async def _stream() -> Any:
            yield chunk
            yield usage_chunk

        provider = NIMProvider.__new__(NIMProvider)
        provider._model = "qwen/qwen3.5-397b-a17b"
        provider._profile = MagicMock(
            supports_reasoning=True,
            reasoning_effort_param=None,
            thinking_tags=("<think>", "</think>"),
        )
        provider._timeout = 30.0
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=_stream())

        events: list[StreamEvent] = []

        def on_stream(event: StreamEvent) -> None:
            events.append(event)

        request = LLMRequest(
            model="qwen/qwen3.5-397b-a17b",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hi")]),),
        )

        response = await provider.complete(request, stream=True, on_stream_event=on_stream)
        assert response.message.text == "done"
        assert response.reasoning_content == "Reason."
        assert any(e.done for e in events)


class TestNIMProviderErrorMapping:
    def test_map_error_auth(self) -> None:
        from penguiflow.llm.providers.nim import NIMProvider

        class AuthenticationError(Exception):
            status_code = 401

        fake_openai = MagicMock()
        fake_openai.AuthenticationError = AuthenticationError
        fake_openai.RateLimitError = type("RateLimitError", (Exception,), {})
        fake_openai.BadRequestError = type("BadRequestError", (Exception,), {})
        fake_openai.APIStatusError = type("APIStatusError", (Exception,), {})
        fake_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})

        provider = NIMProvider.__new__(NIMProvider)

        with patch.dict("sys.modules", {"openai": fake_openai}):
            mapped = provider._map_error(AuthenticationError("auth"))

        assert isinstance(mapped, LLMAuthError)
        assert mapped.provider == "nim"
