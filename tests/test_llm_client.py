"""Tests for penguiflow.planner.llm module with mocked litellm."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from typing import Any
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
)


class TestExtractJsonFromText:
    def test_plain_json(self) -> None:
        assert _extract_json_from_text('{"key": "value"}') == '{"key": "value"}'

    def test_fenced_json(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        assert _extract_json_from_text(text) == '{"key": "value"}'

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here is the result: {"key": "value"} end'
        assert _extract_json_from_text(text) == '{"key": "value"}'

    def test_no_json(self) -> None:
        assert _extract_json_from_text("no json here") == "no json here"


class TestCoerceLlmResponse:
    def test_string_response(self) -> None:
        content, cost = _coerce_llm_response('{"result": 1}')
        assert content == '{"result": 1}'
        assert cost == 0.0

    def test_tuple_response(self) -> None:
        content, cost = _coerce_llm_response(('{"result": 1}', 0.05))
        assert content == '{"result": 1}'
        assert cost == 0.05

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            _coerce_llm_response(123)  # type: ignore


class TestResponseFormatPolicy:
    def test_anthropic_returns_json_object(self) -> None:
        assert _response_format_policy("anthropic/claude-3") == "json_object"
        assert _response_format_policy("claude-sonnet") == "json_object"

    def test_openai_returns_json_object(self) -> None:
        assert _response_format_policy("gpt-4o") == "json_object"
        assert _response_format_policy("openai/gpt-4") == "json_object"

    def test_gemini_returns_json_object(self) -> None:
        assert _response_format_policy("gemini/gemini-1.5-pro") == "json_object"

    def test_maverick_returns_json_object(self) -> None:
        assert _response_format_policy("maverick-model") == "json_object"

    def test_unknown_returns_sanitized_schema(self) -> None:
        assert _response_format_policy("some-unknown-model") == "sanitized_schema"


class TestSanitizeJsonSchema:
    def test_removes_constraints(self) -> None:
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
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        result = _sanitize_json_schema(schema, strict_mode=True)
        assert result["additionalProperties"] is False

    def test_handles_nested_schemas(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "pattern": ".*"}},
                }
            },
        }
        result = _sanitize_json_schema(schema, strict_mode=True)
        assert result["additionalProperties"] is False
        assert result["properties"]["nested"]["additionalProperties"] is False
        assert "pattern" not in result["properties"]["nested"]["properties"]["value"]


class TestLiteLLMJSONClient:
    @pytest.fixture
    def mock_litellm(self) -> MagicMock:
        """Create a mock litellm module."""
        mock = MagicMock()
        mock.acompletion = AsyncMock(
            return_value={
                "choices": [{"message": {"content": '{"thought": "test", "next_node": null}'}}],
                "usage": {"total_tokens": 100},
                "_hidden_params": {"response_cost": 0.001},
            }
        )
        return mock

    @pytest.mark.asyncio
    async def test_complete_success(self, mock_litellm: MagicMock) -> None:
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
    async def test_complete_with_json_schema_mode(self, mock_litellm: MagicMock) -> None:
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
                    "schema": {"type": "object", "properties": {"value": {"type": "string"}}},
                },
            }
            content, cost = await client.complete(
                messages=[{"role": "user", "content": "test"}],
                response_format=response_format,
            )
            assert content is not None
            # Verify response_format was set (downgraded to json_object for gpt-4o)
            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert "response_format" in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_with_dict_llm_config(self, mock_litellm: MagicMock) -> None:
        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                {"model": "gpt-4o", "api_key": "test-key"},
                temperature=0.5,
                json_schema_mode=False,
            )
            await client.complete(messages=[{"role": "user", "content": "test"}])
            call_kwargs = mock_litellm.acompletion.call_args[1]
            assert call_kwargs["model"] == "gpt-4o"
            assert call_kwargs["api_key"] == "test-key"

    @pytest.mark.asyncio
    async def test_complete_retries_on_timeout(self, mock_litellm: MagicMock) -> None:
        call_count = 0

        async def timeout_then_succeed(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError("Request timed out")
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
            content, cost = await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )
            assert content == '{"result": "ok"}'
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_complete_exhausts_retries_on_timeout(self, mock_litellm: MagicMock) -> None:
        mock_litellm.acompletion = AsyncMock(side_effect=TimeoutError("timeout"))

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
                max_retries=2,
                timeout_s=0.1,
            )
            with pytest.raises(RuntimeError, match="failed after 2 retries"):
                await client.complete(messages=[{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_complete_raises_on_empty_content(self, mock_litellm: MagicMock) -> None:
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
                await client.complete(messages=[{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_complete_retries_on_rate_limit(self, mock_litellm: MagicMock) -> None:
        call_count = 0

        class RateLimitError(Exception):
            pass

        async def rate_limit_then_succeed(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RateLimitError("Rate limit exceeded")
            return {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"total_tokens": 10},
                "_hidden_params": {"response_cost": 0.0001},
            }

        mock_litellm.acompletion = AsyncMock(side_effect=rate_limit_then_succeed)

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
    async def test_complete_raises_non_retryable_error(self, mock_litellm: MagicMock) -> None:
        mock_litellm.acompletion = AsyncMock(side_effect=ValueError("Invalid request"))

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
            client = _LiteLLMJSONClient(
                "gpt-4o",
                temperature=0.0,
                json_schema_mode=False,
            )
            with pytest.raises(ValueError, match="Invalid request"):
                await client.complete(messages=[{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_streaming_with_usage_and_chunks(self, mock_litellm: MagicMock) -> None:
        async def _stream() -> AsyncIterator[dict[str, Any]]:
            yield {"choices": [{"delta": {"content": '{"raw_answer": "Hel' }}], "usage": None}
            yield {
                "choices": [{"delta": {"content": 'lo"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

        mock_litellm.acompletion = AsyncMock(return_value=_stream())

        def completion_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
            assert model == "gpt-4o"
            assert prompt_tokens == 10
            assert completion_tokens == 5
            return 0.123

        mock_litellm.completion_cost = completion_cost  # type: ignore[attr-defined]

        chunks: list[tuple[str, bool]] = []

        def on_chunk(text: str, done: bool) -> None:
            chunks.append((text, done))

        with patch.dict(sys.modules, {"litellm": mock_litellm}):
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
    async def test_sanitized_schema_policy(self, mock_litellm: MagicMock) -> None:
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
    def test_builds_valid_schema(self) -> None:
        schema = _build_minimal_planner_schema()
        assert schema["type"] == "object"
        assert "thought" in schema["properties"]
        assert "next_node" in schema["properties"]
        assert "args" in schema["properties"]
        assert "plan" in schema["properties"]
        assert "join" in schema["properties"]
        assert "required" in schema
        assert schema["additionalProperties"] is False


class TestInlineDefs:
    def test_inlines_simple_ref(self) -> None:
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
        defs = {
            "Inner": {"type": "integer"},
            "Outer": {"type": "object", "properties": {"value": {"$ref": "#/$defs/Inner"}}},
        }
        schema = {
            "type": "object",
            "properties": {"nested": {"$ref": "#/$defs/Outer"}},
            "$defs": defs,
        }
        result = _inline_defs(schema, defs)
        assert result["properties"]["nested"]["properties"]["value"] == {"type": "integer"}

    def test_handles_lists(self) -> None:
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
        schema = {"type": "object", "properties": {"field": {"$ref": "#/$defs/Missing"}}}
        result = _inline_defs(schema, {})
        # Should keep the ref if not found in defs
        assert result["properties"]["field"] == {"$ref": "#/$defs/Missing"}


class TestArtifactPlaceholder:
    def test_string_artifact(self) -> None:
        result = _artifact_placeholder("hello world")
        assert result == "<artifact:str size=11>"

    def test_bytes_artifact(self) -> None:
        result = _artifact_placeholder(b"binary data")
        assert result == "<artifact:bytes size=11>"

    def test_list_artifact(self) -> None:
        result = _artifact_placeholder([1, 2, 3])
        assert result == "<artifact:list size=3>"

    def test_dict_artifact(self) -> None:
        result = _artifact_placeholder({"a": 1, "b": 2})
        assert result == "<artifact:dict size=2>"

    def test_object_without_len(self) -> None:
        class NoLen:
            pass
        result = _artifact_placeholder(NoLen())
        assert result == "<artifact:NoLen>"


class TestSanitizeJsonSchemaAdvanced:
    def test_require_all_fields(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
            "required": ["a"],
        }
        result = _sanitize_json_schema(schema, strict_mode=True, require_all_fields=True)
        assert set(result["required"]) == {"a", "b"}

    def test_inline_defs_option(self) -> None:
        schema = {
            "type": "object",
            "properties": {"field": {"$ref": "#/$defs/MyType"}},
            "$defs": {"MyType": {"type": "string"}},
        }
        result = _sanitize_json_schema(schema, inline_defs=True)
        assert "$defs" not in result
        assert result["properties"]["field"] == {"type": "string"}

    def test_all_of_sanitization(self) -> None:
        schema = {
            "allOf": [
                {"type": "object", "properties": {"x": {"type": "string", "minLength": 1}}},
            ]
        }
        result = _sanitize_json_schema(schema, strict_mode=True)
        assert "minLength" not in result["allOf"][0]["properties"]["x"]

    def test_one_of_sanitization(self) -> None:
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
        schema = {
            "anyOf": [
                {"type": "string", "pattern": ".*"},
                {"type": "null"},
            ]
        }
        result = _sanitize_json_schema(schema)
        assert "pattern" not in result["anyOf"][0]

    def test_defs_sanitization(self) -> None:
        schema = {
            "type": "object",
            "$defs": {
                "MyDef": {"type": "string", "minLength": 5},
            },
        }
        result = _sanitize_json_schema(schema)
        assert "minLength" not in result["$defs"]["MyDef"]

    def test_additional_properties_dict_sanitized(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": {"type": "string", "maxLength": 50},
        }
        result = _sanitize_json_schema(schema, strict_mode=False)
        # additionalProperties with dict schema should be recursively sanitized
        assert "maxLength" not in result["additionalProperties"]


class TestResponseFormatPolicyExtended:
    def test_xai_returns_json_object(self) -> None:
        assert _response_format_policy("xai/grok-2") == "json_object"

    def test_grok_returns_json_object(self) -> None:
        assert _response_format_policy("grok-beta") == "json_object"

    def test_mistral_returns_json_object(self) -> None:
        assert _response_format_policy("mistral/mistral-large") == "json_object"

    def test_llama_returns_json_object(self) -> None:
        assert _response_format_policy("meta-llama/llama-3") == "json_object"

    def test_qwen_returns_json_object(self) -> None:
        assert _response_format_policy("qwen/qwen-2.5") == "json_object"

    def test_deepseek_returns_json_object(self) -> None:
        assert _response_format_policy("deepseek/deepseek-coder") == "json_object"

    def test_cohere_returns_json_object(self) -> None:
        assert _response_format_policy("cohere/command-r") == "json_object"

    def test_openrouter_gpt_returns_json_object(self) -> None:
        assert _response_format_policy("openrouter/gpt-4") == "json_object"

    def test_o1_model_returns_json_object(self) -> None:
        assert _response_format_policy("openrouter/o1-preview") == "json_object"

    def test_o3_model_returns_json_object(self) -> None:
        assert _response_format_policy("openrouter/o3-mini") == "json_object"
