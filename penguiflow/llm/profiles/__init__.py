"""Model profiles for the LLM layer.

Declarative capability descriptions per model, adapted from PydanticAI patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..schema.transformer import JsonSchemaTransformer


@dataclass
class ModelProfile:
    """Describes capabilities and configuration for a specific model.

    This dataclass captures provider-specific capabilities and quirks,
    allowing the LLM layer to make informed decisions about:
    - Which structured output mode to use
    - How to transform schemas for compatibility
    - What parameters are supported
    """

    # Output capabilities
    supports_schema_guided_output: bool = False  # Provider-native schema-guided structured output
    supports_json_only_output: bool = True  # Provider-native "JSON only" mode (if supported)
    supports_tools: bool = True  # Tool/function calling
    supports_reasoning: bool = False  # Native reasoning (o1, o3, deepseek-r1)
    supports_streaming: bool = True  # Streaming responses

    # Output mode selection
    default_output_mode: Literal["native", "tools", "prompted"] = "native"

    # Provider-native structured output mechanism (used by OutputMode.NATIVE)
    native_structured_kind: Literal[
        "openai_response_format",
        "databricks_constrained_decoding",
        "anthropic_tool_use",
        "google_response_schema",
        "bedrock_tool_use",
        "openai_compatible_tools",
        "unknown",
    ] = "unknown"

    # Schema transformation class name (string to avoid circular imports)
    schema_transformer_name: str | None = None

    # Reasoning configuration
    reasoning_effort_param: str | None = None  # Parameter name if supported
    thinking_tags: tuple[str, str] | None = None  # e.g., ("<think>", "</think>")

    # Provider quirks
    strict_mode_default: bool = True  # Default for strict JSON schema
    supports_system_role: bool = True  # Some models need user role for system
    drop_unsupported_params: bool = True  # Silently drop unknown params
    max_tools: int | None = None  # Maximum number of tools allowed
    max_schema_keys: int | None = None  # Maximum schema keys (e.g., Databricks: 64)

    # Token limits
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None


# ---------------------------------------------------------------------------
# Profile Registry
# ---------------------------------------------------------------------------

# Import profiles from submodules at runtime to avoid circular imports
_PROFILES: dict[str, ModelProfile] | None = None


def _load_profiles() -> dict[str, ModelProfile]:
    """Load all profiles from submodules."""
    from . import anthropic, bedrock, databricks, google, openai, openrouter

    profiles: dict[str, ModelProfile] = {}
    profiles.update(openai.PROFILES)
    profiles.update(anthropic.PROFILES)
    profiles.update(google.PROFILES)
    profiles.update(bedrock.PROFILES)
    profiles.update(databricks.PROFILES)
    profiles.update(openrouter.PROFILES)
    return profiles


def get_profiles() -> dict[str, ModelProfile]:
    """Get all registered profiles."""
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _load_profiles()
    return _PROFILES


def get_profile(model: str) -> ModelProfile:
    """Get profile for a model, with fallback to defaults.

    Args:
        model: The model identifier (e.g., "gpt-4o", "claude-3-5-sonnet").

    Returns:
        The model profile, or a default profile if not found.
    """
    profiles = get_profiles()

    # Exact match
    if model in profiles:
        return profiles[model]

    # Strip provider prefix (e.g., "openai/gpt-4o" -> "gpt-4o")
    if "/" in model:
        stripped = model.split("/", 1)[-1]
        if stripped in profiles:
            return profiles[stripped]

    # Prefix matching for versioned models (e.g., "gpt-4o-2024-08-06" -> "gpt-4o")
    for key, profile in profiles.items():
        if model.startswith(key):
            return profile
        # Also try with stripped prefix
        if "/" in model:
            stripped = model.split("/", 1)[-1]
            if stripped.startswith(key):
                return profile

    # Default fallback
    return ModelProfile()


def register_profile(model: str, profile: ModelProfile) -> None:
    """Register a custom profile for a model.

    Args:
        model: The model identifier.
        profile: The model profile.
    """
    profiles = get_profiles()
    profiles[model] = profile


# ---------------------------------------------------------------------------
# Schema Transformer Factory
# ---------------------------------------------------------------------------


def get_schema_transformer(
    profile: ModelProfile,
    schema: dict[str, Any],
    *,
    strict: bool = True,
) -> JsonSchemaTransformer | None:
    """Get the schema transformer for a profile.

    Args:
        profile: The model profile.
        schema: The JSON schema to transform.
        strict: Whether to use strict mode.

    Returns:
        The appropriate schema transformer, or None if no transformation needed.
    """
    if not profile.schema_transformer_name:
        return None

    # Import transformer classes dynamically
    from ..schema import anthropic as anthropic_schema
    from ..schema import bedrock as bedrock_schema
    from ..schema import databricks as databricks_schema
    from ..schema import google as google_schema
    from ..schema import openai as openai_schema

    transformer_map = {
        "OpenAIJsonSchemaTransformer": openai_schema.OpenAIJsonSchemaTransformer,
        "AnthropicJsonSchemaTransformer": anthropic_schema.AnthropicJsonSchemaTransformer,
        "GoogleJsonSchemaTransformer": google_schema.GoogleJsonSchemaTransformer,
        "BedrockJsonSchemaTransformer": bedrock_schema.BedrockJsonSchemaTransformer,
        "DatabricksJsonSchemaTransformer": databricks_schema.DatabricksJsonSchemaTransformer,
    }

    transformer_cls = transformer_map.get(profile.schema_transformer_name)
    if transformer_cls:
        return transformer_cls(schema, strict=strict)  # type: ignore[abstract]

    return None
