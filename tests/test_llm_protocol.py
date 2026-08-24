"""Tests for the LLM protocol adapter module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.llm.errors import LLMInvalidRequestError
from penguiflow.llm.fallback import ModelFallbackConfig
from penguiflow.llm.profiles import ModelProfile
from penguiflow.llm.protocol import (
    INLINE_MULTIMODAL_DATA_LIMIT_BYTES,
    NativeLLMAdapter,
    create_native_adapter,
)
from penguiflow.llm.types import (
    AudioPart,
    CompletionResponse,
    ImagePart,
    LLMMessage,
    TextPart,
    ToolCallPart,
    Usage,
)


class TestNativeLLMAdapter:
    @pytest.fixture
    def mock_provider(self) -> MagicMock:
        """Create a mock provider."""
        provider = MagicMock()
        provider.model = "test-model"
        provider.provider_name = "test"
        provider.profile = ModelProfile()
        provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )
        return provider

    def test_init(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "gpt-4o"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("gpt-4o", temperature=0.5)

            mock_create.assert_called_once_with(
                "gpt-4o",
                api_key=None,
                base_url=None,
            )
            assert adapter._temperature == 0.5

    def test_init_with_options(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "gpt-4o"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "gpt-4o",
                api_key="test-key",
                base_url="https://api.example.com",
                max_retries=5,
                timeout_s=360.0,
                json_schema_mode=False,
            )

            mock_create.assert_called_once_with(
                "gpt-4o",
                api_key="test-key",
                base_url="https://api.example.com",
            )
            assert adapter._max_retries == 5
            assert adapter._timeout_s == 360.0
            assert adapter._json_schema_mode is False

    @pytest.mark.asyncio
    async def test_complete(self, mock_provider: MagicMock) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            content, cost = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

            assert content == '{"result": "ok"}'
            mock_provider.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_falls_back_to_tool_call_arguments_when_text_empty(self, mock_provider: MagicMock) -> None:
        mock_provider.complete.return_value = CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[
                    ToolCallPart(
                        name="json_output",
                        arguments_json='{"next_node":"final_response","args":{"answer":"hi"}}',
                    )
                ],
            ),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            content, _ = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])
            assert content.startswith("{")
            assert '"next_node"' in content

    @pytest.mark.asyncio
    async def test_complete_with_response_format(self, mock_provider: MagicMock) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", json_schema_mode=True)
            content, cost = await adapter.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "test_schema",
                        "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
                    },
                },
            )

            assert content is not None
            call_args = mock_provider.complete.call_args[0][0]
            assert call_args.structured_output is not None
            assert call_args.structured_output.name == "test_schema"

    @pytest.mark.asyncio
    async def test_image_part_reaches_provider_when_profile_supports_it(self, mock_provider: MagicMock) -> None:
        mock_provider.profile = ModelProfile(supports_image_input=True)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            await adapter.complete(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            TextPart(text="Describe this"),
                            ImagePart(data=b"\x89PNG", media_type="image/png"),
                        ],
                    }
                ]
            )

            request = mock_provider.complete.call_args[0][0]
            assert isinstance(request.messages[0].parts[1], ImagePart)

    @pytest.mark.asyncio
    async def test_unsupported_image_input_fails_loudly(self, mock_provider: MagicMock) -> None:
        mock_provider.profile = ModelProfile(supports_image_input=False)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            with pytest.raises(LLMInvalidRequestError, match="does not support image input"):
                await adapter.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [ImagePart(data=b"\x89PNG", media_type="image/png")],
                        }
                    ]
                )
            mock_provider.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_multimodal_threshold_fails_loudly(self, mock_provider: MagicMock) -> None:
        mock_provider.profile = ModelProfile(supports_image_input=True)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            with pytest.raises(LLMInvalidRequestError, match="inline limit"):
                await adapter.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                ImagePart(
                                    data=b"x" * (INLINE_MULTIMODAL_DATA_LIMIT_BYTES + 1),
                                    media_type="image/png",
                                )
                            ],
                        }
                    ]
                )
            mock_provider.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_multimodal_threshold_is_configurable(self, mock_provider: MagicMock) -> None:
        mock_provider.profile = ModelProfile(supports_image_input=True)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", multimodal_inline_data_limit_bytes=4)
            await adapter.complete(
                messages=[
                    {
                        "role": "user",
                        "content": [ImagePart(data=b"1234", media_type="image/png")],
                    }
                ]
            )
            with pytest.raises(LLMInvalidRequestError, match="4 byte inline limit"):
                await adapter.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [ImagePart(data=b"12345", media_type="image/png")],
                        }
                    ]
                )

    def test_negative_inline_multimodal_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match="multimodal_inline_data_limit_bytes"):
            NativeLLMAdapter("test-model", multimodal_inline_data_limit_bytes=-1)

    def test_full_reasoning_display_rejected(self) -> None:
        with pytest.raises(ValueError, match="reasoning_display"):
            NativeLLMAdapter("test-model", reasoning_display="full")

    @pytest.mark.asyncio
    async def test_unsupported_audio_input_fails_loudly(self, mock_provider: MagicMock) -> None:
        mock_provider.profile = ModelProfile(supports_audio_input=False)
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            with pytest.raises(LLMInvalidRequestError, match="does not support audio input"):
                await adapter.complete(
                    messages=[
                        {
                            "role": "user",
                            "content": [AudioPart(data=b"RIFF", media_type="audio/wav")],
                        }
                    ]
                )
            mock_provider.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_with_json_object_format(self, mock_provider: MagicMock) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", json_schema_mode=True)
            content, cost = await adapter.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format={"type": "json_object"},
            )

            assert content is not None
            # json_object mode creates a generic JSON output schema
            call_args = mock_provider.complete.call_args[0][0]
            assert call_args.structured_output is not None
            assert call_args.structured_output.name == "json_response"
            assert call_args.structured_output.json_schema == {"type": "object"}
            assert call_args.structured_output.strict is False

    def test_build_request_normalizes_composed_schema_root_type(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "test-model"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", json_schema_mode=True)
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])

            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "allOf": [
                                {
                                    "type": "object",
                                    "properties": {"next_node": {"type": "string"}},
                                    "required": ["next_node"],
                                },
                                {
                                    "if": {
                                        "properties": {"next_node": {"const": "final_response"}},
                                    },
                                    "then": {
                                        "properties": {
                                            "args": {
                                                "type": "object",
                                                "properties": {"answer": {"type": "string"}},
                                                "required": ["answer"],
                                            }
                                        }
                                    },
                                },
                            ]
                        },
                    },
                },
            )

            assert request.structured_output is not None
            assert request.structured_output.json_schema["type"] == "object"
            assert "allOf" in request.structured_output.json_schema

    def test_build_request_openrouter_non_allowlisted_route_uses_json_object(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "anthropic/claude-sonnet-4.5"
            mock_provider.provider_name = "openrouter"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("openrouter/anthropic/claude-sonnet-4.5", json_schema_mode=True)
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])

            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is not None
            assert request.structured_output.name == "json_response"
            assert request.structured_output.json_schema == {"type": "object"}
            assert request.structured_output.strict is False

    def test_build_request_openrouter_openai_route_keeps_json_schema(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "openai/gpt-5"
            mock_provider.provider_name = "openrouter"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("openrouter/openai/gpt-5", json_schema_mode=True)
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])

            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is not None
            assert request.structured_output.name == "planner_action"
            assert request.structured_output.strict is True

    def test_build_request_openrouter_stepfun_route_uses_text_mode(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "stepfun/step-3.5-flash"
            mock_provider.provider_name = "openrouter"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("openrouter/stepfun/step-3.5-flash", json_schema_mode=True)
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])

            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is None

    def test_build_request_anthropic_claude_with_reasoning_disables_structured_output(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "claude-sonnet-4-5"
            mock_provider.provider_name = "anthropic"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "anthropic/claude-sonnet-4-5",
                json_schema_mode=True,
                use_native_reasoning=True,
                reasoning_effort="medium",
            )
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])
            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is None
            assert request.extra == {"reasoning_effort": "medium"}

    def test_build_request_databricks_claude_with_reasoning_disables_structured_output(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "databricks-claude-sonnet-4-5"
            mock_provider.provider_name = "databricks"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "databricks/databricks-claude-sonnet-4-5",
                json_schema_mode=True,
                use_native_reasoning=True,
                reasoning_effort="high",
            )
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])
            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is None
            assert request.extra == {"reasoning_effort": "high"}

    def test_build_request_databricks_sonnet_5_uses_default_reasoning_display(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "databricks-claude-sonnet-5"
            mock_provider.provider_name = "databricks"
            mock_provider.profile = ModelProfile(reasoning_display_default="omitted")
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "databricks/databricks-claude-sonnet-5",
                use_native_reasoning=True,
                reasoning_effort="high",
            )
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])
            request = adapter._build_request(messages, None)

            assert request.extra == {"reasoning_effort": "high", "reasoning_display": "omitted"}

    def test_build_request_anthropic_claude_without_reasoning_keeps_structured_output(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "claude-sonnet-4-5"
            mock_provider.provider_name = "anthropic"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "anthropic/claude-sonnet-4-5",
                json_schema_mode=True,
                use_native_reasoning=False,
                reasoning_effort="medium",
            )
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])
            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is not None
            assert request.structured_output.name == "planner_action"
            assert request.extra is None

    def test_build_request_nim_structured_keeps_reasoning_effort_by_default(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "qwen/qwen3.5-397b-a17b"
            mock_provider.provider_name = "nim"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "nim/qwen/qwen3.5-397b-a17b",
                json_schema_mode=True,
                use_native_reasoning=True,
                reasoning_effort="high",
            )
            messages = adapter._convert_messages([{"role": "user", "content": "test"}])

            request = adapter._build_request(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
            )

            assert request.structured_output is not None
            assert request.structured_output.name == "json_response"
            assert request.extra is not None
            assert request.extra["reasoning_effort"] == "high"

    def test_convert_messages(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "test-model"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            messages = [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]

            result = adapter._convert_messages(messages)

            assert len(result) == 3
            assert result[0].role == "system"
            assert result[0].text == "You are helpful."
            assert result[1].role == "user"
            assert result[2].role == "assistant"

    def test_convert_messages_invalid_role(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "test-model"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model")
            messages = [{"role": "invalid_role", "content": "test"}]

            result = adapter._convert_messages(messages)

            # Invalid role should be mapped to "user"
            assert result[0].role == "user"

    @pytest.mark.asyncio
    async def test_complete_with_reasoning_content(self, mock_provider: MagicMock) -> None:
        mock_provider.complete.return_value = CompletionResponse(
            message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            reasoning_content="I thought about it...",
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            reasoning_chunks: list[tuple[str, bool]] = []

            def on_reasoning(text: str, done: bool) -> None:
                reasoning_chunks.append((text, done))

            adapter = NativeLLMAdapter("test-model")
            content, cost = await adapter.complete(
                messages=[{"role": "user", "content": "test"}],
                on_reasoning_chunk=on_reasoning,
            )

            assert content == '{"result": "ok"}'
            assert len(reasoning_chunks) == 2
            assert reasoning_chunks[0] == ("I thought about it...", False)
            assert reasoning_chunks[1] == ("", True)

    @pytest.mark.asyncio
    async def test_complete_reorders_nim_system_messages_before_request(self, mock_provider: MagicMock) -> None:
        mock_provider.provider_name = "nim"
        mock_provider.model = "qwen/qwen3.5-397b-a17b"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text='{"ok": true}')]),
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider
            adapter = NativeLLMAdapter("nim/qwen/qwen3.5-397b-a17b")
            await adapter.complete(
                messages=[
                    {"role": "user", "content": "Hello"},
                    {"role": "system", "content": "System guidance"},
                ]
            )

            request = mock_provider.complete.call_args.args[0]
            assert [msg.role for msg in request.messages] == ["system", "user"]

    @pytest.mark.asyncio
    async def test_complete_collapses_multiple_nim_system_messages(self, mock_provider: MagicMock) -> None:
        mock_provider.provider_name = "nim"
        mock_provider.model = "qwen/qwen3.5-397b-a17b"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text='{"ok": true}')]),
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider
            adapter = NativeLLMAdapter("nim/qwen/qwen3.5-397b-a17b")
            await adapter.complete(
                messages=[
                    {"role": "system", "content": "System A"},
                    {"role": "user", "content": "Hello"},
                    {"role": "system", "content": "System B"},
                ]
            )

            request = mock_provider.complete.call_args.args[0]
            assert [msg.role for msg in request.messages] == ["system", "user"]
            assert "System A" in request.messages[0].text
            assert "System B" in request.messages[0].text

    @pytest.mark.asyncio
    async def test_complete_downgrades_schema_after_invalid_json_schema_error(self, mock_provider: MagicMock) -> None:
        mock_provider.complete = AsyncMock(
            side_effect=[
                RuntimeError("invalid_json_schema: strict provider rejected schema"),
                CompletionResponse(
                    message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
                    usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                ),
            ]
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", json_schema_mode=True)
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "args": {"type": "object"},
                            },
                            "required": ["args"],
                        },
                    },
                },
            )

            assert content == '{"result": "ok"}'
            assert mock_provider.complete.call_count == 2
            retry_request = mock_provider.complete.call_args_list[1].args[0]
            assert retry_request.structured_output is not None
            assert retry_request.structured_output.name == "json_response"
            assert retry_request.structured_output.json_schema == {"type": "object"}
            assert retry_request.structured_output.strict is False

    @pytest.mark.asyncio
    async def test_complete_downgrades_json_object_to_text_mode(self, mock_provider: MagicMock) -> None:
        mock_provider.provider_name = "openrouter"
        mock_provider.model = "meta-llama/llama-3.3-70b-instruct"
        mock_provider.complete = AsyncMock(
            side_effect=[
                RuntimeError("response_format json_object is not supported for this model"),
                CompletionResponse(
                    message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
                    usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                ),
            ]
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("openrouter/meta-llama/llama-3.3-70b-instruct", json_schema_mode=True)
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format={"type": "json_object"},
            )

            assert content == '{"result": "ok"}'
            assert mock_provider.complete.call_count == 2
            retry_request = mock_provider.complete.call_args_list[1].args[0]
            assert retry_request.structured_output is None

    @pytest.mark.asyncio
    async def test_complete_nim_structured_disables_reasoning_after_error(self, mock_provider: MagicMock) -> None:
        mock_provider.provider_name = "nim"
        mock_provider.model = "qwen/qwen3.5-397b-a17b"
        mock_provider.complete = AsyncMock(
            side_effect=[
                RuntimeError("provider rejected first structured request"),
                CompletionResponse(
                    message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
                    usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                    reasoning_content="hidden reasoning",
                ),
            ]
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            reasoning_chunks: list[tuple[str, bool]] = []

            def on_reasoning(text: str, done: bool) -> None:
                reasoning_chunks.append((text, done))

            adapter = NativeLLMAdapter(
                "nim/qwen/qwen3.5-397b-a17b",
                json_schema_mode=True,
                use_native_reasoning=True,
                reasoning_effort="high",
            )
            content, _ = await adapter.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "planner_action",
                        "schema": {
                            "type": "object",
                            "properties": {"next_node": {"type": "string"}},
                            "required": ["next_node"],
                        },
                    },
                },
                on_reasoning_chunk=on_reasoning,
            )

            assert content == '{"result": "ok"}'
            assert mock_provider.complete.call_count == 2
            first_request = mock_provider.complete.call_args_list[0].args[0]
            second_request = mock_provider.complete.call_args_list[1].args[0]
            assert first_request.extra is not None
            assert first_request.extra["reasoning_effort"] == "high"
            assert second_request.extra is None
            assert reasoning_chunks == []


class TestCreateNativeAdapter:
    def test_create_with_string_model(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter("gpt-4o", temperature=0.7)

            mock_adapter.assert_called_once_with(
                "gpt-4o",
                api_key=None,
                base_url=None,
                temperature=0.7,
                json_schema_mode=True,
                max_retries=3,
                timeout_s=360.0,
                streaming_enabled=True,
                use_native_reasoning=True,
                reasoning_effort=None,
                reasoning_display=None,
                trace_sink=None,
                transport=None,
                multimodal_inline_data_limit_bytes=INLINE_MULTIMODAL_DATA_LIMIT_BYTES,
            )

    def test_create_with_dict_config(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter(
                {"model": "gpt-4o", "api_key": "test-key", "base_url": "https://api.example.com"},
                temperature=0.5,
            )

            mock_adapter.assert_called_once_with(
                "gpt-4o",
                api_key="test-key",
                base_url="https://api.example.com",
                temperature=0.5,
                json_schema_mode=True,
                max_retries=3,
                timeout_s=360.0,
                streaming_enabled=True,
                use_native_reasoning=True,
                reasoning_effort=None,
                reasoning_display=None,
                trace_sink=None,
                transport=None,
                multimodal_inline_data_limit_bytes=INLINE_MULTIMODAL_DATA_LIMIT_BYTES,
            )

    def test_create_with_nim_dict_config(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter(
                {
                    "model": "nim/qwen/qwen3.5-397b-a17b",
                    "api_key": "nim-key",
                    "base_url": "https://integrate.api.nvidia.com/v1",
                }
            )

            mock_adapter.assert_called_once_with(
                "nim/qwen/qwen3.5-397b-a17b",
                api_key="nim-key",
                base_url="https://integrate.api.nvidia.com/v1",
                temperature=None,
                json_schema_mode=True,
                max_retries=3,
                timeout_s=360.0,
                streaming_enabled=True,
                use_native_reasoning=True,
                reasoning_effort=None,
                reasoning_display=None,
                trace_sink=None,
                transport=None,
                multimodal_inline_data_limit_bytes=INLINE_MULTIMODAL_DATA_LIMIT_BYTES,
            )

    def test_create_with_dict_config_multimodal_limit(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter(
                {
                    "model": "gpt-4o",
                    "multimodal_inline_data_limit_bytes": 1234,
                }
            )

            call_kwargs = mock_adapter.call_args.kwargs
            assert call_kwargs["multimodal_inline_data_limit_bytes"] == 1234

    def test_create_with_api_base_alias(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter(
                {"model": "gpt-4o", "api_base": "https://legacy.api.com"},
            )

            # api_base should be mapped to base_url
            call_kwargs = mock_adapter.call_args[1]
            assert call_kwargs["base_url"] == "https://legacy.api.com"

    def test_create_with_streaming(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter("gpt-4o", streaming_enabled=True)

            call_kwargs = mock_adapter.call_args[1]
            assert call_kwargs["streaming_enabled"] is True

    def test_create_with_reasoning_params(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter(
                "o1",
                use_native_reasoning=True,
                reasoning_effort="high",
            )

            call_kwargs = mock_adapter.call_args[1]
            assert call_kwargs["use_native_reasoning"] is True
            assert call_kwargs["reasoning_effort"] == "high"

    def test_create_extracts_extra_kwargs_from_dict(self) -> None:
        with patch("penguiflow.llm.protocol.NativeLLMAdapter") as mock_adapter:
            create_native_adapter(
                {"model": "gpt-4o", "custom_param": "value"},
            )

            call_kwargs = mock_adapter.call_args[1]
            assert call_kwargs.get("custom_param") == "value"

    def test_create_with_fallback_threads_multimodal_limit(self) -> None:
        with patch("penguiflow.llm.fallback.FallbackLLMClient") as mock_fallback:
            create_native_adapter(
                "gpt-4o",
                fallback=ModelFallbackConfig(models=["gpt-4o-mini"]),
                multimodal_inline_data_limit_bytes=2048,
            )

            call_kwargs = mock_fallback.call_args.kwargs
            assert call_kwargs["multimodal_inline_data_limit_bytes"] == 2048

    def test_create_rejects_full_reasoning_display_with_fallback(self) -> None:
        with pytest.raises(ValueError, match="reasoning_display"):
            create_native_adapter(
                "gpt-4o",
                fallback=ModelFallbackConfig(models=["gpt-4o-mini"]),
                reasoning_display="full",
            )


class TestNativeLLMAdapterStreaming:
    """Additional streaming tests for NativeLLMAdapter."""

    @pytest.fixture
    def mock_provider(self) -> MagicMock:
        """Create a mock provider."""
        provider = MagicMock()
        provider.model = "test-model"
        provider.provider_name = "test"
        provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text='{"result": "ok"}')]),
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )
        return provider

    @pytest.mark.asyncio
    async def test_streaming_callback_wrapper(self, mock_provider: MagicMock) -> None:
        """Test that streaming callback wrapper forwards events correctly."""
        from penguiflow.llm.types import StreamEvent

        received_chunks: list[tuple[str, bool]] = []
        received_reasoning: list[tuple[str, bool]] = []

        def on_chunk(text: str, done: bool) -> None:
            received_chunks.append((text, done))

        def on_reasoning(text: str, done: bool) -> None:
            received_reasoning.append((text, done))

        # Mock provider that calls the stream callback
        async def mock_complete(request: Any, **kwargs: Any) -> CompletionResponse:
            callback = kwargs.get("on_stream_event")
            if callback:
                callback(StreamEvent(delta_text="Hello"))
                callback(StreamEvent(delta_text=" world"))
                callback(StreamEvent(delta_reasoning="Because..."))
                callback(StreamEvent(done=True))
            return CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Hello world")]),
                usage=Usage(input_tokens=5, output_tokens=2, total_tokens=7),
            )

        mock_provider.complete = mock_complete

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", streaming_enabled=True)
            await adapter.complete(
                messages=[{"role": "user", "content": "Hi"}],
                stream=True,
                on_stream_chunk=on_chunk,
                on_reasoning_chunk=on_reasoning,
            )

        assert ("Hello", False) in received_chunks
        assert (" world", False) in received_chunks
        assert ("", True) in received_chunks
        assert ("Because...", False) in received_reasoning
        assert ("", True) in received_reasoning

    @pytest.mark.asyncio
    async def test_streaming_disabled_ignores_callback(self, mock_provider: MagicMock) -> None:
        """Test that streaming disabled doesn't use callback."""
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", streaming_enabled=False)

            called = False

            def on_chunk(text: str, done: bool) -> None:
                nonlocal called
                called = True

            await adapter.complete(
                messages=[{"role": "user", "content": "Hi"}],
                stream=True,  # Requested but disabled
                on_stream_chunk=on_chunk,
            )

            # stream should be False when streaming_enabled=False
            call_kwargs = mock_provider.complete.call_args[1]
            assert call_kwargs["stream"] is False


