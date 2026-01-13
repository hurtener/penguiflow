"""Tests for the LLM profiles module."""

from __future__ import annotations

from penguiflow.llm.profiles import (
    ModelProfile,
    get_profile,
    get_profiles,
    get_schema_transformer,
    register_profile,
)


class TestGetProfileForModel:
    """Test get_profile function."""

    def test_exact_match(self) -> None:
        """Test exact model name match."""
        profile = get_profile("gpt-4o")
        assert profile is not None
        assert profile.supports_tools is True

    def test_provider_prefix_stripped(self) -> None:
        """Test that provider prefix is stripped for matching."""
        profile = get_profile("openai/gpt-4o")
        assert profile is not None
        assert profile.supports_tools is True

    def test_versioned_model_prefix_match(self) -> None:
        """Test that versioned models match base model."""
        profile = get_profile("gpt-4o-2024-08-06")
        assert profile is not None
        assert profile.supports_tools is True

    def test_versioned_model_with_provider_prefix(self) -> None:
        """Test versioned model with provider prefix."""
        profile = get_profile("openai/gpt-4o-2024-08-06")
        assert profile is not None
        assert profile.supports_tools is True

    def test_unknown_model_returns_default(self) -> None:
        """Test that unknown models return default profile."""
        profile = get_profile("unknown-model-xyz")
        assert profile is not None
        # Default profile has minimal capabilities
        assert isinstance(profile, ModelProfile)

    def test_anthropic_model(self) -> None:
        """Test Anthropic model profile."""
        profile = get_profile("claude-3-5-sonnet")
        assert profile is not None
        assert profile.supports_tools is True

    def test_google_model(self) -> None:
        """Test Google model profile."""
        profile = get_profile("gemini-2.0-flash")
        assert profile is not None


class TestRegisterProfile:
    """Test register_profile function."""

    def test_register_custom_profile(self) -> None:
        """Test registering a custom profile."""
        custom_profile = ModelProfile(
            supports_tools=False,
            supports_schema_guided_output=False,
            max_output_tokens=1000,
        )
        register_profile("my-custom-model", custom_profile)

        # Verify the profile is registered
        profile = get_profile("my-custom-model")
        assert profile is custom_profile
        assert profile.max_output_tokens == 1000

        # Clean up by removing from profiles dict
        profiles = get_profiles()
        del profiles["my-custom-model"]


class TestGetSchemaTransformer:
    """Test get_schema_transformer function."""

    def test_no_transformer_needed(self) -> None:
        """Test profile with no schema transformer."""
        profile = ModelProfile()  # Default has no transformer
        result = get_schema_transformer(profile, {"type": "object"})
        assert result is None

    def test_openai_transformer(self) -> None:
        """Test getting OpenAI schema transformer."""
        profile = ModelProfile(schema_transformer_name="OpenAIJsonSchemaTransformer")
        transformer = get_schema_transformer(profile, {"type": "object"})
        assert transformer is not None

    def test_anthropic_transformer(self) -> None:
        """Test getting Anthropic schema transformer."""
        profile = ModelProfile(schema_transformer_name="AnthropicJsonSchemaTransformer")
        transformer = get_schema_transformer(profile, {"type": "object"})
        assert transformer is not None

    def test_google_transformer(self) -> None:
        """Test getting Google schema transformer."""
        profile = ModelProfile(schema_transformer_name="GoogleJsonSchemaTransformer")
        transformer = get_schema_transformer(profile, {"type": "object"})
        assert transformer is not None

    def test_bedrock_transformer(self) -> None:
        """Test getting Bedrock schema transformer."""
        profile = ModelProfile(schema_transformer_name="BedrockJsonSchemaTransformer")
        transformer = get_schema_transformer(profile, {"type": "object"})
        assert transformer is not None

    def test_databricks_transformer(self) -> None:
        """Test getting Databricks schema transformer."""
        profile = ModelProfile(schema_transformer_name="DatabricksJsonSchemaTransformer")
        transformer = get_schema_transformer(profile, {"type": "object"})
        assert transformer is not None

    def test_unknown_transformer_returns_none(self) -> None:
        """Test that unknown transformer name returns None."""
        profile = ModelProfile(schema_transformer_name="UnknownTransformer")
        result = get_schema_transformer(profile, {"type": "object"})
        assert result is None
