"""Tests for the LLM routing module."""

from __future__ import annotations

from penguiflow.llm.routing import (
    build_model_string,
    estimate_context_window,
    get_provider_for_model,
    is_reasoning_model,
    is_vision_model,
    normalize_model_id,
    parse_model_string,
)


class TestParseModelString:
    def test_openai_prefix(self) -> None:
        result = parse_model_string("openai/gpt-4o")
        assert result.provider == "openai"
        assert result.model_id == "gpt-4o"
        assert result.original == "openai/gpt-4o"

    def test_nim_prefix(self) -> None:
        result = parse_model_string("nim/qwen/qwen3.5-397b-a17b")
        assert result.provider == "nim"
        assert result.model_id == "qwen/qwen3.5-397b-a17b"

    def test_nvidia_prefix_maps_to_nim(self) -> None:
        result = parse_model_string("nvidia/qwen/qwen3.5-397b-a17b")
        assert result.provider == "nim"
        assert result.model_id == "qwen/qwen3.5-397b-a17b"

    def test_openai_implied(self) -> None:
        result = parse_model_string("gpt-4o")
        assert result.provider == "openai"
        assert result.model_id == "gpt-4o"

    def test_openai_o1(self) -> None:
        result = parse_model_string("o1-preview")
        assert result.provider == "openai"
        assert result.model_id == "o1-preview"

    def test_anthropic_prefix(self) -> None:
        result = parse_model_string("anthropic/claude-3-5-sonnet")
        assert result.provider == "anthropic"
        assert result.model_id == "claude-3-5-sonnet"

    def test_anthropic_implied(self) -> None:
        result = parse_model_string("claude-3-opus")
        assert result.provider == "anthropic"

    def test_google_prefix(self) -> None:
        result = parse_model_string("google/gemini-2.0-flash")
        assert result.provider == "google"
        assert result.model_id == "gemini-2.0-flash"

    def test_google_implied(self) -> None:
        result = parse_model_string("gemini-1.5-pro")
        assert result.provider == "google"

    def test_bedrock_prefix(self) -> None:
        result = parse_model_string("bedrock/anthropic.claude-3-5-sonnet")
        assert result.provider == "bedrock"
        assert result.model_id == "anthropic.claude-3-5-sonnet"

    def test_bedrock_implied(self) -> None:
        result = parse_model_string("anthropic.claude-3-5-sonnet-v2")
        assert result.provider == "bedrock"

    def test_bedrock_amazon(self) -> None:
        result = parse_model_string("amazon.nova-pro")
        assert result.provider == "bedrock"

    def test_bedrock_meta(self) -> None:
        result = parse_model_string("meta.llama3-1-70b-instruct")
        assert result.provider == "bedrock"

    def test_databricks_prefix(self) -> None:
        result = parse_model_string("databricks/databricks-dbrx-instruct")
        assert result.provider == "databricks"
        assert result.model_id == "databricks-dbrx-instruct"

    def test_databricks_implied(self) -> None:
        result = parse_model_string("databricks-mixtral-8x7b-instruct")
        assert result.provider == "databricks"

    def test_openrouter(self) -> None:
        result = parse_model_string("openrouter/anthropic/claude-3-5-sonnet")
        assert result.provider == "openrouter"
        assert result.model_id == "anthropic/claude-3-5-sonnet"
        assert result.sub_provider == "anthropic"

    def test_openrouter_single_part(self) -> None:
        result = parse_model_string("openrouter/mistral-large")
        assert result.provider == "openrouter"
        assert result.model_id == "mistral-large"

    def test_unknown_model(self) -> None:
        result = parse_model_string("some-custom-model")
        assert result.provider == "unknown"
        assert result.model_id == "some-custom-model"


class TestNormalizeModelId:
    def test_strip_prefix(self) -> None:
        assert normalize_model_id("openai/gpt-4o") == "gpt-4o"

    def test_no_prefix(self) -> None:
        assert normalize_model_id("gpt-4o") == "gpt-4o"

    def test_bedrock(self) -> None:
        assert normalize_model_id("bedrock/anthropic.claude-3") == "anthropic.claude-3"


class TestGetProviderForModel:
    def test_get_provider(self) -> None:
        assert get_provider_for_model("gpt-4o") == "openai"
        assert get_provider_for_model("claude-3-5-sonnet") == "anthropic"
        assert get_provider_for_model("gemini-2.0-flash") == "google"
        assert get_provider_for_model("nim/qwen/qwen3.5-397b-a17b") == "nim"
        assert get_provider_for_model("nvidia/qwen/qwen3.5-397b-a17b") == "nim"


class TestIsReasoningModel:
    def test_o1_models(self) -> None:
        assert is_reasoning_model("o1") is True
        assert is_reasoning_model("o1-preview") is True
        assert is_reasoning_model("o1-mini") is True

    def test_o3_models(self) -> None:
        assert is_reasoning_model("o3") is True
        assert is_reasoning_model("o3-mini") is True

    def test_deepseek_reasoner(self) -> None:
        assert is_reasoning_model("deepseek-reasoner") is True
        assert is_reasoning_model("deepseek-r1") is True

    def test_non_reasoning(self) -> None:
        assert is_reasoning_model("gpt-4o") is False
        assert is_reasoning_model("claude-3-5-sonnet") is False


class TestIsVisionModel:
    def test_gpt4o_vision(self) -> None:
        assert is_vision_model("gpt-4o") is True
        assert is_vision_model("gpt-4-turbo") is True

    def test_claude_vision(self) -> None:
        assert is_vision_model("claude-3-opus") is True
        assert is_vision_model("claude-3-5-sonnet") is True

    def test_gemini_vision(self) -> None:
        assert is_vision_model("gemini-1.5-pro") is True
        assert is_vision_model("gemini-2.0-flash") is True

    def test_non_vision(self) -> None:
        # This is a best-effort heuristic, so testing known non-vision models
        assert is_vision_model("gpt-3.5-turbo") is False


class TestEstimateContextWindow:
    def test_gpt4o_context(self) -> None:
        assert estimate_context_window("gpt-4o") == 128000

    def test_claude_context(self) -> None:
        assert estimate_context_window("claude-3-5-sonnet") == 200000

    def test_gemini_context(self) -> None:
        assert estimate_context_window("gemini-1.5-pro") == 2000000

    def test_unknown_model_default(self) -> None:
        assert estimate_context_window("unknown-model") == 8192


class TestBuildModelString:
    def test_build_openai(self) -> None:
        result = build_model_string("openai", "gpt-4o")
        assert result == "openai/gpt-4o"

    def test_build_nim(self) -> None:
        result = build_model_string("nim", "qwen/qwen3.5-397b-a17b")
        assert result == "nim/qwen/qwen3.5-397b-a17b"

    def test_build_openrouter(self) -> None:
        result = build_model_string("openrouter", "claude-3-5-sonnet", sub_provider="anthropic")
        assert result == "openrouter/anthropic/claude-3-5-sonnet"

    def test_build_openrouter_no_sub_provider(self) -> None:
        """Test building openrouter model string without sub_provider."""
        result = build_model_string("openrouter", "mistral-large")
        assert result == "openrouter/mistral-large"

    def test_build_unknown(self) -> None:
        result = build_model_string("unknown", "custom-model")
        assert result == "custom-model"