class TestNativeLLMAdapterStreamEvents:
    @pytest.mark.asyncio
    async def test_stream_events_yields_provider_events(self) -> None:
        from penguiflow.llm.types import StreamEvent

        async def mock_complete(request: Any, **kwargs: Any) -> CompletionResponse:
            callback = kwargs.get("on_stream_event")
            assert kwargs.get("stream") is True
            assert callback is not None
            callback(StreamEvent(delta_text="Hello"))
            callback(StreamEvent(delta_reasoning="Thinking..."))
            callback(StreamEvent(done=True))
            return CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Hello")]),
                usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
                reasoning_content="Thinking...",
            )

        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.complete = mock_complete

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider
            adapter = NativeLLMAdapter("test-model", streaming_enabled=True)

            events: list[Any] = []
            async for event in adapter.stream_events(messages=[{"role": "user", "content": "Hi"}]):
                events.append(event)

        assert any(e.delta_text == "Hello" for e in events)
        assert any(e.delta_reasoning == "Thinking..." for e in events)
        assert events[-1].done is True

    @pytest.mark.asyncio
    async def test_stream_events_raises_when_disabled(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.complete = AsyncMock()

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider
            adapter = NativeLLMAdapter("test-model", streaming_enabled=False)

            with pytest.raises(RuntimeError, match="Streaming is disabled"):
                async for _ in adapter.stream_events(messages=[{"role": "user", "content": "Hi"}]):
                    pass


class TestNativeLLMAdapterBuildRequest:
    """Tests for request building in NativeLLMAdapter."""

    def test_build_request_no_response_format(self) -> None:
        """Test request building without response format."""
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "test-model"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", temperature=0.5)
            messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

            request = adapter._build_request(messages, None)

            assert request.model == "test-model"
            assert request.temperature == 0.5
            assert request.structured_output is None

    def test_build_request_json_schema_mode_disabled(self) -> None:
        """Test request building with json_schema_mode disabled."""
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "test-model"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("test-model", json_schema_mode=False)
            messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

            # Pass response_format but mode is disabled
            request = adapter._build_request(
                messages,
                {"type": "json_schema", "json_schema": {"name": "test", "schema": {}}},
            )

            # structured_output should be None when mode is disabled
            assert request.structured_output is None

    def test_build_request_with_reasoning_effort(self) -> None:
        """Test request building includes reasoning effort in extra."""
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "o1"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "o1",
                use_native_reasoning=True,
                reasoning_effort="medium",
            )
            messages = [LLMMessage(role="user", parts=[TextPart(text="Think")])]

            request = adapter._build_request(messages, None)

            assert request.extra is not None
            assert request.extra["reasoning_effort"] == "medium"

    def test_build_request_with_reasoning_effort_for_nim_model(self) -> None:
        """NIM models should use the same canonical reasoning_effort request knob."""
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "qwen/qwen3.5-397b-a17b"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "nim/qwen/qwen3.5-397b-a17b",
                use_native_reasoning=True,
                reasoning_effort="high",
            )
            messages = [LLMMessage(role="user", parts=[TextPart(text="Think")])]

            request = adapter._build_request(messages, None)

            assert request.extra is not None
            assert request.extra["reasoning_effort"] == "high"

    def test_build_request_openrouter_xai_injects_reasoning_enabled(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "x-ai/grok-4.1-fast"
            mock_provider.provider_name = "openrouter"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "openrouter/x-ai/grok-4.1-fast",
                use_native_reasoning=True,
            )
            messages = [LLMMessage(role="user", parts=[TextPart(text="Think")])]

            request = adapter._build_request(messages, None)

            assert request.extra is not None
            assert request.extra["reasoning_enabled"] is True
            assert "reasoning_effort" not in request.extra

    def test_build_request_openrouter_xai_reasoning_effort_off_sets_disabled(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "x-ai/grok-4.1-fast"
            mock_provider.provider_name = "openrouter"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "openrouter/x-ai/grok-4.1-fast",
                use_native_reasoning=True,
                reasoning_effort="off",
            )
            messages = [LLMMessage(role="user", parts=[TextPart(text="Think")])]

            request = adapter._build_request(messages, None)

            assert request.extra is not None
            assert request.extra["reasoning_enabled"] is False
            assert request.extra["reasoning_effort"] == "off"

    def test_build_request_openrouter_non_xai_does_not_inject_reasoning_enabled(self) -> None:
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "anthropic/claude-sonnet-4.5"
            mock_provider.provider_name = "openrouter"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "openrouter/anthropic/claude-sonnet-4.5",
                use_native_reasoning=True,
            )
            messages = [LLMMessage(role="user", parts=[TextPart(text="Think")])]

            request = adapter._build_request(messages, None)

            assert request.extra is None

    def test_build_request_no_reasoning_when_disabled(self) -> None:
        """Test request building omits reasoning when disabled."""
        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_provider = MagicMock()
            mock_provider.model = "o1"
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "o1",
                use_native_reasoning=False,
                reasoning_effort="medium",
            )
            messages = [LLMMessage(role="user", parts=[TextPart(text="Think")])]

            request = adapter._build_request(messages, None)

            # extra should be None when use_native_reasoning is False
            assert request.extra is None


