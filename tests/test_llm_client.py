"""Tests for penguiflow.planner.llm module with mocked litellm."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from penguiflow.planner.llm import (
    _artifact_placeholder,
    _build_minimal_planner_schema,
    _coerce_llm_response,
    _extract_json_from_text,
    _inline_defs,
    _LiteLLMJSONClient,
    _response_format_policy,
    _sanitize_json_schema,
    _supports_reasoning,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TestExtractJsonFromText:
    """Tests for extract json from text."""

    def test_plain_json(self) -> None:
        """Plain JSON."""
        assert (
            _extract_json_from_text('{"key": "value"}') == '{"key": "value"}'
        )

    def test_fenced_json(self) -> None:
        """Fenced JSON."""
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json_from_text(text) == '{"key": "value"}'

    def test_json_with_surrounding_text(self) -> None:
        """JSON with surrounding text."""
        text = 'Here is the result: {"key": "value"} end'
        assert _extract_json_from_text(text) == '{"key": "value"}'

    def test_no_json(self) -> None:
        """No JSON."""
        assert _extract_json_from_text("no json here") == "no json here"


class TestCoerceLlmResponse:
    """Tests for coerce llm response."""

    def test_string_response(self) -> None:
        """String response."""
        content, cost = _coerce_llm_response('{"result": 1}')
        assert content == '{"result": 1}'
        assert cost == 0.0

    def test_tuple_response(self) -> None:
        """Tuple response."""
        content, cost = _coerce_llm_response(('{"result": 1}', 0.05))
        assert content == '{"result": 1}'
        assert cost == 0.05

    def test_invalid_type_raises(self) -> None:
        """Invalid type raises."""
        with pytest.raises(TypeError):
            _coerce_llm_response(123)


class TestResponseFormatPolicy:
    """Tests for response format policy."""

    def test_anthropic_returns_json_object(self) -> None:
        """Anthropic returns JSON object."""
        assert _response_format_policy("anthropic/claude-3") == "json_object"
        assert _response_format_policy("claude-sonnet") == "json_object"

    def test_openai_returns_json_object(self) -> None:
        """OpenAI returns JSON object."""
        assert _response_format_policy("gpt-4o") == "json_object"
        assert _response_format_policy("openai/gpt-4") == "json_object"

    def test_gemini_returns_json_object(self) -> None:
        """Gemini returns JSON object."""
        assert (
            _response_format_policy("gemini/gemini-1.5-pro") == "json_object"
        )

    def test_maverick_returns_json_object(self) -> None:
        """Maverick returns JSON object."""
        assert _response_format_policy("maverick-model") == "json_object"

    def test_unknown_returns_sanitized_schema(self) -> None:
        """Unknown returns sanitized schema."""
        assert (
            _response_format_policy("some-unknown-model") == "sanitized_schema"
        )


class TestSanitizeJsonSchema:
    """Tests for sanitize json schema."""

    def test_removes_constraints(self) -> None:
        """Removes constraints."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 100},
                "age": {"type": "integer", "minimum": 0, "maximum": 150},
            },
        }
        result = _sanitize_json_schema(schema)
        assert "minLength" not in result["properties"]["name"]
        assert "maxLength" not in result["properties"]["name"]
        assert "minimum" not in result["properties"]["age"]
        assert "maximum" not in result["properties"]["age"]

    def test_strict_mode_adds_additional_properties_false(self) -> None:
        """Strict mode adds additional properties false."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = _sanitize_json_schema(schema, strict_mode=True)
        assert result["additionalProperties"] is False

    def test_handles_nested_schemas(self) -> None:
        """Handles nested schemas."""
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "pattern": ".*"}
                    },
                }
            },
        }
        result = _sanitize_json_schema(schema, strict_mode=True)
        assert result["additionalProperties"] is False
        assert result["properties"]["nested"]["additionalProperties"] is False
        assert (
            "pattern"
            not in result["properties"]["nested"]["properties"]["value"]
        )


class TestLiteLLMJSONClient:
    """Tests for lite LLMJSON client."""

    @pytest.fixture
    def mock_litellm(self) -> MagicMock:
        """Create a mock litellm module."""
        mock = MagicMock()
        mock.acompletion = AsyncMock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": '{"thought": "test", "next_node": null}'
                        }
                    }
                ],
                "usage": {"total_tokens": 100},
                "_hidden_params": {"response_cost": 0.001},
            }
        )
        return mock

    @pytest.mark.asyncio
    async def test_complete_success(self, mock_litellm: MagicMock) -> None:
        """Complete success."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
            )
            content, cost = await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )
            assert '{"thought"' in content
            assert cost == 0.001
            mock_litellm.acompletion.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_with_json_schema_mode(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete with JSON schema mode."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=True,
            )
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "test_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    },
                },
            }
            content, _cost = await client.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format=response_format,
            )
            assert content is not None
            # Verify response_format was set (downgraded to json_object for
            # gpt-4o)
            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert "response_format" in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_with_dict_llm_config(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete with dict LLM config."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                {"model": "gpt-4o", "api_key": "test-key"},
                temperature=0.5,
                json_schema_mode=False,
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )
            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert call_kwargs["model"] == "gpt-4o"
            assert call_kwargs["api_key"] == "test-key"

    @pytest.mark.asyncio
    async def test_complete_strips_databricks_unsupported_params(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete strips unsupported params from databricks dict config."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                {
                    "model": "databricks/databricks-claude-sonnet-5",
                    "api_key": "test-key",
                    "temperature": 0.5,
                    "top_p": 0.9,
                    "top_k": 10,
                },
                temperature=0.0,
                json_schema_mode=False,
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["model"] == "databricks/databricks-claude-sonnet-5"
        assert call_kwargs["api_key"] == "test-key"
        assert not {"temperature", "top_p", "top_k"} & call_kwargs.keys()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model",
        [
            "databricks/databricks-claude-opus-4-7",
            "databricks/databricks-claude-opus-4-8",
            "databricks/databricks-claude-sonnet-5",
        ],
    )
    async def test_complete_omits_temperature_for_databricks_claude_models(
        self, mock_litellm: MagicMock, model: str
    ) -> None:
        """Complete omits temperature for Databricks claude models."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                model, temperature=0.0, json_schema_mode=False
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model",
        [
            "databricks/databricks-claude-opus-4-7",
            "databricks/databricks-claude-opus-4-8",
            "databricks/databricks-claude-sonnet-5",
        ],
    )
    async def test_complete_databricks_claude_adaptive_thinking(
        self, mock_litellm: MagicMock, model: str
    ) -> None:
        """Reasoning effort maps to adaptive thinking on Databricks Claude."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                model,
                temperature=0.0,
                json_schema_mode=False,
                reasoning_effort="low",
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["thinking"] == {
            "type": "adaptive",
            "display": "omitted",
        }
        assert call_kwargs["output_config"] == {"effort": "low"}
        assert "reasoning_effort" not in call_kwargs

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model",
        [
            "databricks/databricks-claude-opus-4-8",
            "databricks/databricks-claude-sonnet-5",
        ],
    )
    @pytest.mark.parametrize(
        "reasoning_effort", ["none", "off", "disabled", "false", "0"]
    )
    async def test_complete_disables_thinking_for_databricks_claude_sonnet_5(
        self, mock_litellm: MagicMock, model: str, reasoning_effort: str
    ) -> None:
        """Complete disables thinking for Databricks claude sonnet 5."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                model,
                temperature=0.0,
                json_schema_mode=False,
                reasoning_effort=reasoning_effort,
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["thinking"] == {"type": "disabled"}
        assert "output_config" not in call_kwargs
        assert "reasoning_effort" not in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_sonnet_5_maps_reasoning_display(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete maps reasoning display for databricks claude sonnet 5."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "databricks/databricks-claude-sonnet-5",
                temperature=0.0,
                json_schema_mode=False,
                reasoning_effort="high",
                reasoning_display="summarized",
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["thinking"] == {
            "type": "adaptive",
            "display": "summarized",
        }
        assert call_kwargs["output_config"] == {"effort": "high"}
        assert "reasoning_display" not in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_sonnet_5_omits_reasoning_display(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete omits reasoning display for databricks claude sonnet 5."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "databricks/databricks-claude-sonnet-5",
                temperature=0.0,
                json_schema_mode=False,
                reasoning_effort="high",
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )

        call_kwargs = mock_litellm.acompletion.call_args[1]
        assert call_kwargs["thinking"] == {
            "type": "adaptive",
            "display": "omitted",
        }
        assert call_kwargs["output_config"] == {"effort": "high"}

    @pytest.mark.asyncio
    async def test_complete_retries_on_timeout(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete retries on timeout."""
        call_count = 0

        async def timeout_then_succeed(
            **_kwargs: str | float | list[object],
        ) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                msg = "Request timed out"
                raise TimeoutError(msg)
            return {
                "choices": [{"message": {"content": '{"result": "ok"}'}}],
                "usage": {"total_tokens": 50},
                "_hidden_params": {"response_cost": 0.0005},
            }

        mock_litellm.acompletion = AsyncMock(side_effect=timeout_then_succeed)

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                max_retries=3,
                timeout_s=1.0,
            )
            content, _cost = await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )
            assert content == '{"result": "ok"}'
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_complete_exhausts_retries_on_timeout(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete exhausts retries on timeout."""
        mock_litellm.acompletion = AsyncMock(
            side_effect=TimeoutError("timeout")
        )

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                max_retries=2,
                timeout_s=0.1,
            )
            with pytest.raises(RuntimeError, match="failed after 2 retries"):
                await client.complete(
                    messages=[{"role": "user", "content": "test"}]
                )

    @pytest.mark.asyncio
    async def test_complete_raises_on_empty_content(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete raises on empty content."""
        mock_litellm.acompletion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": None}}],
                "usage": {},
                "_hidden_params": {},
            }
        )

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
            )
            with pytest.raises(RuntimeError, match="empty content"):
                await client.complete(
                    messages=[{"role": "user", "content": "test"}]
                )

    @pytest.mark.asyncio
    async def test_complete_retries_on_rate_limit(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete retries on rate limit."""
        call_count = 0

        class RateLimitError(Exception):
            pass

        async def rate_limit_then_succeed(
            **_kwargs: str | float | list[object],
        ) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                msg = "Rate limit exceeded"
                raise RateLimitError(msg)
            return {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"total_tokens": 10},
                "_hidden_params": {"response_cost": 0.0001},
            }

        mock_litellm.acompletion = AsyncMock(
            side_effect=rate_limit_then_succeed
        )

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                max_retries=3,
            )
            # Use patch to speed up the backoff sleep
            with patch("asyncio.sleep", new_callable=AsyncMock):
                content, _ = await client.complete(
                    messages=[{"role": "user", "content": "test"}],
                )
            assert content == '{"ok": true}'
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_complete_raises_non_retryable_error(
        self, mock_litellm: MagicMock
    ) -> None:
        """Complete raises non retryable error."""
        mock_litellm.acompletion = AsyncMock(
            side_effect=ValueError("Invalid request")
        )

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
            )
            with pytest.raises(ValueError, match="Invalid request"):
                await client.complete(
                    messages=[{"role": "user", "content": "test"}]
                )

    @pytest.mark.asyncio
    async def test_streaming_with_usage_and_chunks(
        self, mock_litellm: MagicMock
    ) -> None:
        """Streaming with usage and chunks."""

        async def _stream() -> AsyncIterator[dict[str, Any]]:
            yield {
                "choices": [{"delta": {"content": '{"raw_answer": "Hel'}}],
                "usage": None,
            }
            yield {
                "choices": [{"delta": {"content": 'lo"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

        mock_litellm.acompletion = AsyncMock(return_value=_stream())

        def completion_cost(completion_response: MockModelResponse) -> float:
            assert completion_response.model == "gpt-4o"
            usage = completion_response.usage
            assert isinstance(usage, MockUsage)
            assert usage.prompt_tokens == 10
            assert usage.completion_tokens == 5
            return 0.123

        mock_litellm.completion_cost = completion_cost

        # Mock ModelResponse and Usage classes for cost calculation
        class MockUsage:
            def __init__(
                self,
                prompt_tokens: int = 0,
                completion_tokens: int = 0,
                total_tokens: int = 0,
            ) -> None:
                self.prompt_tokens = prompt_tokens
                self.completion_tokens = completion_tokens
                self.total_tokens = total_tokens

        class MockModelResponse:
            # litellm builds responses with an ``id`` keyword.
            def __init__(self, **fields: object) -> None:
                self.id = fields["id"]
                self.model = fields["model"]
                self.choices = fields["choices"]
                self.usage = fields["usage"]

        mock_litellm.ModelResponse = MockModelResponse
        # Set up nested module for litellm.types.utils.Usage
        mock_types = MagicMock()
        mock_types.utils.Usage = MockUsage
        mock_litellm.types = mock_types

        chunks: list[tuple[str, bool]] = []

        def on_chunk(text: str, done: bool) -> None:
            chunks.append((text, done))

        patched_modules = {
            "litellm": mock_litellm,
            "litellm.types": mock_types,
            "litellm.types.utils": mock_types.utils,
        }
        with patch.dict(sys.modules, patched_modules):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                streaming_enabled=True,
            )
            content, cost = await client.complete(
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
                on_stream_chunk=on_chunk,
            )

        assert content == '{"raw_answer": "Hello"}'
        assert chunks == [
            ('{"raw_answer": "Hel', False),
            ('lo"}', False),
            ("", True),
        ]
        assert cost == pytest.approx(0.123)

    @pytest.mark.asyncio
    async def test_non_streaming_emits_reasoning_content_when_available(
        self, mock_litellm: MagicMock
    ) -> None:
        """Non streaming emits reasoning content when available."""
        mock_litellm.acompletion = AsyncMock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": '{"result": "ok"}',
                            "reasoning_content": "I reasoned about it.",
                        }
                    }
                ],
                "usage": {"total_tokens": 10},
                "_hidden_params": {"response_cost": 0.01},
            }
        )

        reasoning_chunks: list[tuple[str, bool]] = []

        def on_reasoning(text: str, done: bool) -> None:
            reasoning_chunks.append((text, done))

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                use_native_reasoning=True,
            )
            content, _ = await client.complete(
                messages=[{"role": "user", "content": "hello"}],
                on_reasoning_chunk=on_reasoning,
            )

        assert content == '{"result": "ok"}'
        assert reasoning_chunks == [
            ("I reasoned about it.", False),
            ("", True),
        ]

    @pytest.mark.asyncio
    async def test_streaming_emits_reasoning_delta_when_available(
        self, mock_litellm: MagicMock
    ) -> None:
        """Streaming emits reasoning delta when available."""

        async def _stream() -> AsyncIterator[dict[str, Any]]:
            yield {
                "choices": [{"delta": {"reasoning_content": "think-"}}],
                "usage": None,
            }
            yield {
                "choices": [{"delta": {"content": '{"answer": "hi"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        mock_litellm.acompletion = AsyncMock(return_value=_stream())

        def completion_cost(
            _model: str, _prompt_tokens: int, _completion_tokens: int
        ) -> float:
            return 0.0

        mock_litellm.completion_cost = completion_cost

        answer_chunks: list[tuple[str, bool]] = []
        reasoning_chunks: list[tuple[str, bool]] = []

        def on_chunk(text: str, done: bool) -> None:
            answer_chunks.append((text, done))

        def on_reasoning(text: str, done: bool) -> None:
            reasoning_chunks.append((text, done))

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                streaming_enabled=True,
                use_native_reasoning=True,
            )
            content, _ = await client.complete(
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
                on_stream_chunk=on_chunk,
                on_reasoning_chunk=on_reasoning,
            )

        assert content == '{"answer": "hi"}'
        assert answer_chunks[-1] == ("", True)
        assert reasoning_chunks == [("think-", False), ("", True)]

    @pytest.mark.asyncio
    async def test_reasoning_effort_passed_with_drop_params(
        self, mock_litellm: MagicMock
    ) -> None:
        """Reasoning effort passed with drop params."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                # Must use a reasoning model for reasoning_effort to be passed
                "openai/o1-preview",
                temperature=0.0,
                json_schema_mode=False,
                use_native_reasoning=True,
                reasoning_effort="medium",
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )
            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert call_kwargs["reasoning_effort"] == "medium"
            assert call_kwargs["drop_params"] is True

    @pytest.mark.asyncio
    async def test_reasoning_callback_errors_are_swallowed(
        self, mock_litellm: MagicMock
    ) -> None:
        """Reasoning callback errors are swallowed."""
        mock_litellm.acompletion = AsyncMock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": '{"result": "ok"}',
                            "reasoning_content": "I reasoned about it.",
                        }
                    }
                ],
                "usage": {"total_tokens": 10},
                "_hidden_params": {"response_cost": 0.01},
            }
        )

        def on_reasoning(_: str, __: bool) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                use_native_reasoning=True,
            )
            content, cost = await client.complete(
                messages=[{"role": "user", "content": "hello"}],
                on_reasoning_chunk=on_reasoning,
            )

        assert content == '{"result": "ok"}'
        assert cost == 0.01

    @pytest.mark.asyncio
    async def test_sanitized_schema_policy(
        self, mock_litellm: MagicMock
    ) -> None:
        """Test that unknown models use sanitized_schema policy."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "unknown-provider/custom-model",
                temperature=0.0,
                json_schema_mode=True,
            )
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "test",
                    "schema": {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                    },
                },
            }
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format=response_format,
            )
            call_kwargs = mock_litellm.acompletion.call_args[1]
            # Should have sanitized schema with additionalProperties: false
            rf = call_kwargs.get("response_format", {})
            if "json_schema" in rf:
                schema = rf["json_schema"].get("schema", {})
                assert schema.get("additionalProperties") is False


class TestBuildMinimalPlannerSchema:
    """Tests for build minimal planner schema."""

    def test_builds_valid_schema(self) -> None:
        """Builds valid schema."""
        schema = _build_minimal_planner_schema()
        assert schema["type"] == "object"
        assert "next_node" in schema["properties"]
        assert "args" in schema["properties"]
        assert "required" in schema
        assert schema["additionalProperties"] is False


class TestInlineDefs:
    """Tests for inline defs."""

    def test_inlines_simple_ref(self) -> None:
        """Inlines simple ref."""
        defs = {"MyType": {"type": "string"}}
        schema = {
            "type": "object",
            "properties": {"field": {"$ref": "#/$defs/MyType"}},
            "$defs": defs,
        }
        result = _inline_defs(schema, defs)
        assert result["properties"]["field"] == {"type": "string"}
        assert "$defs" not in result

    def test_inlines_nested_refs(self) -> None:
        """Inlines nested refs."""
        defs = {
            "Inner": {"type": "integer"},
            "Outer": {
                "type": "object",
                "properties": {"value": {"$ref": "#/$defs/Inner"}},
            },
        }
        schema = {
            "type": "object",
            "properties": {"nested": {"$ref": "#/$defs/Outer"}},
            "$defs": defs,
        }
        result = _inline_defs(schema, defs)
        assert result["properties"]["nested"]["properties"]["value"] == {
            "type": "integer"
        }

    def test_handles_lists(self) -> None:
        """Handles lists."""
        defs = {"Item": {"type": "string"}}
        schema = {
            "type": "array",
            "items": [{"$ref": "#/$defs/Item"}, {"type": "number"}],
            "$defs": defs,
        }
        result = _inline_defs(schema, defs)
        assert result["items"][0] == {"type": "string"}
        assert result["items"][1] == {"type": "number"}

    def test_handles_missing_ref(self) -> None:
        """Handles missing ref."""
        schema = {
            "type": "object",
            "properties": {"field": {"$ref": "#/$defs/Missing"}},
        }
        result = _inline_defs(schema, {})
        # Should keep the ref if not found in defs
        assert result["properties"]["field"] == {"$ref": "#/$defs/Missing"}


class TestArtifactPlaceholder:
    """Tests for artifact placeholder."""

    def test_string_artifact(self) -> None:
        """String artifact."""
        result = _artifact_placeholder("hello world")
        assert result == "<artifact:str size=11>"

    def test_bytes_artifact(self) -> None:
        """Bytes artifact."""
        result = _artifact_placeholder(b"binary data")
        assert result == "<artifact:bytes size=11>"

    def test_list_artifact(self) -> None:
        """List artifact."""
        result = _artifact_placeholder([1, 2, 3])
        assert result == "<artifact:list size=3>"

    def test_dict_artifact(self) -> None:
        """Dict artifact."""
        result = _artifact_placeholder({"a": 1, "b": 2})
        assert result == "<artifact:dict size=2>"

    def test_object_without_len(self) -> None:
        """Object without len."""

        class NoLen:
            pass

        result = _artifact_placeholder(NoLen())
        assert result == "<artifact:NoLen>"


class TestSanitizeJsonSchemaAdvanced:
    """Tests for sanitize json schema advanced."""

    def test_require_all_fields(self) -> None:
        """Require all fields."""
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        result = _sanitize_json_schema(
            schema, strict_mode=True, require_all_fields=True
        )
        assert set(result["required"]) == {"a", "b"}

    def test_inline_defs_option(self) -> None:
        """Inline defs option."""
        schema = {
            "type": "object",
            "properties": {"field": {"$ref": "#/$defs/MyType"}},
            "$defs": {"MyType": {"type": "string"}},
        }
        result = _sanitize_json_schema(schema, inline_defs=True)
        assert "$defs" not in result
        assert result["properties"]["field"] == {"type": "string"}

    def test_all_of_sanitization(self) -> None:
        """All of sanitization."""
        schema = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {"x": {"type": "string", "minLength": 1}},
                },
            ]
        }
        result = _sanitize_json_schema(schema, strict_mode=True)
        assert "minLength" not in result["allOf"][0]["properties"]["x"]

    def test_one_of_sanitization(self) -> None:
        """One of sanitization."""
        schema = {
            "oneOf": [
                {"type": "string", "maxLength": 100},
                {"type": "integer", "maximum": 10},
            ]
        }
        result = _sanitize_json_schema(schema)
        assert "maxLength" not in result["oneOf"][0]
        assert "maximum" not in result["oneOf"][1]

    def test_any_of_sanitization(self) -> None:
        """Any of sanitization."""
        schema = {
            "anyOf": [
                {"type": "string", "pattern": ".*"},
                {"type": "null"},
            ]
        }
        result = _sanitize_json_schema(schema)
        assert "pattern" not in result["anyOf"][0]

    def test_defs_sanitization(self) -> None:
        """Defs sanitization."""
        schema = {
            "type": "object",
            "$defs": {
                "MyDef": {"type": "string", "minLength": 5},
            },
        }
        result = _sanitize_json_schema(schema)
        assert "minLength" not in result["$defs"]["MyDef"]

    def test_additional_properties_dict_sanitized(self) -> None:
        """Additional properties dict sanitized."""
        schema = {
            "type": "object",
            "additionalProperties": {"type": "string", "maxLength": 50},
        }
        result = _sanitize_json_schema(schema, strict_mode=False)
        # additionalProperties with dict schema should be recursively sanitized
        assert "maxLength" not in result["additionalProperties"]


class TestResponseFormatPolicyExtended:
    """Tests for response format policy extended."""

    def test_xai_returns_json_object(self) -> None:
        """Xai returns JSON object."""
        assert _response_format_policy("xai/grok-2") == "json_object"

    def test_grok_returns_json_object(self) -> None:
        """Grok returns JSON object."""
        assert _response_format_policy("grok-beta") == "json_object"

    def test_mistral_returns_json_object(self) -> None:
        """Mistral returns JSON object."""
        assert (
            _response_format_policy("mistral/mistral-large") == "json_object"
        )

    def test_llama_returns_json_object(self) -> None:
        """Llama returns JSON object."""
        assert _response_format_policy("meta-llama/llama-3") == "json_object"

    def test_qwen_returns_json_object(self) -> None:
        """Qwen returns JSON object."""
        assert _response_format_policy("qwen/qwen-2.5") == "json_object"

    def test_deepseek_returns_json_object(self) -> None:
        """Deepseek returns JSON object."""
        assert (
            _response_format_policy("deepseek/deepseek-coder") == "json_object"
        )

    def test_cohere_returns_json_object(self) -> None:
        """Cohere returns JSON object."""
        assert _response_format_policy("cohere/command-r") == "json_object"

    def test_openrouter_gpt_returns_json_object(self) -> None:
        """OpenRouter gpt returns JSON object."""
        assert _response_format_policy("openrouter/gpt-4") == "json_object"

    def test_o1_model_returns_json_object(self) -> None:
        """O1 model returns JSON object."""
        assert (
            _response_format_policy("openrouter/o1-preview") == "json_object"
        )

    def test_o3_model_returns_json_object(self) -> None:
        """O3 model returns JSON object."""
        assert _response_format_policy("openrouter/o3-mini") == "json_object"

    def test_nim_model_returns_json_object(self) -> None:
        """NIM model returns JSON object."""
        assert (
            _response_format_policy("nim/qwen/qwen3.5-397b-a17b")
            == "json_object"
        )

    def test_nvidia_model_returns_json_object(self) -> None:
        """Nvidia model returns JSON object."""
        assert (
            _response_format_policy("nvidia/qwen/qwen3.5-397b-a17b")
            == "json_object"
        )


# ─── RFC_UNIFIED_ACTION_SCHEMA: Native reasoning support tests ───────────────


class TestSupportsReasoning:
    """Tests for _supports_reasoning model detection."""

    def test_o1_model_supports_reasoning(self) -> None:
        """OpenAI o1 models should support native reasoning."""
        assert _supports_reasoning("openai/o1-preview") is True
        assert _supports_reasoning("o1-mini") is True

    def test_o3_model_supports_reasoning(self) -> None:
        """OpenAI o3 models should support native reasoning."""
        assert _supports_reasoning("openai/o3-mini") is True
        assert _supports_reasoning("o3-preview") is True

    def test_deepseek_reasoner_supports_reasoning(self) -> None:
        """DeepSeek reasoner models should support native reasoning."""
        assert _supports_reasoning("deepseek-reasoner") is True
        assert _supports_reasoning("deepseek-r1") is True

    def test_model_with_reasoning_in_name(self) -> None:
        """Any model with 'reasoning' in name should be detected."""
        assert _supports_reasoning("custom-reasoning-model") is True

    def test_regular_model_no_reasoning(self) -> None:
        """Regular models should not support native reasoning."""
        assert _supports_reasoning("gpt-4o") is False
        assert _supports_reasoning("claude-3-sonnet") is False
        assert _supports_reasoning("mistral-large") is False

    def test_databricks_model_no_reasoning(self) -> None:
        """Databricks models should not support native reasoning."""
        assert _supports_reasoning("databricks/gpt-oss-120b") is False
        assert _supports_reasoning("databricks/llama-3") is False


class TestReasoningEffortGuard:
    """Tests for reasoning_effort parameter guarding."""

    @pytest.fixture
    def mock_litellm(self) -> MagicMock:
        """Provide a stand-in for the litellm module."""
        mock = MagicMock()
        mock.acompletion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"total_tokens": 10},
                "_hidden_params": {"response_cost": 0.001},
            }
        )
        return mock

    @pytest.mark.asyncio
    async def test_reasoning_effort_not_passed_for_non_reasoning_model(
        self, mock_litellm: MagicMock
    ) -> None:
        """reasoning_effort is not sent to models without reasoning."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "databricks/gpt-oss-120b",  # Non-reasoning model
                temperature=0.0,
                json_schema_mode=False,
                use_native_reasoning=True,
                reasoning_effort="medium",
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )
            call_kwargs = mock_litellm.acompletion.call_args[1]
            # reasoning_effort should NOT be in params for non-reasoning models
            assert "reasoning_effort" not in call_kwargs

    @pytest.mark.asyncio
    async def test_reasoning_effort_passed_for_reasoning_model(
        self, mock_litellm: MagicMock
    ) -> None:
        """reasoning_effort is sent to models that support reasoning."""
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "openai/o1-preview",  # Reasoning model
                temperature=0.0,
                json_schema_mode=False,
                use_native_reasoning=True,
                reasoning_effort="medium",
            )
            await client.complete(
                messages=[{"role": "user", "content": "test"}]
            )
            call_kwargs = mock_litellm.acompletion.call_args[1]
            # reasoning_effort SHOULD be in params for reasoning models
            assert call_kwargs.get("reasoning_effort") == "medium"


