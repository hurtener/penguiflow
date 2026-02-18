"""Smoke tests for the high-level LLMClient.

These tests use a fake Provider to avoid external network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from penguiflow.llm.client import LLMClient, generate_structured
from penguiflow.llm.profiles import ModelProfile
from penguiflow.llm.providers.base import Provider
from penguiflow.llm.schema.plan import OutputMode
from penguiflow.llm.types import CompletionResponse, LLMMessage, LLMRequest, TextPart, Usage


class Answer(BaseModel):
    text: str
    confidence: float


@dataclass
class FakeProvider(Provider):
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
    def provider_name(self) -> str:  # pragma: no cover - trivial
        return self._provider_name

    @property
    def profile(self) -> ModelProfile:  # pragma: no cover - trivial
        return self._profile

    @property
    def model(self) -> str:  # pragma: no cover - trivial
        return self._model

    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel=None,
        stream: bool = False,
        on_stream_event=None,
    ) -> CompletionResponse:
        _ = (request, timeout_s, cancel, stream, on_stream_event)
        return CompletionResponse(
            message=LLMMessage(
                role="assistant",
                parts=[TextPart(text='{"text":"ok","confidence":0.9}')],
            ),
            usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
            raw_response={"ok": True},
        )


class SequenceProvider(Provider):
    def __init__(self, *, payloads: list[str], profile: ModelProfile):
        self._payloads = list(payloads)
        self._profile = profile

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return "gpt-4o"

    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel=None,
        stream: bool = False,
        on_stream_event=None,
    ) -> CompletionResponse:
        _ = (request, timeout_s, cancel, stream, on_stream_event)
        payload = self._payloads.pop(0)
        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=[TextPart(text=payload)]),
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


class ErrorProvider(Provider):
    def __init__(self, *, profile: ModelProfile):
        self._profile = profile

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return "gpt-4o"

    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel=None,
        stream: bool = False,
        on_stream_event=None,
    ) -> CompletionResponse:
        _ = (request, timeout_s, cancel, stream, on_stream_event)
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_llm_client_generate_native_mode_success() -> None:
    provider = FakeProvider(_model="gpt-4o", _provider_name="openai")
    client = LLMClient("openai/gpt-4o", provider=provider, profile=provider.profile)
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
async def test_llm_client_generate_with_nim_model_and_injected_provider() -> None:
    provider = FakeProvider(_model="qwen/qwen3.5-397b-a17b", _provider_name="nim")
    client = LLMClient("nim/qwen/qwen3.5-397b-a17b", provider=provider, profile=provider.profile)
    result = await client.generate(
        [LLMMessage(role="user", parts=[TextPart(text="hello")])],
        Answer,
        force_mode=OutputMode.NATIVE,
    )
    assert isinstance(result.data, Answer)
    assert result.data.text == "ok"


@pytest.mark.asyncio
async def test_generate_structured_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(_model="gpt-4o", _provider_name="openai")

    # generate_structured() instantiates LLMClient, so monkeypatch its factory hooks.
    import penguiflow.llm.client as client_mod

    def _fake_create_provider(*_args, **_kwargs):
        return provider

    def _fake_get_profile(*_args, **_kwargs):
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
    profile = ModelProfile(
        supports_schema_guided_output=True,
        supports_tools=True,
        supports_streaming=False,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
    )
    client = LLMClient("openai/gpt-4o", provider=ErrorProvider(profile=profile), profile=profile)
    with pytest.raises(RuntimeError, match="boom"):
        await client.generate(
            [LLMMessage(role="user", parts=[TextPart(text="hello")])],
            Answer,
            max_retries=0,
        )
