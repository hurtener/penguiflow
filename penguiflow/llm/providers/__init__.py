"""Provider implementations for the LLM layer.

Exports provider classes and the factory function.
"""

from __future__ import annotations

from typing import Any

from .base import OpenAICompatibleProvider, Provider

__all__ = [
    "Provider",
    "OpenAICompatibleProvider",
    "create_provider",
    "OpenAIProvider",
    "AnthropicProvider",
    "GoogleProvider",
    "BedrockProvider",
    "DatabricksProvider",
    "OpenRouterProvider",
]


def create_provider(
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> Provider:
    """Create a provider instance based on model string.

    Model string formats:
    - "openai/gpt-4o" or "gpt-4o" -> OpenAI
    - "anthropic/claude-3-5-sonnet" or "claude-*" -> Anthropic
    - "google/gemini-2.0-flash" or "gemini-*" -> Google
    - "bedrock/anthropic.claude-3-5-sonnet" or "anthropic.*" -> Bedrock
    - "databricks/databricks-dbrx-instruct" or "databricks-*" -> Databricks
    - "openrouter/anthropic/claude-3-5-sonnet" -> OpenRouter

    Args:
        model: Model identifier with optional provider prefix.
        api_key: API key (uses environment variable if not provided).
        base_url: Base URL override.
        **kwargs: Provider-specific configuration.

    Returns:
        Configured provider instance.
    """
    # Lazy imports to avoid loading all SDKs
    from .anthropic import AnthropicProvider
    from .bedrock import BedrockProvider
    from .databricks import DatabricksProvider
    from .google import GoogleProvider
    from .openai import OpenAIProvider
    from .openrouter import OpenRouterProvider

    # OpenRouter (must check first as it contains other provider names)
    if model.startswith("openrouter/"):
        return OpenRouterProvider(model, api_key=api_key, **kwargs)

    # Databricks
    if model.startswith("databricks/"):
        return DatabricksProvider(model.removeprefix("databricks/"), **kwargs)
    if model.startswith("databricks-"):
        return DatabricksProvider(model, **kwargs)

    # Bedrock
    if model.startswith("bedrock/"):
        return BedrockProvider(model.removeprefix("bedrock/"), **kwargs)
    if model.startswith(("anthropic.", "amazon.", "meta.")):
        return BedrockProvider(model, **kwargs)

    # OpenAI
    if model.startswith("openai/"):
        return OpenAIProvider(
            model.removeprefix("openai/"), api_key=api_key, base_url=base_url, **kwargs
        )
    if model.startswith(("gpt", "o1", "o3")):
        return OpenAIProvider(model, api_key=api_key, base_url=base_url, **kwargs)

    # Anthropic
    if model.startswith("anthropic/"):
        return AnthropicProvider(
            model.removeprefix("anthropic/"), api_key=api_key, **kwargs
        )
    if model.startswith("claude"):
        return AnthropicProvider(model, api_key=api_key, **kwargs)

    # Google
    if model.startswith("google/"):
        return GoogleProvider(model.removeprefix("google/"), api_key=api_key, **kwargs)
    if model.startswith("gemini"):
        return GoogleProvider(model, api_key=api_key, **kwargs)

    # Default: OpenAI-compatible (requires base_url for non-OpenAI servers)
    return OpenAIProvider(model, api_key=api_key, base_url=base_url, **kwargs)


def __getattr__(name: str) -> type:
    """Lazy import provider classes to avoid loading all SDKs at import time."""
    if name == "OpenAIProvider":
        from .openai import OpenAIProvider
        return OpenAIProvider
    elif name == "AnthropicProvider":
        from .anthropic import AnthropicProvider
        return AnthropicProvider
    elif name == "GoogleProvider":
        from .google import GoogleProvider
        return GoogleProvider
    elif name == "BedrockProvider":
        from .bedrock import BedrockProvider
        return BedrockProvider
    elif name == "DatabricksProvider":
        from .databricks import DatabricksProvider
        return DatabricksProvider
    elif name == "OpenRouterProvider":
        from .openrouter import OpenRouterProvider
        return OpenRouterProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