class TestExtractJsonFromTextAdvanced:
    """Additional tests for JSON extraction with reasoning/thinking content."""

    def test_json_after_thinking_text(self) -> None:
        """Should extract JSON even when preceded by thinking text."""
        text = """Let me think about this...

After careful consideration, here's my response:
{"next_node": "final_response", "args": {"answer": "42"}}"""
        result = _extract_json_from_text(text)
        assert '"next_node"' in result
        assert '"final_response"' in result

    def test_fenced_json_with_language_tag(self) -> None:
        """Should handle ```json blocks correctly."""
        text = """Here is the action:
```json
{"next_node": "search", "args": {"query": "test"}}
```
"""
        result = _extract_json_from_text(text)
        assert result == '{"next_node": "search", "args": {"query": "test"}}'

    def test_fenced_code_without_language_tag(self) -> None:
        """Should handle ``` blocks without json tag."""
        text = """```
{"next_node": "tool", "args": {}}
```"""
        result = _extract_json_from_text(text)
        assert '"next_node"' in result

    def test_multiple_json_objects_returns_outer(self) -> None:
        """With several JSON objects, extract from the first { to last }."""
        text = '{"a": 1} some text {"b": 2}'
        result = _extract_json_from_text(text)
        # Should get the full span from first { to last }
        assert result == '{"a": 1} some text {"b": 2}'
