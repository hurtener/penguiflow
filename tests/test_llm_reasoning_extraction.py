"""Reasoning/thinking extraction tests for native providers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from anthropic.types import Message as AnthropicMessage
from google.genai.types import GenerateContentResponse

from penguiflow.llm.providers.anthropic import _from_anthropic_response
from penguiflow.llm.providers.base import (
    OpenAICompatibleProvider,
    _extract_openai_delta_reasoning,
    _extract_openai_reasoning_content,
)
from penguiflow.llm.providers.bedrock import _from_bedrock_response
from penguiflow.llm.providers.google import _from_google_response

from .fake_sdk_clients import openai_client

if TYPE_CHECKING:
    from penguiflow.llm.profiles import ModelProfile
    from penguiflow.llm.types import (
        CancelToken,
        CompletionResponse,
        LLMRequest,
        StreamCallback,
        StreamEvent,
    )


class _DummyOpenAICompat(OpenAICompatibleProvider):
    @property
    def provider_name(self) -> str:  # pragma: no cover - test helper
        return "dummy"

    @property
    def profile(self) -> ModelProfile:  # pragma: no cover - test helper
        return MagicMock()

    @property
    def model(self) -> str:  # pragma: no cover - test helper
        return "dummy-model"

    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:  # pragma: no cover - test helper
        raise NotImplementedError


def test_openai_compatible_reasoning_helpers() -> None:
    """OpenAI compatible reasoning helpers."""
    msg = SimpleNamespace(reasoning_content="rc")
    assert _extract_openai_reasoning_content(msg) == "rc"

    msg2 = {"thinking": "t"}
    assert _extract_openai_reasoning_content(msg2) == "t"

    delta = SimpleNamespace(thinking="delta")
    assert _extract_openai_delta_reasoning(delta) == "delta"


class _FakeDelta:
    def __init__(
        self,
        *,
        content: str | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = None


class _FakeChoice:
    def __init__(
        self, delta: _FakeDelta, finish_reason: str | None = None
    ) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(
        self,
        choices: list[_FakeChoice] | None = None,
        usage: object | None = None,
    ) -> None:
        self.choices = choices or []
        self.usage = usage


class _FakeUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 1
        self.completion_tokens = 2
        self.total_tokens = 3


class _FakeStream:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks
        self._idx = 0

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> _FakeChunk:
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        item = self._chunks[self._idx]
        self._idx += 1
        return item


@pytest.mark.asyncio
async def test_openrouter_stream_emits_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter stream emits reasoning."""
    from penguiflow.llm.providers.openrouter import OpenRouterProvider

    async def create(**kwargs: object) -> _FakeStream:
        options = kwargs.get("stream_options")
        assert isinstance(options, dict)
        assert options.get("include_usage") is True
        return _FakeStream(chunks)

    chunks = [
        _FakeChunk([_FakeChoice(_FakeDelta(reasoning_content="R"))]),
        _FakeChunk(
            [_FakeChoice(_FakeDelta(content="A"), finish_reason="stop")]
        ),
        _FakeChunk([], usage=_FakeUsage()),
    ]

    provider = OpenRouterProvider(
        "openai/gpt-4o",
        api_key="test-key",
        client=openai_client(monkeypatch, create=create),
    )

    events: list[StreamEvent] = []

    def on_event(e: StreamEvent) -> None:
        events.append(e)

    resp = await provider.stream_completion(
        params={"model": "x", "messages": []},
        on_stream_event=on_event,
        cancel=None,
    )

    assert any(e.delta_reasoning == "R" for e in events)
    assert resp.reasoning_content == "R"


def test_anthropic_nonstream_extracts_reasoning() -> None:
    """Anthropic nonstream extracts reasoning."""
    response = AnthropicMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {"type": "thinking", "thinking": "T", "signature": "sig"},
                {"type": "text", "text": "A"},
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }
    )

    out = _from_anthropic_response(response)
    assert out.reasoning_content == "T"
    assert out.message.text == "A"


