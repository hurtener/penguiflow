"""Additional pricing tests to cover normalization paths."""

from __future__ import annotations

from penguiflow.llm.pricing import get_pricing


def test_get_pricing_normalizes_dotted_model_names_with_prefix() -> None:
    # Dotted version should normalize to the hyphenated key.
    assert get_pricing("anthropic/claude-sonnet-4.5") == (0.003, 0.015)


def test_get_pricing_prefix_match_with_normalization() -> None:
    # Version-suffixed model should still match via normalized prefix.
    assert get_pricing("claude-sonnet-4.5-2026-01-01") == (0.003, 0.015)


def test_get_pricing_prefix_match_with_provider_prefix_and_normalization() -> None:
    # Also cover the branch that normalizes the stripped name inside the prefix loop.
    assert get_pricing("anthropic/claude-sonnet-4.5-2026-01-01") == (0.003, 0.015)
