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


def test_get_pricing_openrouter_xai_grok_4_1_fast() -> None:
    assert get_pricing("openrouter/x-ai/grok-4.1-fast") == (0.0002, 0.0005)


def test_get_pricing_openrouter_qwen3_5_397b() -> None:
    assert get_pricing("openrouter/qwen/qwen3.5-397b-a17b") == (0.00055, 0.0035)


def test_get_pricing_openai_gpt_5_2_codex() -> None:
    assert get_pricing("openai/gpt-5.2-codex") == (0.00175, 0.014)


def test_get_pricing_openai_gpt_5_1_codex_mini() -> None:
    assert get_pricing("openai/gpt-5.1-codex-mini") == (0.00025, 0.002)


def test_get_pricing_google_gemini_3_1_pro_preview() -> None:
    assert get_pricing("google/gemini-3.1-pro-preview") == (0.002, 0.012)


def test_get_pricing_claude_sonnet_4_6() -> None:
    assert get_pricing("anthropic/claude-sonnet-4.6") == (0.003, 0.015)


def test_get_pricing_openrouter_inception_mercury_2() -> None:
    assert get_pricing("openrouter/inception/mercury-2") == (0.00025, 0.00075)


def test_get_pricing_openrouter_nested_provider_model_gpt_5_3_chat() -> None:
    assert get_pricing("openrouter/openai/gpt-5.3-chat") == (0.00175, 0.014)


def test_get_pricing_openrouter_minimax_m2_7() -> None:
    assert get_pricing("openrouter/minimax/minimax-m2.7") == (0.0003, 0.0012)


def test_get_pricing_openrouter_xiaomi_mimo_v2_pro() -> None:
    assert get_pricing("openrouter/xiaomi/mimo-v2-pro") == (0.001, 0.003)


def test_get_pricing_openrouter_xiaomi_mimo_v2_omni() -> None:
    assert get_pricing("openrouter/xiaomi/mimo-v2-omni") == (0.0004, 0.002)


def test_get_pricing_openrouter_nvidia_nemotron_3_super_120b_a12b() -> None:
    assert get_pricing("openrouter/nvidia/nemotron-3-super-120b-a12b") == (0.0001, 0.0005)


def test_get_pricing_openrouter_mistral_small_2603() -> None:
    assert get_pricing("openrouter/mistralai/mistral-small-2603") == (0.00015, 0.0006)
