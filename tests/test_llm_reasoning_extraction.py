"""Reasoning/thinking extraction tests for native providers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from penguiflow.llm.providers.base import OpenAICompatibleProvider


class _DummyOpenAICompat(OpenAICompatibleProvider):
    @property
    def provider_name(self) -> str:  # pragma: no cover - test helper
        return "dummy"

    @property
    def profile(self) -> Any:  # pragma: no cover - test helper
        return MagicMock()

    @property
    def model(self) -> str:  # pragma: no cover - test helper
        return "dummy-model"

    async def complete(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - test helper
        raise NotImplementedError


def test_openai_compatible_reasoning_helpers() -> None:
    provider = _DummyOpenAICompat()

    msg = SimpleNamespace(reasoning_content="rc")
    assert provider._extract_openai_reasoning_content(msg) == "rc"

    msg2 = {"thinking": "t"}
    assert provider._extract_openai_reasoning_content(msg2) == "t"

    delta = SimpleNamespace(thinking="delta")
    assert provider._extract_openai_delta_reasoning(delta) == "delta"


@pytest.mark.asyncio
async def test_openrouter_stream_emits_reasoning() -> None:
    from penguiflow.llm.providers.openrouter import OpenRouterProvider
    from penguiflow.llm.types import StreamEvent

    class FakeDelta:
        def __init__(self, *, content: str | None = None, reasoning_content: str | None = None) -> None:
            self.content = content
            self.reasoning_content = reasoning_content
            self.tool_calls = None

    class FakeChoice:
        def __init__(self, delta: FakeDelta, finish_reason: str | None = None) -> None:
            self.delta = delta
            self.finish_reason = finish_reason

    class FakeChunk:
        def __init__(self, choices: list[FakeChoice] | None = None, usage: Any | None = None) -> None:
            self.choices = choices or []
            self.usage = usage

    class FakeUsage:
        def __init__(self) -> None:
            self.prompt_tokens = 1
            self.completion_tokens = 2
            self.total_tokens = 3

    class FakeStream:
        def __init__(self, chunks: list[FakeChunk]) -> None:
            self._chunks = chunks
            self._idx = 0

        def __aiter__(self) -> FakeStream:
            return self

        async def __anext__(self) -> FakeChunk:
            if self._idx >= len(self._chunks):
                raise StopAsyncIteration
            item = self._chunks[self._idx]
            self._idx += 1
            return item

    class FakeCompletions:
        def __init__(self, stream: FakeStream) -> None:
            self._stream = stream

        async def create(self, **kwargs: Any) -> FakeStream:
            assert (kwargs.get("stream_options") or {}).get("include_usage") is True
            return self._stream

    class FakeChat:
        def __init__(self, stream: FakeStream) -> None:
            self.completions = FakeCompletions(stream)

    class FakeClient:
        def __init__(self, stream: FakeStream) -> None:
            self.chat = FakeChat(stream)

    chunks = [
        FakeChunk([FakeChoice(FakeDelta(reasoning_content="R"))]),
        FakeChunk([FakeChoice(FakeDelta(content="A"), finish_reason="stop")]),
        FakeChunk([], usage=FakeUsage()),
    ]

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._client = FakeClient(FakeStream(chunks))  # type: ignore[attr-defined]

    events: list[StreamEvent] = []

    def on_event(e: StreamEvent) -> None:
        events.append(e)

    resp = await provider._stream_completion(  # type: ignore[attr-defined]
        params={"model": "x", "messages": []},
        on_stream_event=on_event,
        timeout=5.0,
        cancel=None,
    )

    assert any(e.delta_reasoning == "R" for e in events)
    assert resp.reasoning_content == "R"


def test_anthropic_nonstream_extracts_reasoning() -> None:
    from penguiflow.llm.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider.__new__(AnthropicProvider)

    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="T"),
            SimpleNamespace(type="text", text="A"),
        ],
        usage=SimpleNamespace(input_tokens=1, output_tokens=2),
        stop_reason="end_turn",
    )

    out = provider._from_anthropic_response(response)  # type: ignore[attr-defined]
    assert out.reasoning_content == "T"
    assert out.message.text == "A"


def test_bedrock_nonstream_extracts_reasoning() -> None:
    from penguiflow.llm.providers.bedrock import BedrockProvider

    provider = BedrockProvider.__new__(BedrockProvider)

    resp = provider._from_bedrock_response(  # type: ignore[attr-defined]
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
    from penguiflow.llm.providers.google import GoogleProvider

    provider = GoogleProvider.__new__(GoogleProvider)

    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(thought="R"),
                        SimpleNamespace(text="A"),
                    ]
                ),
                finish_reason="STOP",
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=1,
            candidates_token_count=2,
            total_token_count=3,
        ),
    )

    out = provider._from_google_response(response)  # type: ignore[attr-defined]
    assert out.reasoning_content == "R"
    assert out.message.text == "A"


def test_reasoning_extraction_dict_content_list_with_summary() -> None:
    """Test reasoning extraction from dict message with content list and summary."""
    provider = _DummyOpenAICompat()

    # Dict message with content list containing reasoning with summary
    msg = {
        "content": [
            {"type": "reasoning", "summary": [{"text": "step1"}, {"text": "step2"}]},
            {"type": "text", "text": "result"},
        ]
    }
    result = provider._extract_openai_reasoning_content(msg)
    assert result == "step1step2"


def test_reasoning_extraction_dict_content_list_with_direct_text() -> None:
    """Test reasoning extraction from dict message with content list and direct text."""
    provider = _DummyOpenAICompat()

    # Dict message with content list containing reasoning with direct text
    msg = {
        "content": [
            {"type": "thinking", "text": "direct thought"},
            {"type": "text", "text": "result"},
        ]
    }
    result = provider._extract_openai_reasoning_content(msg)
    assert result == "direct thought"


def test_reasoning_extraction_object_content_list_with_summary() -> None:
    """Test reasoning extraction from object message with content list attribute."""
    provider = _DummyOpenAICompat()

    # Object message with content list attribute
    msg = SimpleNamespace(
        content=[
            {"type": "thought", "summary": [{"text": "think1"}]},
            {"type": "text", "text": "output"},
        ]
    )
    result = provider._extract_openai_reasoning_content(msg)
    assert result == "think1"


def test_reasoning_extraction_object_content_list_with_direct_text() -> None:
    """Test reasoning extraction from object with content list and direct text."""
    provider = _DummyOpenAICompat()

    msg = SimpleNamespace(
        content=[
            {"type": "reasoning", "text": "direct reasoning"},
        ]
    )
    result = provider._extract_openai_reasoning_content(msg)
    assert result == "direct reasoning"


def test_delta_reasoning_dict_content_list_with_summary() -> None:
    """Test delta reasoning extraction from dict delta with content list."""
    provider = _DummyOpenAICompat()

    delta = {
        "content": [
            {"type": "thinking", "summary": [{"text": "delta summary"}]},
        ]
    }
    result = provider._extract_openai_delta_reasoning(delta)
    assert result == "delta summary"


def test_delta_reasoning_dict_content_list_with_direct_text() -> None:
    """Test delta reasoning extraction from dict delta with content list direct text."""
    provider = _DummyOpenAICompat()

    delta = {
        "content": [
            {"type": "reasoning", "text": "delta direct"},
        ]
    }
    result = provider._extract_openai_delta_reasoning(delta)
    assert result == "delta direct"


def test_delta_reasoning_object_content_list_with_summary() -> None:
    """Test delta reasoning extraction from object delta with content list."""
    provider = _DummyOpenAICompat()

    delta = SimpleNamespace(
        content=[
            {"type": "thought", "summary": [{"text": "obj delta summary"}]},
        ]
    )
    result = provider._extract_openai_delta_reasoning(delta)
    assert result == "obj delta summary"


def test_delta_reasoning_object_content_list_with_direct_text() -> None:
    """Test delta reasoning extraction from object delta with content list direct text."""
    provider = _DummyOpenAICompat()

    delta = SimpleNamespace(
        content=[
            {"type": "thinking", "text": "obj delta direct"},
        ]
    )
    result = provider._extract_openai_delta_reasoning(delta)
    assert result == "obj delta direct"