class TestNativeLLMAdapterCost:
    """Tests for cost calculation in adapter."""

    @pytest.mark.asyncio
    async def test_cost_from_usage(self) -> None:
        """Test cost calculated from usage."""
        mock_provider = MagicMock()
        mock_provider.model = "gpt-4o"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Response")]),
                usage=Usage(input_tokens=1000, output_tokens=500, total_tokens=1500),
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("gpt-4o")
            content, cost = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

            # Cost should be positive for known model
            assert cost > 0

    @pytest.mark.asyncio
    async def test_estimated_cost_no_usage_for_known_model(self) -> None:
        """Test cost estimation when usage is missing but pricing is known."""
        mock_provider = MagicMock()
        mock_provider.model = "claude-haiku-4.5"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Response " * 200)]),
                usage=Usage.zero(),
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("claude-haiku-4.5")
            content, cost = await adapter.complete(messages=[{"role": "user", "content": "Hello " * 500}])

            assert cost > 0.0

    @pytest.mark.asyncio
    async def test_zero_cost_no_usage_for_unknown_model(self) -> None:
        """Test zero cost when usage is missing and pricing is unknown."""
        mock_provider = MagicMock()
        mock_provider.model = "totally-unknown-model"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Response")]),
                usage=None,
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("totally-unknown-model")
            content, cost = await adapter.complete(messages=[{"role": "user", "content": "Hello"}])

            assert cost == 0.0

    @pytest.mark.asyncio
    async def test_estimated_cost_in_native_tool_call_mode_when_usage_is_zero(self) -> None:
        """Native tool-calling should estimate cost when a priced provider returns 0/0 usage."""
        mock_provider = MagicMock()
        mock_provider.model = "databricks-claude-opus-4-6"
        mock_provider.provider_name = "databricks"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(
                    role="assistant",
                    parts=[
                        ToolCallPart(
                            name="resolve_analysis_scope",
                            arguments_json='{"advertiser":"Tubi","period_grain":"quarter","period_value":"2026-Q1"}',
                            call_id="call_1",
                        )
                    ],
                ),
                usage=Usage.zero(),
                reasoning_content="check scope",
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter("databricks-claude-opus-4-6")
            result = await adapter.complete_with_tools(
                messages=[{"role": "user", "content": "Analyze Tubi for Q1 2026"}],
                tools=[],
            )

            assert result.cost > 0.0

    @pytest.mark.asyncio
    async def test_complete_with_tools_uses_databricks_sonnet_5_reasoning_defaults(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "databricks-claude-sonnet-5"
        mock_provider.provider_name = "databricks"
        mock_provider.profile = ModelProfile(reasoning_display_default="omitted")
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Response")]),
                usage=Usage.zero(),
            )
        )

        with patch("penguiflow.llm.protocol.create_provider") as mock_create:
            mock_create.return_value = mock_provider

            adapter = NativeLLMAdapter(
                "databricks/databricks-claude-sonnet-5",
                reasoning_effort="high",
            )
            await adapter.complete_with_tools(
                messages=[{"role": "user", "content": "Think"}],
                tools=[],
            )

            request = mock_provider.complete.call_args.args[0]
            assert request.extra == {"reasoning_effort": "high", "reasoning_display": "omitted"}
