"""Tests for ReAct LLM fallback integration."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from typing_extensions import override

from penguiflow.llm.fallback import (
    GenericFallbackLLMClient,
    ModelFallbackConfig,
    NativeAdapter,
)
from penguiflow.planner import DSPyLLMClient, ReactPlanner
from penguiflow.planner.llm import _LiteLLMJSONClient
from penguiflow.planner.models import JSONLLMClient, ReflectionConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class _UnsupportedClient(JSONLLMClient):
    @override
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> str | tuple[str, float]:
        return "{}", 0.0


@pytest.mark.asyncio
async def test_planner_litellm_path_wraps_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner LiteLLM path wraps with fallback."""
    call_order: list[str] = []

    class RateLimitError(Exception):
        pass

    async def acompletion(**kwargs: object) -> dict[str, object]:
        model = kwargs["model"]
        assert isinstance(model, str)
        call_order.append(model)
        if model == "primary":
            msg = "rate limited"
            raise RateLimitError(msg)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"next_node":"final_response",'
                            '"args":{"answer":"ok"}}'
                        )
                    }
                }
            ],
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

    assert isinstance(planner.client, GenericFallbackLLMClient)
    result = await planner.client.complete(
        messages=[{"role": "user", "content": "hi"}]
    )
    assert isinstance(result, tuple)
    text, _ = result
    assert '"answer":"ok"' in text
    assert call_order == ["primary", "backup"]


def test_planner_passes_reasoning_display_to_litellm_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner passes reasoning display to LiteLLM client."""
    fake_litellm = MagicMock()
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    planner = ReactPlanner(
        llm="databricks/databricks-claude-sonnet-5",
        catalog=[],
        use_native_llm=False,
        reasoning_effort="high",
        reasoning_display="summarized",
    )

    assert isinstance(planner.client, _LiteLLMJSONClient)
    assert planner.client.reasoning_display == "summarized"


def test_planner_rejects_full_reasoning_display() -> None:
    """Planner rejects full reasoning display."""
    with pytest.raises(ValueError, match="reasoning_display"):
        # The setting arrives untyped, as when read from JSON config.
        ReactPlanner(
            llm="primary",
            catalog=[],
            **json.loads('{"reasoning_display": "full"}'),
        )


def test_dspy_client_construction_is_deprecated() -> None:
    """DSPy client construction is deprecated."""
    with pytest.warns(DeprecationWarning, match="DSPyLLMClient is deprecated"):
        DSPyLLMClient(llm="primary")


def test_planner_rejects_dspy_client_with_fallback() -> None:
    """Planner rejects DSPy client with fallback."""
    with pytest.warns(DeprecationWarning):
        client = DSPyLLMClient(llm="primary")
    # DSPy is deprecated and treated like any other custom client: fallback is
    # not auto-wired and must fail loudly rather than be silently ignored or
    # wrapped (wrapping previously broke clarification's output schema
    # dispatch).
    with pytest.raises(
        ValueError,
        match="llm_fallback is not supported with a custom llm_client",
    ):
        ReactPlanner(
            llm_client=client,
            catalog=[],
            llm_fallback=ModelFallbackConfig(models=["backup"]),
            reflection_config=ReflectionConfig(enabled=True),
            token_budget=100,
        )


def test_planner_dspy_client_without_fallback_builds_unwrapped() -> None:
    """Planner DSPy client without fallback builds unwrapped."""
    with pytest.warns(DeprecationWarning):
        client = DSPyLLMClient(llm="primary")
    planner = ReactPlanner(
        llm_client=client,
        catalog=[],
        reflection_config=ReflectionConfig(enabled=True),
        token_budget=100,
    )
    # Auxiliary DSPy clients keep their dedicated output schemas (must stay
    # DSPyLLMClient so generate_clarification's isinstance dispatch routes to
    # the ClarificationResponse-schema client).
    assert isinstance(planner.client, DSPyLLMClient)
    assert isinstance(planner.clarification_client, DSPyLLMClient)
    assert isinstance(planner.reflection_client, DSPyLLMClient)
    assert isinstance(planner.summarizer_client, DSPyLLMClient)


def test_planner_rejects_unsupported_custom_client_with_fallback() -> None:
    """Planner rejects unsupported custom client with fallback."""
    with pytest.raises(
        ValueError,
        match="llm_fallback is not supported with a custom llm_client",
    ):
        ReactPlanner(
            llm_client=_UnsupportedClient(),
            catalog=[],
            llm_fallback=ModelFallbackConfig(models=["backup"]),
        )


def _unused_client_factory(model: str, **_options: object) -> JSONLLMClient:
    """Client factory the wrapper never calls during construction."""
    msg = f"no client expected for {model}"
    raise AssertionError(msg)


def _unused_adapter_factory(model: str, **_options: object) -> NativeAdapter:
    """Adapter factory the wrapper never calls during construction."""
    msg = f"no adapter expected for {model}"
    raise AssertionError(msg)


def test_fallback_wrappers_are_streaming_capable() -> None:
    # The planner gates streaming/reasoning callbacks on this predicate; the
    # fallback wrappers must qualify so enabling llm_fallback does not silently
    # disable streaming.
    """Fallback wrappers are streaming capable."""
    from penguiflow.llm.fallback import FallbackLLMClient
    from penguiflow.llm.protocol import NativeLLMAdapter
    from penguiflow.planner.llm import supports_callback_streaming

    cfg = ModelFallbackConfig(models=["primary", "backup"])
    generic = GenericFallbackLLMClient(
        "primary", cfg, client_factory=_unused_client_factory
    )
    native = FallbackLLMClient(
        "primary", cfg, adapter_factory=_unused_adapter_factory
    )

    assert supports_callback_streaming(generic) is True
    assert supports_callback_streaming(native) is True
    assert (
        supports_callback_streaming(
            NativeLLMAdapter("openai/gpt-4o", api_key="x")
        )
        is True
    )
    assert supports_callback_streaming(_UnsupportedClient()) is False


def test_litellm_client_can_disable_rate_limit_retries() -> None:
    """LiteLLM client can disable rate limit retries."""
    client = _LiteLLMJSONClient(
        "primary",
        temperature=0.0,
        json_schema_mode=False,
        retry_rate_limit_errors=False,
    )
    assert client.retry_rate_limit_errors is False
