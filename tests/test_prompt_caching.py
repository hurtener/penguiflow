"""Prompt caching for hosted Claude: the marker, the usage it reports, and what it costs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from penguiflow.llm.pricing import calculate_cost
from penguiflow.llm.types import LLMMessage, LLMRequest, TextPart


@pytest.fixture
def openai_sdk() -> MagicMock:
    sdk = MagicMock()
    sdk.AsyncOpenAI.return_value = MagicMock()
    return sdk


def _provider(openai_sdk: MagicMock, model: str):
    with patch.dict("sys.modules", {"openai": openai_sdk}):
        from penguiflow.llm.providers.databricks import DatabricksProvider

        return DatabricksProvider(model, host="ws.cloud.databricks.com", token="t")


def _request() -> LLMRequest:
    return LLMRequest(
        model="m",
        messages=(
            LLMMessage(role="system", parts=[TextPart(text="the long stable planner prompt")]),
            LLMMessage(role="system", parts=[TextPart(text="per-run memory")]),
            LLMMessage(role="user", parts=[TextPart(text="question")]),
        ),
    )


def test_the_first_system_message_carries_the_cache_breakpoint(openai_sdk: MagicMock) -> None:
    messages = _provider(openai_sdk, "databricks-claude-sonnet-4-6")._build_params(_request())["messages"]
    assert messages[0]["content"] == [
        {"type": "text", "text": "the long stable planner prompt", "cache_control": {"type": "ephemeral"}}
    ]


def test_only_the_first_system_message_is_marked(openai_sdk: MagicMock) -> None:
    """Later system messages vary per run (memory); marking them would cache nothing reusable."""

    messages = _provider(openai_sdk, "databricks-claude-sonnet-4-6")._build_params(_request())["messages"]
    assert messages[1]["content"] == "per-run memory"
    assert messages[2]["content"] == "question"


def test_a_non_claude_model_is_not_marked(openai_sdk: MagicMock) -> None:
    messages = _provider(openai_sdk, "databricks-gpt-oss-120b")._build_params(_request())["messages"]
    assert messages[0]["content"] == "the long stable planner prompt"


def test_the_environment_can_turn_caching_off(openai_sdk: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENGUIFLOW_DATABRICKS_PROMPT_CACHE", "0")
    messages = _provider(openai_sdk, "databricks-claude-sonnet-4-6")._build_params(_request())["messages"]
    assert messages[0]["content"] == "the long stable planner prompt"


def test_cache_counts_are_read_from_the_endpoints_usage(openai_sdk: MagicMock) -> None:
    """The shape the Databricks endpoint returned in the live probe: prompt_tokens includes the cache."""

    provider = _provider(openai_sdk, "databricks-claude-sonnet-4-6")
    usage = SimpleNamespace(
        prompt_tokens=5515, completion_tokens=4, total_tokens=5519, cache_read_input_tokens=5502, cache_creation_input_tokens=0
    )
    message = SimpleNamespace(content="ok", tool_calls=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)
    _, parsed = provider._from_openai_response(response)
    assert parsed.input_tokens == 5515
    assert parsed.cache_read_input_tokens == 5502
    assert parsed.cache_creation_input_tokens == 0


def test_usage_without_cache_fields_reads_as_zero(openai_sdk: MagicMock) -> None:
    provider = _provider(openai_sdk, "databricks-claude-sonnet-4-6")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")], usage=usage
    )
    _, parsed = provider._from_openai_response(response)
    assert parsed.cache_read_input_tokens == 0 and parsed.cache_creation_input_tokens == 0


def test_a_cache_read_costs_a_tenth_of_an_input_token() -> None:
    model = "databricks-claude-sonnet-4-6"
    full = calculate_cost(model, 50_000, 0)
    cached = calculate_cost(model, 50_000, 0, cache_read_input_tokens=50_000)
    assert cached == pytest.approx(full * 0.10)


def test_a_cache_write_costs_a_quarter_more_than_an_input_token() -> None:
    model = "databricks-claude-sonnet-4-6"
    full = calculate_cost(model, 50_000, 0)
    written = calculate_cost(model, 50_000, 0, cache_creation_input_tokens=50_000)
    assert written == pytest.approx(full * 1.25)


def test_no_cache_counts_leaves_the_price_unchanged() -> None:
    model = "databricks-claude-sonnet-4-6"
    assert calculate_cost(model, 1_000, 200) == calculate_cost(
        model, 1_000, 200, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
