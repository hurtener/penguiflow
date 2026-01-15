"""Tests for the LLM retry module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from penguiflow.llm.errors import LLMServerError
from penguiflow.llm.retry import (
    ModelRetry,
    RetryConfig,
    RetryState,
    ValidationRetry,
    format_parse_retry_message,
    format_validation_retry_message,
)


class TestModelRetry:
    def test_create(self) -> None:
        error = ModelRetry("Please try again")
        assert error.message == "Please try again"
        assert error.validation_errors is None

    def test_with_validation_errors(self) -> None:
        errors = [{"loc": ["field"], "msg": "invalid"}]
        error = ModelRetry("Validation failed", validation_errors=errors)
        assert error.validation_errors == errors


class TestValidationRetry:
    def test_create(self) -> None:
        errors = [{"loc": ["name"], "msg": "required"}]
        error = ValidationRetry(errors=errors, raw_content='{"incomplete": true}')
        assert error.errors == errors
        assert error.raw_content == '{"incomplete": true}'


class TestRetryConfig:
    def test_defaults(self) -> None:
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.retry_on_validation is True
        assert config.retry_on_parse is True
        assert config.retry_on_provider_errors is True
        assert config.initial_backoff_s == 1.0
        assert config.max_backoff_s == 30.0
        assert config.backoff_multiplier == 2.0

    def test_custom_config(self) -> None:
        config = RetryConfig(
            max_retries=5,
            retry_on_validation=False,
            initial_backoff_s=0.5,
        )
        assert config.max_retries == 5
        assert config.retry_on_validation is False
        assert config.initial_backoff_s == 0.5


class TestRetryState:
    def test_initial_state(self) -> None:
        state = RetryState()
        assert state.attempt == 0
        assert state.total_cost == 0.0
        assert state.errors == []

    def test_track_errors(self) -> None:
        state = RetryState()
        state.errors.append(ValueError("test"))
        assert len(state.errors) == 1


class TestFormatValidationRetryMessage:
    def test_format_validation_retry(self) -> None:
        error = ValidationRetry(
            errors=[
                {"loc": ["field1"], "msg": "is required"},
                {"loc": ["nested", "field2"], "msg": "must be positive"},
            ],
            raw_content="{}",
        )
        msg = format_validation_retry_message(error)
        assert msg.role == "user"
        assert "failed validation" in msg.text.lower()
        assert "field1" in msg.text
        assert "nested -> field2" in msg.text

    def test_format_pydantic_validation_error(self) -> None:
        class TestModel(BaseModel):
            name: str
            age: int

        try:
            TestModel(name=123, age="not a number")  # type: ignore[arg-type]
        except ValidationError as e:
            msg = format_validation_retry_message(e)
            assert msg.role == "user"
            assert "validation" in msg.text.lower()


class TestFormatParseRetryMessage:
    def test_format_json_error(self) -> None:
        try:
            json.loads("{invalid json}")
        except json.JSONDecodeError as e:
            msg = format_parse_retry_message(e)
            assert msg.role == "user"
            assert "invalid json" in msg.text.lower()
            assert "valid json" in msg.text.lower()


class SampleOutput(BaseModel):
    """Test output model for retry tests."""

    result: str
    count: int


class TestCallWithRetry:
    """Tests for the call_with_retry function."""

    @pytest.fixture
    def mock_provider(self) -> MagicMock:
        from penguiflow.llm.types import CompletionResponse, LLMMessage, TextPart, Usage

        provider = MagicMock()
        provider.model = "test-model"
        provider.profile = MagicMock()
        provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(
                    role="assistant",
                    parts=[TextPart(text='{"result": "success", "count": 42}')],
                ),
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )
        return provider

    @pytest.fixture
    def mock_strategy(self) -> MagicMock:
        strategy = MagicMock()
        strategy.parse_response.return_value = SampleOutput(result="success", count=42)
        return strategy

    @pytest.mark.asyncio
    async def test_successful_call(self, mock_provider: MagicMock, mock_strategy: MagicMock) -> None:
        from penguiflow.llm.retry import call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        result, cost = await call_with_retry(
            provider=mock_provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
        )

        assert result.result == "success"
        assert result.count == 42
        mock_provider.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_validation_error(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        # First call raises ValidationError, second succeeds
        mock_strategy.parse_response.side_effect = [
            ValidationError.from_exception_data(
                "test",
                [{"type": "missing", "loc": ("result",), "msg": "Field required", "input": {}}],
            ),
            SampleOutput(result="success", count=42),
        ]

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]
        retry_callback = MagicMock()

        result, cost = await call_with_retry(
            provider=mock_provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
            on_retry=retry_callback,
        )

        assert result.result == "success"
        assert mock_provider.complete.call_count == 2
        retry_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_json_error(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        # First call raises JSONDecodeError, second succeeds
        mock_strategy.parse_response.side_effect = [
            json.JSONDecodeError("Invalid JSON", "{", 0),
            SampleOutput(result="success", count=42),
        ]

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        result, cost = await call_with_retry(
            provider=mock_provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
        )

        assert result.result == "success"
        assert mock_provider.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_model_retry_exception(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import ModelRetry, call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        # First call raises ModelRetry, second succeeds
        mock_strategy.parse_response.side_effect = [
            ModelRetry("Please provide more details"),
            SampleOutput(result="success", count=42),
        ]

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        result, cost = await call_with_retry(
            provider=mock_provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
        )

        assert result.result == "success"
        assert mock_provider.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_provider_error(self, mock_strategy: MagicMock) -> None:
        from penguiflow.llm.retry import RetryConfig, call_with_retry
        from penguiflow.llm.types import CompletionResponse, LLMMessage, TextPart, Usage

        provider = MagicMock()
        provider.model = "test-model"
        provider.profile = MagicMock()

        # First call raises LLMServerError, second succeeds
        provider.complete = AsyncMock(
            side_effect=[
                LLMServerError(message="Internal error", status_code=500),
                CompletionResponse(
                    message=LLMMessage(
                        role="assistant",
                        parts=[TextPart(text='{"result": "success", "count": 42}')],
                    ),
                    usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
                ),
            ]
        )

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]
        config = RetryConfig(initial_backoff_s=0.01)  # Fast backoff for tests

        result, cost = await call_with_retry(
            provider=provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
            config=config,
        )

        assert result.result == "success"
        assert provider.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_validation(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import RetryConfig, call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        # All calls fail validation
        mock_strategy.parse_response.side_effect = ValidationError.from_exception_data(
            "test",
            [{"type": "missing", "loc": ("result",), "msg": "Field required", "input": {}}],
        )

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]
        config = RetryConfig(max_retries=2)

        with pytest.raises(ValidationError):
            await call_with_retry(
                provider=mock_provider,
                base_messages=messages,
                response_model=SampleOutput,
                output_strategy=mock_strategy,
                config=config,
            )

        assert mock_provider.complete.call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_no_retry_when_disabled(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import RetryConfig, call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        mock_strategy.parse_response.side_effect = ValidationError.from_exception_data(
            "test",
            [{"type": "missing", "loc": ("result",), "msg": "Field required", "input": {}}],
        )

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]
        config = RetryConfig(retry_on_validation=False)

        with pytest.raises(ValidationError):
            await call_with_retry(
                provider=mock_provider,
                base_messages=messages,
                response_model=SampleOutput,
                output_strategy=mock_strategy,
                config=config,
            )

        # Should not retry when disabled
        assert mock_provider.complete.call_count == 1

    @pytest.mark.asyncio
    async def test_cost_tracking_with_pricing_function(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import call_with_retry
        from penguiflow.llm.types import LLMMessage, TextPart

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        def pricing_fn(model: str, input_tokens: int, output_tokens: int) -> float:
            return input_tokens * 0.001 + output_tokens * 0.002

        result, cost = await call_with_retry(
            provider=mock_provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
            pricing_fn=pricing_fn,
        )

        # 10 input tokens * 0.001 + 5 output tokens * 0.002 = 0.01 + 0.01 = 0.02
        assert cost == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_custom_request_builder(
        self, mock_provider: MagicMock, mock_strategy: MagicMock
    ) -> None:
        from penguiflow.llm.retry import call_with_retry
        from penguiflow.llm.types import LLMMessage, LLMRequest, TextPart

        messages = [LLMMessage(role="user", parts=[TextPart(text="Hello")])]

        def custom_builder(msgs: list[LLMMessage]) -> LLMRequest:
            return LLMRequest(
                model="custom-model",
                messages=tuple(msgs),
            )

        result, cost = await call_with_retry(
            provider=mock_provider,
            base_messages=messages,
            response_model=SampleOutput,
            output_strategy=mock_strategy,
            build_request=custom_builder,
        )

        # Verify custom builder was used
        call_args = mock_provider.complete.call_args[0][0]
        assert call_args.model == "custom-model"
