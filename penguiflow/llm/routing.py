"""Model routing and string parsing utilities for the LLM layer.

Provides functions to parse model strings and route to appropriate providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Provider type literals
ProviderType = Literal[
    "openai",
    "anthropic",
    "google",
    "bedrock",
    "databricks",
    "openrouter",
    "unknown",
]


@dataclass(frozen=True)
class ParsedModel:
    """Result of parsing a model string."""

    provider: ProviderType
    model_id: str
    original: str
    sub_provider: str | None = None  # For OpenRouter: the underlying provider


def parse_model_string(model: str) -> ParsedModel:
    """Parse a model string into provider and model components.

    Supported formats:
    - "gpt-4o" -> (openai, gpt-4o)
    - "openai/gpt-4o" -> (openai, gpt-4o)
    - "claude-3-5-sonnet" -> (anthropic, claude-3-5-sonnet)
    - "anthropic/claude-3-5-sonnet" -> (anthropic, claude-3-5-sonnet)
    - "gemini-2.0-flash" -> (google, gemini-2.0-flash)
    - "google/gemini-2.0-flash" -> (google, gemini-2.0-flash)
    - "anthropic.claude-3-5-sonnet-v2" -> (bedrock, anthropic.claude-3-5-sonnet-v2)
    - "bedrock/anthropic.claude-3-5-sonnet" -> (bedrock, anthropic.claude-3-5-sonnet)
    - "databricks-dbrx-instruct" -> (databricks, databricks-dbrx-instruct)
    - "databricks/databricks-dbrx-instruct" -> (databricks, databricks-dbrx-instruct)
    - "openrouter/anthropic/claude-3-5-sonnet" -> (openrouter, anthropic/claude-3-5-sonnet, anthropic)

    Args:
        model: Model identifier string.

    Returns:
        ParsedModel with provider and model_id.
    """
    original = model

    # OpenRouter (check first as it contains other provider names)
    if model.startswith("openrouter/"):
        rest = model.removeprefix("openrouter/")
        # Try to extract sub-provider
        if "/" in rest:
            sub_provider = rest.split("/", 1)[0]
            return ParsedModel(
                provider="openrouter",
                model_id=rest,
                original=original,
                sub_provider=sub_provider,
            )
        return ParsedModel(
            provider="openrouter",
            model_id=rest,
            original=original,
        )

    # Explicit provider prefix
    if "/" in model:
        provider_prefix, model_id = model.split("/", 1)
        provider_prefix = provider_prefix.lower()

        if provider_prefix == "openai":
            return ParsedModel(provider="openai", model_id=model_id, original=original)
        elif provider_prefix == "anthropic":
            return ParsedModel(provider="anthropic", model_id=model_id, original=original)
        elif provider_prefix == "google":
            return ParsedModel(provider="google", model_id=model_id, original=original)
        elif provider_prefix == "bedrock":
            return ParsedModel(provider="bedrock", model_id=model_id, original=original)
        elif provider_prefix == "databricks":
            return ParsedModel(provider="databricks", model_id=model_id, original=original)

    # Bedrock inference profile format (anthropic.*, amazon.*, meta.*)
    if model.startswith(("anthropic.", "amazon.", "meta.")):
        return ParsedModel(provider="bedrock", model_id=model, original=original)

    # OpenAI models
    if model.startswith(("gpt", "o1", "o3")):
        return ParsedModel(provider="openai", model_id=model, original=original)

    # Anthropic models
    if model.startswith("claude"):
        return ParsedModel(provider="anthropic", model_id=model, original=original)

    # Google models
    if model.startswith("gemini"):
        return ParsedModel(provider="google", model_id=model, original=original)

    # Databricks models
    if model.startswith("databricks-"):
        return ParsedModel(provider="databricks", model_id=model, original=original)

    # Unknown provider - could be OpenAI-compatible
    return ParsedModel(provider="unknown", model_id=model, original=original)


def normalize_model_id(model: str) -> str:
    """Normalize a model ID by removing provider prefix.

    Args:
        model: Model identifier string.

    Returns:
        Model ID without provider prefix.
    """
    parsed = parse_model_string(model)
    return parsed.model_id


def get_provider_for_model(model: str) -> ProviderType:
    """Get the provider type for a model string.

    Args:
        model: Model identifier string.

    Returns:
        Provider type string.
    """
    parsed = parse_model_string(model)
    return parsed.provider


def is_reasoning_model(model: str) -> bool:
    """Check if a model supports native reasoning/thinking.

    Args:
        model: Model identifier string.

    Returns:
        True if the model supports native reasoning.
    """
    lower = model.lower()
    reasoning_indicators = (
        "o1",  # OpenAI o1 family
        "o3",  # OpenAI o3 family
        "deepseek-reasoner",
        "deepseek-r1",
        "thinking",
        "reasoning",
    )
    return any(indicator in lower for indicator in reasoning_indicators)


def is_vision_model(model: str) -> bool:
    """Check if a model supports vision/image input.

    Args:
        model: Model identifier string.

    Returns:
        True if the model supports vision.
    """
    lower = model.lower()

    # Models with known vision support
    vision_models = (
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-4-vision",
        "claude-3",
        "claude-sonnet-4",
        "claude-opus-4",
        "gemini-1.5",
        "gemini-2.0",
    )

    return any(vm in lower for vm in vision_models)


def estimate_context_window(model: str) -> int:
    """Estimate the context window size for a model.

    Args:
        model: Model identifier string.

    Returns:
        Estimated context window in tokens.
    """
    lower = model.lower()

    # Known context windows
    context_windows = {
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "o1": 200000,
        "o1-preview": 128000,
        "o1-mini": 128000,
        "o3": 200000,
        "o3-mini": 200000,
        "claude-3-5-sonnet": 200000,
        "claude-3-5-haiku": 200000,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
        "claude-opus-4": 200000,
        "claude-sonnet-4": 200000,
        "gemini-2.0-flash": 1000000,
        "gemini-1.5-pro": 2000000,
        "gemini-1.5-flash": 1000000,
    }

    for model_prefix, window in context_windows.items():
        if model_prefix in lower:
            return window

    # Default for unknown models
    return 8192


def build_model_string(
    provider: ProviderType,
    model_id: str,
    *,
    sub_provider: str | None = None,
) -> str:
    """Build a full model string from components.

    Args:
        provider: Provider type.
        model_id: Model identifier.
        sub_provider: Sub-provider for OpenRouter.

    Returns:
        Full model string.
    """
    if provider == "openrouter":
        if sub_provider:
            return f"openrouter/{sub_provider}/{model_id}"
        return f"openrouter/{model_id}"

    if provider == "unknown":
        return model_id

    return f"{provider}/{model_id}"


__all__ = [
    "ParsedModel",
    "ProviderType",
    "parse_model_string",
    "normalize_model_id",
    "get_provider_for_model",
    "is_reasoning_model",
    "is_vision_model",
    "estimate_context_window",
    "build_model_string",
]
