"""Tests for the LLM pricing module."""

from __future__ import annotations

import pytest

from penguiflow.llm.pricing import (
    calculate_cost,
    calculate_cost_from_usage,
    get_pricing,
    register_pricing,
)
from penguiflow.llm.types import Usage


class TestGetPricing:
    """Tests for get pricing."""

    def test_known_model_exact(self) -> None:
        """Known model exact."""
        input_price, output_price = get_pricing("gpt-4o")
        assert input_price > 0
        assert output_price > 0

    def test_known_model_with_prefix(self) -> None:
        """Known model with prefix."""
        input_price, _output_price = get_pricing("openai/gpt-4o")
        assert input_price > 0

    def test_versioned_model(self) -> None:
        """Versioned model."""
        input_price, _output_price = get_pricing("gpt-4o-2024-11-20")
        assert input_price > 0

    def test_versioned_model_with_provider_prefix(self) -> None:
        """Versioned model ids with a provider prefix are priced."""
        input_price, output_price = get_pricing("openai/gpt-4o-2024-11-20")
        assert input_price > 0
        assert output_price > 0

    def test_unknown_model_returns_zero(self) -> None:
        """Unknown model returns zero."""
        input_price, output_price = get_pricing("unknown-model-xyz")
        assert input_price == 0.0
        assert output_price == 0.0

    def test_claude_model(self) -> None:
        """Claude model."""
        input_price, output_price = get_pricing("claude-3-5-sonnet")
        assert input_price > 0
        assert output_price > 0

    def test_gemini_free_tier(self) -> None:
        # gemini-2.0-flash-exp is the experimental/free tier model
        """Gemini free tier."""
        input_price, output_price = get_pricing("gemini-2.0-flash-exp")
        assert input_price == 0.0
        assert output_price == 0.0


class TestCalculateCost:
    """Tests for calculate cost."""

    def test_calculate_cost_gpt4o(self) -> None:
        """Calculate cost gpt4o."""
        cost = calculate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        assert cost > 0

    def test_calculate_cost_zero_tokens(self) -> None:
        """Calculate cost zero tokens."""
        cost = calculate_cost("gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_calculate_cost_unknown_model(self) -> None:
        """Calculate cost unknown model."""
        cost = calculate_cost(
            "unknown-model", input_tokens=1000, output_tokens=500
        )
        assert cost == 0.0


class TestCalculateCostFromUsage:
    """Tests for calculate cost from usage."""

    def test_from_usage(self) -> None:
        """From usage."""
        usage = Usage(input_tokens=1000, output_tokens=500, total_tokens=1500)
        cost = calculate_cost_from_usage("gpt-4o", usage)
        assert cost.input_cost > 0
        assert cost.output_cost > 0
        assert cost.total_cost == pytest.approx(
            cost.input_cost + cost.output_cost
        )

    def test_zero_usage(self) -> None:
        """Zero usage."""
        usage = Usage.zero()
        cost = calculate_cost_from_usage("gpt-4o", usage)
        assert cost.total_cost == 0.0


class TestRegisterPricing:
    """Tests for register pricing."""

    def test_register_custom_pricing(self) -> None:
        """Register custom pricing."""
        register_pricing("custom-model", 0.001, 0.002)
        input_price, output_price = get_pricing("custom-model")
        assert input_price == 0.001
        assert output_price == 0.002

    def test_override_existing_pricing(self) -> None:
        """Override existing pricing."""
        original_input, original_output = get_pricing("gpt-4o")
        register_pricing("gpt-4o", 0.999, 0.999)
        new_input, _new_output = get_pricing("gpt-4o")
        assert new_input == 0.999
        # Restore original
        register_pricing("gpt-4o", original_input, original_output)
