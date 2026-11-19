"""Smoke tests for the high-level LLMClient.

These tests use a fake Provider to avoid external network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel
from typing_extensions import override

from penguiflow.llm.client import (
    LLMClient,
    _merge_fallback_profile,
    generate_structured,
)
from penguiflow.llm.errors import LLMRateLimitError
from penguiflow.llm.fallback import ModelFallbackConfig
from penguiflow.llm.profiles import ModelProfile, register_profile
from penguiflow.llm.providers.base import Provider
from penguiflow.llm.schema.plan import OutputMode
from penguiflow.llm.types import (
    CancelToken,
    CompletionResponse,
    LLMMessage,
    LLMRequest,
    StreamCallback,
    TextPart,
    Usage,
)


class Answer(BaseModel):
    """Answer payload."""

    text: str
    confidence: float


@dataclass
class FakeProvider(Provider):
    """Provider double for the tests."""

    _model: str = "fake-model"
    _provider_name: str = "fake"
    _profile: ModelProfile = field(
        default_factory=lambda: ModelProfile(
            supports_schema_guided_output=True,
            supports_tools=True,
            supports_streaming=False,
            default_output_mode="native",
            native_structured_kind="openai_response_format",
        )
    )

    @property
    @override
    def provider_name(self) -> str:  # pragma: no cover - trivial
        return self._provider_name

    @property
    @override
    def profile(self) -> ModelProfile:  # pragma: no cover - trivial
        return self._profile

    @property
    @override
    def model(self) -> str:  # pragma: no cover - trivial
        return self._model

    @override
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        return CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='{"text":"ok","confidence":0.9}')],
            ),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            raw_response={"ok": True},
        )


class SequenceProvider(Provider):
    """Provider double for the tests."""

    def __init__(self, *, payloads: list[str], profile: ModelProfile) -> None:
        """Replay ``payloads`` in order."""
        self._payloads = list(payloads)
        self._profile = profile

    @property
    @override
    def provider_name(self) -> str:
        return "fake"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return "gpt-4o"

    @override
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        payload = self._payloads.pop(0)
        return CompletionResponse(
            message=LLMMessage(
                role="assistant", parts=[TextPart(text=payload)]
            ),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


class ErrorProvider(Provider):
    """Provider double for the tests."""

    def __init__(self, *, profile: ModelProfile) -> None:
        """Store ``profile``."""
        self._profile = profile

    @property
    @override
    def provider_name(self) -> str:
        return "fake"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return "gpt-4o"

    @override
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        msg = "boom"
        raise RuntimeError(msg)


class RateLimitThenSuccessProvider(Provider):
    """Provider double for the tests."""

    def __init__(self, model: str, first_rate_limited: bool = False) -> None:
        """Store ``model`` and ``first_rate_limited``."""
        self._model = model
        self._first_rate_limited = first_rate_limited
        self.calls = 0
        self._profile = ModelProfile(
            supports_schema_guided_output=True,
            supports_tools=True,
            supports_streaming=False,
            default_output_mode="native",
            native_structured_kind="openai_response_format",
        )

    @property
    @override
    def provider_name(self) -> str:
        return "fake"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return self._model

    @override
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        self.calls += 1
        if self._first_rate_limited and self.calls == 1:
            raise LLMRateLimitError(
                message="429", provider="fake", status_code=429
            )
        return CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='{"text":"ok","confidence":0.9}')],
            ),
            usage=Usage(input_tokens=2, output_tokens=1, total_tokens=3),
        )


@pytest.mark.asyncio
async def test_llm_client_generate_native_mode_success() -> None:
    """LLM client generate native mode success."""
    provider = FakeProvider(_model="gpt-4o", _provider_name="openai")
    client = LLMClient(
        "openai/gpt-4o", provider=provider, profile=provider.profile
    )
    result = await client.generate(
        [LLMMessage(role="user", parts=[TextPart(text="hello")])],
        Answer,
    )
    assert result.mode_used == OutputMode.NATIVE
    assert isinstance(result.data, Answer)
    assert result.data.text == "ok"
    assert result.data.confidence == 0.9
    assert result.attempts >= 1


@pytest.mark.asyncio
async def test_llm_client_generate_with_nim_model_and_injected_provider() -> (
    None
):
    """LLM client generate with NIM model and injected provider."""
    provider = FakeProvider(
        _model="qwen/qwen3.5-397b-a17b", _provider_name="nim"
    )
    client = LLMClient(
        "nim/qwen/qwen3.5-397b-a17b",
        provider=provider,
        profile=provider.profile,
    )
    result = await client.generate(
        [LLMMessage(role="user", parts=[TextPart(text="hello")])],
        Answer,
        force_mode=OutputMode.NATIVE,
    )
    assert isinstance(result.data, Answer)
    assert result.data.text == "ok"


@pytest.mark.asyncio
async def test_generate_structured_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate structured smoke."""
    provider = FakeProvider(_model="gpt-4o", _provider_name="openai")

    # generate_structured() instantiates LLMClient, so monkeypatch its factory
    # hooks.
    import penguiflow.llm.client as client_mod

    def _fake_create_provider(*_args: str, **_kwargs: None) -> FakeProvider:
        return provider

    def _fake_get_profile(*_args: str, **_kwargs: object) -> ModelProfile:
        return provider.profile

    monkeypatch.setattr(client_mod, "create_provider", _fake_create_provider)
    monkeypatch.setattr(client_mod, "get_profile", _fake_get_profile)

    data = await generate_structured(
        "openai/gpt-4o",
        [LLMMessage(role="user", parts=[TextPart(text="hello")])],
        Answer,
        force_mode=OutputMode.NATIVE,
    )
    assert isinstance(data, Answer)
    assert data.text == "ok"