def test_bedrock_nonstream_extracts_reasoning() -> None:
    """Bedrock nonstream extracts reasoning."""
    resp = _from_bedrock_response(
        {
            "output": {
                "message": {
                    "content": [
                        {"reasoningContent": {"text": "R"}},
                        {"text": "A"},
                    ]
                }
            },
            "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
            "stopReason": "stop",
        }
    )

    assert resp.reasoning_content == "R"
    assert resp.message.text == "A"


def test_google_nonstream_extracts_reasoning() -> None:
    """Google nonstream extracts reasoning."""
    response = GenerateContentResponse.model_validate(
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "R", "thought": True},
                            {"text": "A", "thought": False},
                        ],
                    },
                    "finish_reason": "STOP",
                }
            ],
            "usage_metadata": {
                "prompt_token_count": 1,
                "candidates_token_count": 2,
                "total_token_count": 3,
            },
        }
    )

    out = _from_google_response(response)
    assert out.reasoning_content == "R"
    assert out.message.text == "A"


def test_reasoning_extraction_dict_content_list_with_summary() -> None:
    """Extract reasoning from a dict message's summary content."""
    # Dict message with content list containing reasoning with summary
    msg = {
        "content": [
            {
                "type": "reasoning",
                "summary": [{"text": "step1"}, {"text": "step2"}],
            },
            {"type": "text", "text": "result"},
        ]
    }
    result = _extract_openai_reasoning_content(msg)
    assert result == "step1step2"


def test_reasoning_extraction_dict_content_list_with_direct_text() -> None:
    """Extract reasoning from a dict message's text content."""
    # Dict message with content list containing reasoning with direct text
    msg = {
        "content": [
            {"type": "thinking", "text": "direct thought"},
            {"type": "text", "text": "result"},
        ]
    }
    result = _extract_openai_reasoning_content(msg)
    assert result == "direct thought"


def test_reasoning_extraction_object_content_list_with_summary() -> None:
    """Extract reasoning from an object message's content list."""
    # Object message with content list attribute
    msg = SimpleNamespace(
        content=[
            {"type": "thought", "summary": [{"text": "think1"}]},
            {"type": "text", "text": "output"},
        ]
    )
    result = _extract_openai_reasoning_content(msg)
    assert result == "think1"


def test_reasoning_extraction_object_content_list_with_direct_text() -> None:
    """Extract reasoning from an object message's text content."""
    msg = SimpleNamespace(
        content=[
            {"type": "reasoning", "text": "direct reasoning"},
        ]
    )
    result = _extract_openai_reasoning_content(msg)
    assert result == "direct reasoning"


def test_delta_reasoning_dict_content_list_with_summary() -> None:
    """Test delta reasoning extraction from dict delta with content list."""
    delta = {
        "content": [
            {"type": "thinking", "summary": [{"text": "delta summary"}]},
        ]
    }
    result = _extract_openai_delta_reasoning(delta)
    assert result == "delta summary"


def test_delta_reasoning_dict_content_list_with_direct_text() -> None:
    """Extract delta reasoning from a dict delta's text content."""
    delta = {
        "content": [
            {"type": "reasoning", "text": "delta direct"},
        ]
    }
    result = _extract_openai_delta_reasoning(delta)
    assert result == "delta direct"


def test_delta_reasoning_object_content_list_with_summary() -> None:
    """Test delta reasoning extraction from object delta with content list."""
    delta = SimpleNamespace(
        content=[
            {"type": "thought", "summary": [{"text": "obj delta summary"}]},
        ]
    )
    result = _extract_openai_delta_reasoning(delta)
    assert result == "obj delta summary"


def test_delta_reasoning_object_content_list_with_direct_text() -> None:
    """Extract delta reasoning from an object delta's text content."""
    delta = SimpleNamespace(
        content=[
            {"type": "thinking", "text": "obj delta direct"},
        ]
    )
    result = _extract_openai_delta_reasoning(delta)
    assert result == "obj delta direct"
