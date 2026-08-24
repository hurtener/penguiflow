"""genai-prices integration tests for the LLM pricing facade."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import penguiflow.llm.pricing as pricing
from penguiflow.llm.pricing import calculate_cost, get_pricing, register_pricing


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


class _FakeGenaiPrices:
    Usage = _FakeUsage

    def __init__(self, prices: dict[str, tuple[Decimal, Decimal]]) -> None:
        self.prices = prices
        self.seen: list[str] = []

    def calc_price(self, usage: _FakeUsage, *, model_ref: str) -> Any:
        self.seen.append(model_ref)
        if model_ref not in self.prices:
            raise LookupError(model_ref)
        input_rate, output_rate = self.prices[model_ref]
        return SimpleNamespace(
            input_price=input_rate * Decimal(usage.input_tokens) / Decimal(1000),
            output_price=output_rate * Decimal(usage.output_tokens) / Decimal(1000),
        )


@pytest.fixture(autouse=True)
def _reset_pricing_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pricing_snapshot = dict(pricing.PRICING)
    monkeypatch.setattr(pricing, "_REGISTERED_PRICING", {})
    pricing._genai_prices_module.cache_clear()
    yield
    pricing.PRICING.clear()
    pricing.PRICING.update(pricing_snapshot)
    cache_clear = getattr(pricing._genai_prices_module, "cache_clear", None)
    if callable(cache_clear):
        cache_clear()


def test_registered_override_beats_genai_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGenaiPrices({"private-model": (Decimal("0.111"), Decimal("0.222"))})
    monkeypatch.setattr(pricing, "_genai_prices_module", lambda: fake)

    register_pricing("private-model", 0.001, 0.002)

    assert get_pricing("private-model") == (0.001, 0.002)
    assert calculate_cost("private-model", input_tokens=1000, output_tokens=1000) == pytest.approx(0.003)
    assert fake.seen == []


def test_registered_override_beats_genai_prices_for_versioned_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGenaiPrices({"gpt-4o-2024-11-20": (Decimal("0.111"), Decimal("0.222"))})
    monkeypatch.setattr(pricing, "_genai_prices_module", lambda: fake)

    register_pricing("gpt-4o", 0.001, 0.002)

    assert get_pricing("gpt-4o-2024-11-20") == (0.001, 0.002)
    assert calculate_cost("gpt-4o-2024-11-20", input_tokens=1000, output_tokens=1000) == pytest.approx(0.003)
    assert fake.seen == []


def test_genai_prices_beats_static_table(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGenaiPrices({"gpt-4o": (Decimal("0.123"), Decimal("0.456"))})
    monkeypatch.setattr(pricing, "_genai_prices_module", lambda: fake)
    monkeypatch.setitem(pricing.PRICING, "gpt-4o", (0.0025, 0.01))

    assert get_pricing("gpt-4o") == (0.123, 0.456)


def test_static_table_used_when_genai_prices_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pricing, "_genai_prices_module", lambda: None)
    monkeypatch.setitem(pricing.PRICING, "static-only-model", (0.007, 0.009))

    assert get_pricing("static-only-model") == (0.007, 0.009)
    assert calculate_cost("static-only-model", input_tokens=1000, output_tokens=2000) == pytest.approx(0.025)


def test_prefix_normalization_tries_databricks_stripped_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGenaiPrices({"gpt-5-4-mini": (Decimal("0.00075"), Decimal("0.0045"))})
    monkeypatch.setattr(pricing, "_genai_prices_module", lambda: fake)

    assert get_pricing("databricks-gpt-5-4-mini") == (0.00075, 0.0045)
    assert "databricks-gpt-5-4-mini" in fake.seen
    assert "gpt-5-4-mini" in fake.seen


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("databricks-claude-opus-4-7", (0.005, 0.025)),
        ("databricks-claude-opus-4-8", (0.005, 0.025)),
        ("databricks-gpt-5-5", (0.005, 0.03)),
        ("databricks-gpt-5-4-mini", (0.00075, 0.0045)),
    ],
)
def test_genai_prices_parity_for_production_models(model: str, expected: tuple[float, float]) -> None:
    assert get_pricing(model) == pytest.approx(expected)


def test_databricks_claude_sonnet_5_cost_parity() -> None:
    assert calculate_cost(
        "databricks/databricks-claude-sonnet-5", input_tokens=1000, output_tokens=1000
    ) == pytest.approx(0.012)


def test_calculate_cost_uses_genai_prices_tiered_usage() -> None:
    assert get_pricing("claude-sonnet-4.5") == pytest.approx((0.003, 0.015))

    # Sonnet 4.5's long-context tier starts above 200K input tokens in the
    # bundled genai-prices snapshot. This verifies actual usage goes through
    # calc_price(), not the base-rate facade.
    assert calculate_cost("claude-sonnet-4.5", input_tokens=200_001, output_tokens=200_001) == pytest.approx(
        5.7000285
    )