@pytest.mark.asyncio
async def test_llm_client_generate_retries_on_validation_error() -> None:
    """LLM client generate retries on validation error."""
    profile = ModelProfile(
        supports_schema_guided_output=True,
        supports_tools=True,
        supports_streaming=False,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
    )
    provider = SequenceProvider(
        payloads=[
            '{"text":"missing_confidence"}',
            '{"text":"ok","confidence":0.9}',
        ],
        profile=profile,
    )
    client = LLMClient("openai/gpt-4o", provider=provider, profile=profile)
    result = await client.generate(
        [LLMMessage(role="user", parts=[TextPart(text="hello")])],
        Answer,
        max_retries=1,
    )
    assert result.attempts == 2
    assert isinstance(result.data, Answer)
    assert result.data.text == "ok"


@pytest.mark.asyncio
async def test_llm_client_generate_emits_error_path() -> None:
    """LLM client generate emits error path."""
    profile = ModelProfile(
        supports_schema_guided_output=True,
        supports_tools=True,
        supports_streaming=False,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
    )
    client = LLMClient(
        "openai/gpt-4o",
        provider=ErrorProvider(profile=profile),
        profile=profile,
    )
    with pytest.raises(RuntimeError, match="boom"):
        await client.generate(
            [LLMMessage(role="user", parts=[TextPart(text="hello")])],
            Answer,
            max_retries=0,
        )


def test_llm_client_rejects_provider_plus_fallback() -> None:
    """LLM client rejects provider plus fallback."""
    provider = FakeProvider(_model="gpt-4o", _provider_name="openai")
    with pytest.raises(ValueError, match="fallback cannot be combined"):
        LLMClient(
            "openai/gpt-4o",
            provider=provider,
            profile=provider.profile,
            fallback=ModelFallbackConfig(models=["openai/gpt-4o-mini"]),
        )


