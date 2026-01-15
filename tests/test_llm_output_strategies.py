"""Tests for the LLM output strategies."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from penguiflow.llm.output.native import NativeOutputStrategy
from penguiflow.llm.output.prompted import PromptedOutputStrategy
from penguiflow.llm.output.tool import ToolsOutputStrategy
from penguiflow.llm.types import (
    CompletionResponse,
    LLMMessage,
    TextPart,
    ToolCallPart,
    Usage,
)


class SampleModel(BaseModel):
    name: str
    value: int


class TestNativeOutputStrategy:
    def test_build_request_standard(self) -> None:
        strategy = NativeOutputStrategy()

        # Create mock profile for standard provider
        profile = MagicMock()
        profile.native_structured_kind = "response_format"

        # Create mock plan
        plan = MagicMock()
        plan.transformed_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        plan.strict_applied = True

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        request = strategy.build_request(
            model="gpt-4o",
            messages=messages,
            response_model=SampleModel,
            profile=profile,
            plan=plan,
        )

        assert request.model == "gpt-4o"
        assert request.structured_output is not None
        assert request.structured_output.name == "SampleModel"
        assert request.structured_output.strict is True
        assert request.tools is None

    def test_build_request_anthropic(self) -> None:
        strategy = NativeOutputStrategy()

        profile = MagicMock()
        profile.native_structured_kind = "anthropic_tool_use"

        plan = MagicMock()
        plan.transformed_schema = {"type": "object"}

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        request = strategy.build_request(
            model="claude-3-5-sonnet",
            messages=messages,
            response_model=SampleModel,
            profile=profile,
            plan=plan,
        )

        assert request.tools is not None
        assert len(request.tools) == 1
        assert request.tools[0].name == "SampleModel"
        assert request.tool_choice == "SampleModel"
        assert request.structured_output is None

    def test_build_request_bedrock(self) -> None:
        strategy = NativeOutputStrategy()

        profile = MagicMock()
        profile.native_structured_kind = "bedrock_tool_use"

        plan = MagicMock()
        plan.transformed_schema = {"type": "object"}

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        request = strategy.build_request(
            model="anthropic.claude-3-5-sonnet",
            messages=messages,
            response_model=SampleModel,
            profile=profile,
            plan=plan,
        )

        assert request.tools is not None
        assert len(request.tools) == 1
        assert request.tool_choice == "SampleModel"

    def test_parse_response_text(self) -> None:
        strategy = NativeOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='{"name": "test", "value": 42}')],
            ),
            usage=Usage(input_tokens=10, output_tokens=20, total_tokens=30),
        )

        result = strategy.parse_response(response, SampleModel)
        assert result.name == "test"
        assert result.value == 42

    def test_parse_response_tool_call(self) -> None:
        strategy = NativeOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[
                    ToolCallPart(
                        name="SampleModel",
                        arguments_json='{"name": "from_tool", "value": 100}',
                    )
                ],
            ),
            usage=Usage(input_tokens=10, output_tokens=20, total_tokens=30),
        )

        result = strategy.parse_response(response, SampleModel)
        assert result.name == "from_tool"
        assert result.value == 100


class TestToolsOutputStrategy:
    def test_build_request(self) -> None:
        strategy = ToolsOutputStrategy()

        profile = MagicMock()
        plan = MagicMock()
        plan.transformed_schema = {"type": "object", "properties": {}}

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        request = strategy.build_request(
            model="gpt-4o",
            messages=messages,
            response_model=SampleModel,
            profile=profile,
            plan=plan,
        )

        assert request.tools is not None
        assert len(request.tools) == 1
        assert request.tools[0].name == "structured_output"
        assert request.tool_choice == "structured_output"

    def test_parse_response(self) -> None:
        strategy = ToolsOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[
                    ToolCallPart(
                        name="structured_output",
                        arguments_json='{"name": "parsed", "value": 50}',
                    )
                ],
            ),
            usage=Usage.zero(),
        )

        result = strategy.parse_response(response, SampleModel)
        assert result.name == "parsed"
        assert result.value == 50

    def test_parse_response_wrong_tool_raises(self) -> None:
        strategy = ToolsOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[
                    ToolCallPart(
                        name="wrong_tool",
                        arguments_json='{"name": "test", "value": 1}',
                    )
                ],
            ),
            usage=Usage.zero(),
        )

        with pytest.raises(ValueError, match="Expected tool call"):
            strategy.parse_response(response, SampleModel)


class TestPromptedOutputStrategy:
    def test_build_request_no_system(self) -> None:
        strategy = PromptedOutputStrategy()

        profile = MagicMock()
        plan = MagicMock()
        plan.transformed_schema = {"type": "object"}

        messages = [LLMMessage(role="user", parts=[TextPart(text="Give me data")])]

        request = strategy.build_request(
            model="gpt-4o",
            messages=messages,
            response_model=SampleModel,
            profile=profile,
            plan=plan,
        )

        # Should have added system message at the beginning
        assert len(request.messages) == 2
        assert request.messages[0].role == "system"
        assert "JSON" in request.messages[0].text

    def test_build_request_with_system(self) -> None:
        strategy = PromptedOutputStrategy()

        profile = MagicMock()
        plan = MagicMock()
        plan.transformed_schema = {"type": "object"}

        messages = [
            LLMMessage(role="system", parts=[TextPart(text="You are helpful.")]),
            LLMMessage(role="user", parts=[TextPart(text="Give me data")]),
        ]

        request = strategy.build_request(
            model="gpt-4o",
            messages=messages,
            response_model=SampleModel,
            profile=profile,
            plan=plan,
        )

        # Should have appended to existing system message
        assert len(request.messages) == 2
        assert "You are helpful." in request.messages[0].text
        assert "JSON" in request.messages[0].text

    def test_parse_response_clean_json(self) -> None:
        strategy = PromptedOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='{"name": "clean", "value": 10}')],
            ),
            usage=Usage.zero(),
        )

        result = strategy.parse_response(response, SampleModel)
        assert result.name == "clean"
        assert result.value == 10

    def test_parse_response_with_markdown_fence(self) -> None:
        strategy = PromptedOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='```json\n{"name": "fenced", "value": 20}\n```')],
            ),
            usage=Usage.zero(),
        )

        result = strategy.parse_response(response, SampleModel)
        assert result.name == "fenced"
        assert result.value == 20

    def test_parse_response_with_surrounding_text(self) -> None:
        strategy = PromptedOutputStrategy()

        response = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='Here is the data: {"name": "extracted", "value": 30} as requested.')],
            ),
            usage=Usage.zero(),
        )

        result = strategy.parse_response(response, SampleModel)
        assert result.name == "extracted"
        assert result.value == 30

    def test_extract_json_array(self) -> None:
        strategy = PromptedOutputStrategy()

        text = 'Here is an array: [1, 2, 3] done.'
        result = strategy._extract_json(text)
        assert result == "[1, 2, 3]"

    def test_extract_json_clean(self) -> None:
        strategy = PromptedOutputStrategy()

        text = '{"key": "value"}'
        result = strategy._extract_json(text)
        assert result == text

    def test_extract_json_no_json(self) -> None:
        strategy = PromptedOutputStrategy()

        text = "No JSON here"
        result = strategy._extract_json(text)
        assert result == text  # Returns as-is for validation to fail
