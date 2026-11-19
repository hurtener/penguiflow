"""Tests for the LLM types module."""

from __future__ import annotations

import dataclasses

import pytest

from penguiflow.llm.types import (
    AudioPart,
    CancelToken,
    CompletionResponse,
    Cost,
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    extract_single_tool_call,
    extract_text,
    strip_markdown_fences,
)


class TestTextPart:
    """Tests for text part."""

    def test_create(self) -> None:
        """Create."""
        part = TextPart(text="hello")
        assert part.text == "hello"

    def test_frozen(self) -> None:
        """Frozen."""
        part = TextPart(text="hello")
        for field in dataclasses.fields(part):
            with pytest.raises(AttributeError):
                setattr(part, field.name, "world")


class TestToolCallPart:
    """Tests for tool call part."""

    def test_create(self) -> None:
        """Create."""
        part = ToolCallPart(
            name="test_tool", arguments_json='{"key": "value"}'
        )
        assert part.name == "test_tool"
        assert part.arguments_json == '{"key": "value"}'
        assert part.call_id is None

    def test_with_call_id(self) -> None:
        """With call ID."""
        part = ToolCallPart(
            name="test", arguments_json="{}", call_id="call_123"
        )
        assert part.call_id == "call_123"


class TestToolResultPart:
    """Tests for tool result part."""

    def test_create(self) -> None:
        """Create."""
        part = ToolResultPart(name="test_tool", result_json='{"result": 42}')
        assert part.name == "test_tool"
        assert part.result_json == '{"result": 42}'
        assert part.is_error is False

    def test_error_result(self) -> None:
        """Error result."""
        part = ToolResultPart(
            name="test", result_json='{"error": "failed"}', is_error=True
        )
        assert part.is_error is True


class TestImagePart:
    """Tests for image part."""

    def test_create(self) -> None:
        """Create."""
        data = b"\x89PNG\r\n\x1a\n"
        part = ImagePart(data=data, media_type="image/png")
        assert part.data == data
        assert part.media_type == "image/png"
        assert part.detail == "auto"

    def test_with_detail(self) -> None:
        """With detail."""
        part = ImagePart(data=b"", media_type="image/jpeg", detail="high")
        assert part.detail == "high"


class TestAudioPart:
    """Tests for audio part."""

    def test_create(self) -> None:
        """Create."""
        data = b"RIFF"
        part = AudioPart(data=data, media_type="audio/wav")
        assert part.data == data
        assert part.media_type == "audio/wav"


class TestLLMMessage:
    """Tests for LLM message."""

    def test_create_simple(self) -> None:
        """Create simple."""
        msg = LLMMessage(role="user", parts=[TextPart(text="hello")])
        assert msg.role == "user"
        assert len(msg.parts) == 1
        assert isinstance(msg.parts[0], TextPart)

    def test_text_property(self) -> None:
        """Text property."""
        msg = LLMMessage(
            role="user",
            parts=[TextPart(text="hello "), TextPart(text="world")],
        )
        assert msg.text == "hello world"

    def test_tool_calls_property(self) -> None:
        """Tool calls property."""
        msg = LLMMessage(
            role="assistant",
            parts=[
                TextPart(text="Let me check"),
                ToolCallPart(name="search", arguments_json='{"q": "test"}'),
            ],
        )
        calls = msg.tool_calls
        assert len(calls) == 1
        assert calls[0].name == "search"

    def test_parts_tuple_conversion(self) -> None:
        """Parts tuple conversion."""
        msg = LLMMessage(role="user", parts=[TextPart(text="test")])
        assert isinstance(msg.parts, tuple)


class TestToolSpec:
    """Tests for tool spec."""

    def test_create(self) -> None:
        """Create."""
        spec = ToolSpec(
            name="calculator",
            description="Do math",
            json_schema={"type": "object"},
        )
        assert spec.name == "calculator"
        assert spec.description == "Do math"


class TestStructuredOutputSpec:
    """Tests for structured output spec."""

    def test_create(self) -> None:
        """Create."""
        spec = StructuredOutputSpec(
            name="response",
            json_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
            },
        )
        assert spec.name == "response"
        assert spec.strict is True

    def test_non_strict(self) -> None:
        """Non strict."""
        spec = StructuredOutputSpec(name="test", json_schema={}, strict=False)
        assert spec.strict is False


class TestLLMRequest:
    """Tests for LLM request."""

    def test_create_minimal(self) -> None:
        """Create minimal."""
        request = LLMRequest(
            model="gpt-4o",
            messages=[LLMMessage(role="user", parts=[TextPart(text="hi")])],
        )
        assert request.model == "gpt-4o"
        assert request.temperature is None  # opt-in: omitted by default
        assert request.tools is None

    def test_with_tools(self) -> None:
        """With tools."""
        tool = ToolSpec(name="test", description="test", json_schema={})
        request = LLMRequest(
            model="gpt-4o",
            messages=[],
            tools=[tool],
            tool_choice="test",
        )
        assert request.tool_choice == "test"
        assert request.tools is not None
        assert len(request.tools) == 1