@pytest.mark.asyncio
async def test_llm_client_generate_uses_provider_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM client generate uses provider fallback."""
    providers = {
        "openai/gpt-4o": RateLimitThenSuccessProvider(
            "openai/gpt-4o", first_rate_limited=True
        ),
        "openai/gpt-4o-mini": RateLimitThenSuccessProvider(
            "openai/gpt-4o-mini"
        ),
    }

    import penguiflow.llm.client as client_mod

    monkeypatch.setattr(
        client_mod, "create_provider", lambda model, **_: providers[model]
    )
    monkeypatch.setattr(
        client_mod, "get_profile", lambda model: providers[model].profile
    )

    client = LLMClient(
        "openai/gpt-4o",
        fallback=ModelFallbackConfig(models=["openai/gpt-4o-mini"]),
    )
    result = await client.generate(
        [LLMMessage(role="user", parts=[TextPart(text="hello")])], Answer
    )
    assert result.data.text == "ok"
    assert providers["openai/gpt-4o"].calls == 1
    assert providers["openai/gpt-4o-mini"].calls == 1


@pytest.mark.asyncio
async def test_llm_client_complete_raw_uses_provider_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM client complete raw uses provider fallback."""
    providers = {
        "openai/gpt-4o": RateLimitThenSuccessProvider(
            "openai/gpt-4o", first_rate_limited=True
        ),
        "openai/gpt-4o-mini": RateLimitThenSuccessProvider(
            "openai/gpt-4o-mini"
        ),
    }

    import penguiflow.llm.client as client_mod

    monkeypatch.setattr(
        client_mod, "create_provider", lambda model, **_: providers[model]
    )
    monkeypatch.setattr(
        client_mod, "get_profile", lambda model: providers[model].profile
    )

    client = LLMClient(
        "openai/gpt-4o",
        fallback=ModelFallbackConfig(models=["openai/gpt-4o-mini"]),
    )
    response = await client.complete_raw(
        LLMRequest(
            model="openai/gpt-4o",
            messages=[LLMMessage(role="user", parts=[TextPart(text="raw")])],
        )
    )
    assert response.message.text == '{"text":"ok","confidence":0.9}'


def test_merge_fallback_profile_downgrades_when_backup_lacks_native() -> None:
    """Merge fallback profile downgrades when backup lacks native."""
    register_profile(
        "fakeco/native-primary",
        ModelProfile(
            supports_schema_guided_output=True,
            default_output_mode="native",
            native_structured_kind="openai_response_format",
        ),
    )
    register_profile(
        "fakeco/prompted-backup",
        ModelProfile(
            supports_schema_guided_output=False,
            supports_tools=False,
            default_output_mode="prompted",
        ),
    )
    merged = _merge_fallback_profile(
        "fakeco/native-primary",
        ModelFallbackConfig(models=["fakeco/prompted-backup"]),
    )
    # The backup can't do native structured output, so the chain must not lead
    # with native — it downgrades to prompted (no tool support either).
    assert merged.supports_schema_guided_output is False
    assert merged.default_output_mode == "prompted"


def test_merge_fallback_profile_disables_native_on_mismatched_kind() -> None:
    """Merge fallback profile disables native on mismatched kind."""
    register_profile(
        "fakeco/openai-kind",
        ModelProfile(
            supports_schema_guided_output=True,
            default_output_mode="native",
            native_structured_kind="openai_response_format",
        ),
    )
    register_profile(
        "fakeco/databricks-kind",
        ModelProfile(
            supports_schema_guided_output=True,
            supports_tools=True,
            default_output_mode="native",
            native_structured_kind="databricks_constrained_decoding",
        ),
    )
    merged = _merge_fallback_profile(
        "fakeco/openai-kind",
        ModelFallbackConfig(models=["fakeco/databricks-kind"]),
    )
    # Both support native, but the request formats are incompatible across the
    # chain, so native is disabled and the chain falls back to tools.
    assert merged.supports_schema_guided_output is False
    assert merged.supports_tools is True
    assert merged.default_output_mode == "tools"


def test_merge_fallback_profile_keeps_native_for_same_family() -> None:
    """Merge fallback profile keeps native for same family."""
    register_profile(
        "fakeco/big",
        ModelProfile(
            supports_schema_guided_output=True,
            default_output_mode="native",
            native_structured_kind="openai_response_format",
        ),
    )
    register_profile(
        "fakeco/small",
        ModelProfile(
            supports_schema_guided_output=True,
            default_output_mode="native",
            native_structured_kind="openai_response_format",
        ),
    )
    merged = _merge_fallback_profile(
        "fakeco/big", ModelFallbackConfig(models=["fakeco/small"])
    )
    assert merged.supports_schema_guided_output is True
    assert merged.default_output_mode == "native"
