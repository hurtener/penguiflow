from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from penguiflow.llm.fallback import GenericFallbackLLMClient, ModelFallbackConfig
from penguiflow.planner import DSPyLLMClient, ReactPlanner
from penguiflow.planner.models import ReflectionConfig
from penguiflow.planner.react_init import _LiteLLMJSONClient


class _UnsupportedClient:
    async def complete(self, *, messages, response_format=None, stream=False, on_stream_chunk=None):  # type: ignore[no-untyped-def]
        del messages, response_format, stream, on_stream_chunk
        return "{}", 0.0


@pytest.mark.asyncio
async def test_planner_litellm_path_wraps_with_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    call_order: list[str] = []

    class RateLimitError(Exception):
        pass

    async def acompletion(**kwargs):  # type: ignore[no-untyped-def]
        model = kwargs["model"]
        call_order.append(model)
        if model == "primary":
            raise RateLimitError("rate limited")
        return {
            "choices": [{"message": {"content": '{"next_node":"final_response","args":{"answer":"ok"}}'}}],
            "usage": {"total_tokens": 10},
            "_hidden_params": {"response_cost": 0.0},
        }

    fake_litellm = MagicMock()
    fake_litellm.acompletion = AsyncMock(side_effect=acompletion)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    planner = ReactPlanner(
        llm="primary",
        catalog=[],
        use_native_llm=False,
        llm_fallback=ModelFallbackConfig(models=["backup"]),
    )

    assert isinstance(planner._client, GenericFallbackLLMClient)
    text, _ = await planner._client.complete(messages=[{"role": "user", "content": "hi"}])
    assert '"answer":"ok"' in text
    assert call_order == ["primary", "backup"]


def test_planner_passes_reasoning_display_to_litellm_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_litellm = MagicMock()
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    planner = ReactPlanner(
        llm="databricks/databricks-claude-sonnet-5",
        catalog=[],
        use_native_llm=False,
        reasoning_effort="high",
        reasoning_display="summarized",
    )

    assert isinstance(planner._client, _LiteLLMJSONClient)
    assert planner._client._reasoning_display == "summarized"


def test_planner_rejects_full_reasoning_display() -> None:
    with pytest.raises(ValueError, match="reasoning_display"):
        ReactPlanner(llm="primary", catalog=[], reasoning_display="full")


def test_dspy_client_construction_is_deprecated() -> None:
    with pytest.warns(DeprecationWarning, match="DSPyLLMClient is deprecated"):
        DSPyLLMClient(llm="primary")


def test_planner_rejects_dspy_client_with_fallback() -> None:
    with pytest.warns(DeprecationWarning):
        client = DSPyLLMClient(llm="primary")
    # DSPy is deprecated and treated like any other custom client: fallback is not
    # auto-wired and must fail loudly rather than be silently ignored or wrapped
    # (wrapping previously broke clarification's output schema dispatch).
    with pytest.raises(ValueError, match="llm_fallback is not supported with a custom llm_client"):
        ReactPlanner(
            llm_client=client,
            catalog=[],
            llm_fallback=ModelFallbackConfig(models=["backup"]),
            reflection_config=ReflectionConfig(enabled=True),
            token_budget=100,
        )


def test_planner_dspy_client_without_fallback_builds_unwrapped() -> None:
    with pytest.warns(DeprecationWarning):
        client = DSPyLLMClient(llm="primary")
    planner = ReactPlanner(
        llm_client=client,
        catalog=[],
        reflection_config=ReflectionConfig(enabled=True),
        token_budget=100,
    )
    # Auxiliary DSPy clients keep their dedicated output schemas (must stay
    # DSPyLLMClient so generate_clarification's isinstance dispatch routes to the
    # ClarificationResponse-schema client).
    assert isinstance(planner._client, DSPyLLMClient)
    assert isinstance(planner._clarification_client, DSPyLLMClient)
    assert isinstance(planner._reflection_client, DSPyLLMClient)
    assert isinstance(planner._summarizer_client, DSPyLLMClient)


def test_planner_rejects_unsupported_custom_client_with_fallback() -> None:
    with pytest.raises(ValueError, match="llm_fallback is not supported with a custom llm_client"):
        ReactPlanner(
            llm_client=_UnsupportedClient(),
            catalog=[],
            llm_fallback=ModelFallbackConfig(models=["backup"]),
        )


def test_fallback_wrappers_are_streaming_capable() -> None:
    # The planner gates streaming/reasoning callbacks on this predicate; the
    # fallback wrappers must qualify so enabling llm_fallback does not silently
    # disable streaming.
    from penguiflow.llm.fallback import FallbackLLMClient
    from penguiflow.llm.protocol import NativeLLMAdapter
    from penguiflow.planner.llm import supports_callback_streaming

    cfg = ModelFallbackConfig(models=["primary", "backup"])
    generic = GenericFallbackLLMClient(
        "primary", cfg, client_factory=lambda model, *, api_key=None, **_: object()
    )
    native = FallbackLLMClient("primary", cfg, adapter_factory=lambda model, **_: object())

    assert supports_callback_streaming(generic) is True
    assert supports_callback_streaming(native) is True
    assert supports_callback_streaming(NativeLLMAdapter("openai/gpt-4o", api_key="x")) is True
    assert supports_callback_streaming(_UnsupportedClient()) is False


def test_litellm_client_can_disable_rate_limit_retries() -> None:
    client = _LiteLLMJSONClient(
        "primary",
        temperature=0.0,
        json_schema_mode=False,
        retry_rate_limit_errors=False,
    )
    assert client._retry_rate_limit_errors is False
