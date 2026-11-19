"""Tests for opt-in temperature handling and temperature-400 recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.llm.errors import LLMInvalidRequestError, is_temperature_error
from penguiflow.llm.output.base import OutputStrategy
from penguiflow.llm.profiles import ModelProfile, get_profile
from penguiflow.llm.providers._params import resolve_temperature
from penguiflow.llm.providers.base import Provider
from penguiflow.llm.types import (
    CompletionResponse,
    LLMMessage,
    LLMRequest,
    TextPart,
    Usage,
)

if TYPE_CHECKING:
    from penguiflow.llm.output.base import ModelT
    from penguiflow.llm.providers.databricks import DatabricksProvider
    from penguiflow.llm.schema.plan import SchemaPlan


class TestResolveTemperature:
    """Unit tests for the shared resolve_temperature helper."""

    def test_none_is_omitted(self) -> None:
        """None is omitted."""
        profile = ModelProfile()
        assert resolve_temperature(profile, None, model="m") is None

    def test_explicit_value_passes_through_when_supported(self) -> None:
        """Explicit value passes through when supported."""
        profile = ModelProfile(supports_temperature=True)
        assert resolve_temperature(profile, 0.5, model="m") == 0.5

    def test_dropped_when_profile_unsupported(self) -> None:
        """Dropped when profile unsupported."""
        profile = ModelProfile(supports_temperature=False)
        assert resolve_temperature(profile, 0.5, model="m") is None

    def test_dropped_when_forced_off(self) -> None:
        """Dropped when forced off."""
        profile = ModelProfile(supports_temperature=True)
        assert (
            resolve_temperature(profile, 0.5, model="m", forced_off=True)
            is None
        )


class TestIsTemperatureError:
    """Detection of provider temperature-rejection errors."""

    def test_detects_fixed_value_error(self) -> None:
        """Detects fixed value error."""
        msg = (
            "Unsupported value: 'temperature' does not support 0.0 with this "
            "model. Only the default (1) value is supported."
        )
        assert is_temperature_error(msg) is True

    def test_detects_unsupported_parameter_error(self) -> None:
        """Detects unsupported parameter error."""
        msg = (
            "BAD_REQUEST: Model global.anthropic.claude-opus-4-7 does not "
            "support the temperature parameter."
        )
        assert is_temperature_error(msg) is True

    def test_ignores_unrelated_error(self) -> None:
        """Ignores unrelated error."""
        assert is_temperature_error("rate limit exceeded") is False


class TestTemperatureProfileAudit:
    """The supports_temperature flag on audited model profiles."""

    def test_databricks_gpt5_series_unsupported(self) -> None:
        """Databricks gpt5 series unsupported."""
        for model in (
            "databricks-gpt-5-5",
            "databricks-gpt-5-5-pro",
            "databricks-gpt-5-4-mini",
            "databricks-gpt-5-4-nano",
            "databricks-gpt-5-2",
        ):
            assert get_profile(model).supports_temperature is False, model

    def test_databricks_claude_opus_4_7_unsupported(self) -> None:
        """Databricks claude opus 4 7 unsupported."""
        assert (
            get_profile("databricks-claude-opus-4-7").supports_temperature
            is False
        )

    def test_openai_o_series_unsupported(self) -> None:
        """OpenAI o series unsupported."""
        for model in ("o1", "o3", "o4-mini"):
            assert get_profile(model).supports_temperature is False, model

    def test_native_claude_opus_4_7_still_supported(self) -> None:
        # The restriction is Databricks-route specific.
        """Native claude opus 4 7 still supported."""
        assert get_profile("claude-opus-4-7").supports_temperature is True

    def test_standard_models_support_temperature(self) -> None:
        """Standard models support temperature."""
        for model in (
            "gpt-4o",
            "claude-sonnet-4-5",
            "databricks-claude-sonnet-4-5",
        ):
            assert get_profile(model).supports_temperature is True, model


class TestDatabricksBuildParamsTemperature:
    """build_params honours opt-in, capability-aware temperature."""

    def _provider(self, model: str) -> DatabricksProvider:
        from penguiflow.llm.providers.databricks import DatabricksProvider

        return DatabricksProvider(
            model,
            host="https://test.cloud.databricks.com",
            token="test-token",
            profile=get_profile(model),
        )

    def _request(self, model: str, temperature: float | None) -> LLMRequest:
        return LLMRequest(
            model=model,
            messages=(LLMMessage(role="user", parts=[TextPart(text="Hi")]),),
            temperature=temperature,
        )

    def test_temperature_omitted_by_default(self) -> None:
        """Temperature omitted by default."""
        provider = self._provider("databricks-claude-sonnet-4-5")
        params = provider.build_params(
            self._request("databricks-claude-sonnet-4-5", None)
        )
        assert "temperature" not in params

    def test_explicit_temperature_sent_for_supported_model(self) -> None:
        """Explicit temperature sent for supported model."""
        provider = self._provider("databricks-claude-sonnet-4-5")
        params = provider.build_params(
            self._request("databricks-claude-sonnet-4-5", 0.4)
        )
        assert params["temperature"] == 0.4

    def test_explicit_temperature_dropped_for_unsupported_model(self) -> None:
        """Explicit temperature dropped for unsupported model."""
        provider = self._provider("databricks-gpt-5-2")
        params = provider.build_params(
            self._request("databricks-gpt-5-2", 0.0)
        )
        assert "temperature" not in params

    def test_runtime_mark_drops_temperature(self) -> None:
        """Runtime mark drops temperature."""
        provider = self._provider("databricks-claude-sonnet-4-5")
        provider.mark_temperature_unsupported()
        params = provider.build_params(
            self._request("databricks-claude-sonnet-4-5", 0.4)
        )
        assert "temperature" not in params


class _SampleOutput(BaseModel):
    result: str


class _RecoveryProvider(Provider):
    """Minimal provider that 400s on temperature until it is disabled."""

    def __init__(self) -> None:
        self._temperature_unsupported = False
        self.calls = 0

    provider_name = "databricks"
    model = "databricks-gpt-5-2"
    profile = ModelProfile(supports_temperature=False)

    @property
    def temperature_unsupported(self) -> bool:
        return self._temperature_unsupported

    def mark_temperature_unsupported(self) -> None:
        self._temperature_unsupported = True

    @override
    async def complete(
        self, request: LLMRequest, **_: object
    ) -> CompletionResponse:
        self.calls += 1
        if not self._temperature_unsupported:
            raise LLMInvalidRequestError(
                message=(
                    "Unsupported value: 'temperature' does not support 0.0 "
                    "with this model. Only the default (1) value is supported."
                ),
                provider="databricks",
                status_code=400,
            )
        return CompletionResponse(
            message=LLMMessage(
                role="assistant", parts=[TextPart(text='{"result": "ok"}')]
            ),
            usage=Usage(input_tokens=5, output_tokens=2, total_tokens=7),
        )


class _Strategy(OutputStrategy):
    @override
    def build_request(
        self,
        model: str,
        messages: list[LLMMessage],
        response_model: type[BaseModel],
        profile: ModelProfile,
        plan: SchemaPlan | None,
        temperature: float | None = None,
    ) -> LLMRequest:
        # Pin temperature to 0 whatever the caller asked for.
        return LLMRequest(
            model=model, messages=tuple(messages), temperature=0.0
        )

    @override
    def parse_response(
        self, response: CompletionResponse, response_model: type[ModelT]
    ) -> ModelT:
        return response_model.model_validate({"result": "ok"})


@pytest.mark.asyncio
async def test_call_with_retry_recovers_from_temperature_400() -> None:
    """A temperature-400 disables temperature and the retried call succeeds."""
    from penguiflow.llm.retry import call_with_retry

    provider = _RecoveryProvider()
    messages = [LLMMessage(role="user", parts=[TextPart(text="Hi")])]

    result, _cost = await call_with_retry(
        provider=provider,
        base_messages=messages,
        response_model=_SampleOutput,
        output_strategy=_Strategy(),
        temperature=0.0,
    )

    assert result.result == "ok"
    assert provider.calls == 2  # first 400, second succeeds
    assert provider.temperature_unsupported is True
