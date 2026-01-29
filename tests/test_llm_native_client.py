"""Tests for the native LLM client module (penguiflow.llm.client)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from penguiflow.llm.client import (
    LLMClient,
    LLMClientConfig,
    LLMResult,
)
from penguiflow.llm.schema.plan import OutputMode
from penguiflow.llm.types import (
    CompletionResponse,
    Cost,
    LLMMessage,
    LLMRequest,
    TextPart,
    Usage,
)


class SampleOutput(BaseModel):
    answer: str
    confidence: float


class TestLLMClientConfig:
    def test_defaults(self) -> None:
        config = LLMClientConfig()
        assert config.max_retries == 3
        assert config.retry_on_validation is True
        assert config.retry_on_parse is True
        assert config.retry_on_provider_errors is True
        assert config.timeout_s == 120.0
        assert config.temperature == 0.0
        assert config.force_mode is None
        assert config.enable_telemetry is True
        assert config.enable_cost_tracking is True

    def test_custom_config(self) -> None:
        config = LLMClientConfig(
            max_retries=5,
            timeout_s=60.0,
            force_mode=OutputMode.TOOLS,
            enable_telemetry=False,
        )
        assert config.max_retries == 5
        assert config.timeout_s == 60.0
        assert config.force_mode == OutputMode.TOOLS
        assert config.enable_telemetry is False


class TestLLMResult:
    def test_create(self) -> None:
        data = SampleOutput(answer="42", confidence=0.95)
        usage = Usage(input_tokens=100, output_tokens=50, total_tokens=150)
        cost = Cost(input_cost=0.01, output_cost=0.02, total_cost=0.03)

        result = LLMResult(
            data=data,
            usage=usage,
            cost=cost,
            mode_used=OutputMode.NATIVE,
            attempts=1,
        )

        assert result.data.answer == "42"
        assert result.usage.total_tokens == 150
        assert result.cost.total_cost == 0.03
        assert result.mode_used == OutputMode.NATIVE
        assert result.attempts == 1

    def test_with_raw_response(self) -> None:
        data = SampleOutput(answer="test", confidence=0.5)

        result = LLMResult(
            data=data,
            usage=Usage.zero(),
            cost=Cost.zero(),
            mode_used=OutputMode.PROMPTED,
            attempts=2,
            raw_response={"raw": "data"},
        )

        assert result.raw_response == {"raw": "data"}
        assert result.attempts == 2


class TestLLMClient:
    def test_init_with_provider(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        client = LLMClient("test-model", provider=mock_provider)

        assert client.provider is mock_provider
        assert client.model == "test-model"

    def test_init_with_profile(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        mock_profile = MagicMock()
        mock_profile.model_pattern = "test-*"

        client = LLMClient("test-model", provider=mock_provider, profile=mock_profile)

        assert client.profile is mock_profile

    def test_total_cost_initial(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        client = LLMClient("test-model", provider=mock_provider)

        assert client.total_cost.total_cost == 0.0

    def test_reset_cost(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        client = LLMClient("test-model", provider=mock_provider)
        client._total_cost = Cost(input_cost=0.1, output_cost=0.2, total_cost=0.3)

        client.reset_cost()

        assert client.total_cost.total_cost == 0.0

    def test_get_strategy_native(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        client = LLMClient("test-model", provider=mock_provider)

        strategy = client._get_strategy(OutputMode.NATIVE)
        assert strategy.__class__.__name__ == "NativeOutputStrategy"

    def test_get_strategy_tools(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        client = LLMClient("test-model", provider=mock_provider)

        strategy = client._get_strategy(OutputMode.TOOLS)
        assert strategy.__class__.__name__ == "ToolsOutputStrategy"

    def test_get_strategy_prompted(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"

        client = LLMClient("test-model", provider=mock_provider)

        strategy = client._get_strategy(OutputMode.PROMPTED)
        assert strategy.__class__.__name__ == "PromptedOutputStrategy"


class TestLLMClientAsync:
    @pytest.mark.asyncio
    async def test_complete_raw(self) -> None:
        mock_provider = MagicMock()
        mock_provider.model = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.complete = AsyncMock(
            return_value=CompletionResponse(
                message=LLMMessage(role="assistant", parts=[TextPart(text="Hello!")]),
                usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        )

        client = LLMClient("test-model", provider=mock_provider)

        request = LLMRequest(
            model="test-model",
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hi")]),),
        )

        response = await client.complete_raw(request)

        assert response.message.text == "Hello!"
        mock_provider.complete.assert_called_once()