class TestUsage:
    """Tests for usage."""

    def test_create(self) -> None:
        """Create."""
        usage = Usage(input_tokens=100, output_tokens=50, total_tokens=150)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150

    def test_zero(self) -> None:
        """Zero."""
        usage = Usage.zero()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestCompletionResponse:
    """Tests for completion response."""

    def test_create(self) -> None:
        """Create."""
        msg = LLMMessage(role="assistant", parts=[TextPart(text="Answer")])
        usage = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
        response = CompletionResponse(message=msg, usage=usage)
        assert response.message.text == "Answer"
        assert response.usage.total_tokens == 15


class TestCost:
    """Tests for cost."""

    def test_create(self) -> None:
        """Create."""
        cost = Cost(input_cost=0.01, output_cost=0.02, total_cost=0.03)
        assert cost.total_cost == 0.03
        assert cost.currency == "USD"

    def test_zero(self) -> None:
        """Zero."""
        cost = Cost.zero()
        assert cost.total_cost == 0.0

    def test_add(self) -> None:
        """Add."""
        cost1 = Cost(input_cost=0.01, output_cost=0.01, total_cost=0.02)
        cost2 = Cost(input_cost=0.02, output_cost=0.02, total_cost=0.04)
        combined = cost1 + cost2
        assert combined.total_cost == pytest.approx(0.06)

    def test_add_different_currency_raises(self) -> None:
        """Add different currency raises."""
        cost1 = Cost(
            input_cost=0.01, output_cost=0.01, total_cost=0.02, currency="USD"
        )
        cost2 = Cost(
            input_cost=0.01, output_cost=0.01, total_cost=0.02, currency="EUR"
        )
        with pytest.raises(ValueError, match="different currencies"):
            cost1 + cost2


class TestStreamEvent:
    """Tests for stream event."""

    def test_create(self) -> None:
        """Create."""
        event = StreamEvent(delta_text="Hello")
        assert event.delta_text == "Hello"
        assert event.done is False

    def test_done_event(self) -> None:
        """Done event."""
        event = StreamEvent(done=True, finish_reason="stop")
        assert event.done is True
        assert event.finish_reason == "stop"

    def test_reasoning_event(self) -> None:
        """Reasoning event."""
        event = StreamEvent(delta_reasoning="Let me think about this")
        assert event.delta_reasoning == "Let me think about this"
        assert event.delta_text is None
        assert event.done is False


class TestCancelToken:
    """Tests for cancel token."""

    def test_initial_state(self) -> None:
        """Initial state."""
        token = CancelToken()
        assert token.is_cancelled() is False

    def test_cancel(self) -> None:
        """Cancel."""
        token = CancelToken()
        token.cancel()
        assert token.is_cancelled() is True


class TestExtractText:
    """Tests for extract text."""

    def test_extract_text(self) -> None:
        """Extract text."""
        msg = LLMMessage(role="user", parts=[TextPart(text="test message")])
        assert extract_text(msg) == "test message"


class TestExtractSingleToolCall:
    """Tests for extract single tool call."""

    def test_extract_success(self) -> None:
        """Extract success."""
        msg = LLMMessage(
            role="assistant",
            parts=[ToolCallPart(name="test_tool", arguments_json='{"a": 1}')],
        )
        call = extract_single_tool_call(msg)
        assert call.name == "test_tool"

    def test_with_expected_name(self) -> None:
        """With expected name."""
        msg = LLMMessage(
            role="assistant",
            parts=[ToolCallPart(name="correct", arguments_json="{}")],
        )
        call = extract_single_tool_call(msg, expected_name="correct")
        assert call.name == "correct"

    def test_wrong_name_raises(self) -> None:
        """Wrong name raises."""
        msg = LLMMessage(
            role="assistant",
            parts=[ToolCallPart(name="wrong", arguments_json="{}")],
        )
        with pytest.raises(ValueError, match="Expected tool call"):
            extract_single_tool_call(msg, expected_name="correct")

    def test_no_tool_call_raises(self) -> None:
        """No tool call raises."""
        msg = LLMMessage(role="assistant", parts=[TextPart(text="no tools")])
        with pytest.raises(ValueError, match="No tool calls found"):
            extract_single_tool_call(msg)

    def test_multiple_tool_calls_raises(self) -> None:
        """Multiple tool calls raises."""
        msg = LLMMessage(
            role="assistant",
            parts=[
                ToolCallPart(name="tool1", arguments_json="{}"),
                ToolCallPart(name="tool2", arguments_json="{}"),
            ],
        )
        with pytest.raises(ValueError, match="Expected single tool call"):
            extract_single_tool_call(msg)


class TestStripMarkdownFences:
    """Tests for strip markdown fences."""

    def test_no_fences(self) -> None:
        """No fences."""
        assert strip_markdown_fences('{"key": "value"}') == '{"key": "value"}'

    def test_with_json_fence(self) -> None:
        """With JSON fence."""
        text = '```json\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_with_plain_fence(self) -> None:
        """With plain fence."""
        text = "```\n{}\n```"
        assert strip_markdown_fences(text) == "{}"

    def test_whitespace_handling(self) -> None:
        """Whitespace handling."""
        text = '  ```json\n  {"test": 1}  \n```  '
        result = strip_markdown_fences(text)
        assert "test" in result


def test_audio_part_exported_from_llm_package() -> None:
    """Audio part exported from LLM package."""
    from penguiflow.llm import AudioPart as ExportedAudioPart

    assert ExportedAudioPart is AudioPart
