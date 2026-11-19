"""Provider implementations for the LLM layer.

Exports provider classes and the factory function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import OpenAICompatibleProvider, Provider

if TYPE_CHECKING:
    from collections.abc import Callable

_DATABRICKS_AUTH_KWARGS = frozenset(
    {
        "token",
        "api_key",
        "token_provider",
        "client_id",
        "client_secret",
    }
)

__all__ = [
    "AnthropicProvider",
    "BedrockProvider",
    "DatabricksProvider",
    "GoogleProvider",
    "NIMProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "Provider",
    "create_provider",
]


def _build(
    provider_cls: Callable[..., Provider], model: str, **kwargs: object
) -> Provider:
    """Instantiate ``provider_cls``; it validates the forwarded options."""
    return provider_cls(model, **kwargs)


def create_provider(
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: object,
) -> Provider:
    """Create a provider instance based on model string.

    Model string formats:
    - "openai/gpt-4o" or "gpt-4o" -> OpenAI
    - "nim/qwen/qwen3.5-397b-a17b", "nvidia/qwen/qwen3.5-397b-a17b", or
      "nvidia/nemotron-3-nano-30b-a3b" -> NIM
    - "anthropic/claude-3-5-sonnet" or "claude-*" -> Anthropic
    - "google/gemini-2.0-flash" or "gemini-*" -> Google
    - "bedrock/anthropic.claude-3-5-sonnet" or "anthropic.*" -> Bedrock
    - "databricks/databricks-dbrx-instruct" or "databricks-*" -> Databricks
    - "openrouter/anthropic/claude-3-5-sonnet" -> OpenRouter

    Args:
        model: Model identifier with optional provider prefix.
        api_key: API key (uses environment variable if not provided).
        base_url: Base URL override.
        **kwargs: Provider-specific configuration, validated by the
            provider's constructor.

    Returns:
        Configured provider instance.
    """
    # Lazy imports to avoid loading all SDKs
    from .anthropic import AnthropicProvider
    from .bedrock import BedrockProvider
    from .databricks import DatabricksProvider
    from .google import GoogleProvider
    from .nim import NIMProvider
    from .openai import OpenAIProvider
    from .openrouter import OpenRouterProvider

    # OpenRouter (must check first as it contains other provider names)
    if model.startswith("openrouter/"):
        return _build(OpenRouterProvider, model, api_key=api_key, **kwargs)
    if model.startswith(("databricks/", "databricks-")):
        if api_key is not None and not _has_databricks_auth(kwargs):
            kwargs["api_key"] = api_key
        return _build(
            DatabricksProvider, model.removeprefix("databricks/"), **kwargs
        )
    if model.startswith(("bedrock/", "anthropic.", "amazon.", "meta.")):
        return _build(
            BedrockProvider, model.removeprefix("bedrock/"), **kwargs
        )
    if model.startswith(("openai/", "gpt", "o1", "o3")):
        return _build(
            OpenAIProvider,
            model.removeprefix("openai/"),
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
    if model.startswith(("anthropic/", "claude")):
        return _build(
            AnthropicProvider,
            model.removeprefix("anthropic/"),
            api_key=api_key,
            **kwargs,
        )
    if model.startswith(("google/", "gemini")):
        return _build(
            GoogleProvider,
            model.removeprefix("google/"),
            api_key=api_key,
            **kwargs,
        )
    if model.startswith(("nim/", "nvidia/")):
        return _build(
            NIMProvider,
            _nim_model_name(model),
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )

    # Default: OpenAI-compatible (requires base_url for non-OpenAI servers)
    return _build(
        OpenAIProvider, model, api_key=api_key, base_url=base_url, **kwargs
    )


def _has_databricks_auth(kwargs: dict[str, object]) -> bool:
    """Return whether ``kwargs`` already carries Databricks credentials."""
    return any(key in kwargs for key in _DATABRICKS_AUTH_KWARGS)


def _nim_model_name(model: str) -> str:
    """Strip the NIM provider prefix from ``model``.

    Legacy aliases look like ``nvidia/<vendor>/<model>`` and drop the
    provider prefix; single-segment ids (``nvidia/<model>``) are model names.
    """
    if model.startswith("nim/"):
        return model.removeprefix("nim/")
    remainder = model.removeprefix("nvidia/")
    return remainder if "/" in remainder else model


def __getattr__(name: str) -> type:
    """Lazily import provider classes so SDKs load only when used."""
    if name == "OpenAIProvider":
        from .openai import OpenAIProvider

        return OpenAIProvider
    if name == "AnthropicProvider":
        from .anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "GoogleProvider":
        from .google import GoogleProvider

        return GoogleProvider
    if name == "NIMProvider":
        from .nim import NIMProvider

        return NIMProvider
    if name == "BedrockProvider":
        from .bedrock import BedrockProvider

        return BedrockProvider
    if name == "DatabricksProvider":
        from .databricks import DatabricksProvider

        return DatabricksProvider
    if name == "OpenRouterProvider":
        from .openrouter import OpenRouterProvider

        return OpenRouterProvider
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
