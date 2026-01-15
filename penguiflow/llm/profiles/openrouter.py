"""OpenRouter model profiles.

Defines capabilities and configuration for OpenRouter routing.
OpenRouter provides access to 500+ models from multiple providers.

Top-tier models available (January 2026):
- OpenAI: GPT-5, GPT-5.1, GPT-5.2 Pro (up to 400K context)
- Anthropic: Claude Opus 4.5, Claude Sonnet 4.5 (up to 1M context)
- Google: Gemini 3 Pro Preview, Gemini 2.5 Pro/Flash (up to 1M context)
- DeepSeek: R1 (671B), V3.x (up to 163K context, reasoning support)
- Meta: Llama 4 Maverick/Scout, Llama 3.3-70B (up to 131K context)
- Qwen: Qwen3, Qwen3-VL (up to 262K context)
- Mistral: Large, Codestral, DevStral

Model variant suffixes:
- :free - Free tier with rate limits
- :extended - Extended context window
- :thinking - Reasoning-enabled mode
- :online - Web search enabled
- :nitro - Low-latency inference
- :exacto - High-precision structured outputs
"""

from __future__ import annotations

from . import ModelProfile

# Provider prefix to profile mapping for OpenRouter
PROVIDER_PROFILE_MAPPING = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "mistralai": "mistral",
    "meta-llama": "llama",
    "deepseek": "deepseek",
    "cohere": "cohere",
    "perplexity": "perplexity",
    "qwen": "qwen",
}

# Profiles for OpenRouter-specific models
PROFILES: dict[str, ModelProfile] = {
    # OpenRouter-specific routing (uses underlying provider profiles)
    # These are fallback profiles when we can't determine the underlying provider
    "openrouter/auto": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
    ),
    # OpenAI GPT-5 series via OpenRouter (January 2026)
    "openai/gpt-5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        strict_mode_default=True,
        max_context_tokens=400000,
        max_output_tokens=16384,
    ),
    "openai/gpt-5.1": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        strict_mode_default=True,
        max_context_tokens=400000,
        max_output_tokens=16384,
    ),
    "openai/gpt-5.2-pro": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        strict_mode_default=True,
        max_context_tokens=400000,
        max_output_tokens=32768,
    ),
    # Anthropic Claude 4.x via OpenRouter (January 2026)
    "anthropic/claude-sonnet-4.5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=8192,
    ),
    "anthropic/claude-opus-4.5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=8192,
    ),
    "anthropic/claude-3.5-haiku": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=8192,
    ),
    # Google Gemini via OpenRouter (January 2026)
    "google/gemini-3-pro-preview": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=8192,
    ),
    "google/gemini-2.5-pro": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=8192,
    ),
    "google/gemini-2.5-flash": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=8192,
    ),
    # DeepSeek models via OpenRouter (R1 is 671B parameter model)
    "deepseek/deepseek-r1": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        thinking_tags=("<think>", "</think>"),
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    "deepseek/deepseek-r1-0528": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        thinking_tags=("<think>", "</think>"),
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    "deepseek/deepseek-v3": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    "deepseek/deepseek-v3.1": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # V3.1 can act like R1 for reasoning
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        thinking_tags=("<think>", "</think>"),
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    "deepseek/deepseek-v3.2": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        thinking_tags=("<think>", "</think>"),
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    "deepseek/deepseek-chat": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    "deepseek/deepseek-coder": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=163000,
        max_output_tokens=8192,
    ),
    # Mistral via OpenRouter
    "mistralai/mistral-large": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    "mistralai/mistral-medium": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=32000,
        max_output_tokens=8192,
    ),
    "mistralai/mistral-small": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=32000,
        max_output_tokens=8192,
    ),
    "mistralai/mixtral-8x7b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=32000,
        max_output_tokens=4096,
    ),
    # Meta Llama via OpenRouter
    "meta-llama/llama-4-maverick": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "meta-llama/llama-4-scout": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "meta-llama/llama-3.3-70b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "meta-llama/llama-3.1-405b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "meta-llama/llama-3.1-70b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "meta-llama/llama-3.1-8b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    # Qwen3 via OpenRouter (January 2026)
    "qwen/qwen3-235b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=262000,
        max_output_tokens=8192,
    ),
    "qwen/qwen3-30b-a3b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=262000,
        max_output_tokens=8192,
    ),
    "qwen/qwen3-vl-72b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    "qwen/qwen-2.5-72b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    "qwen/qwen-2.5-coder-32b-instruct": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    # Cohere via OpenRouter
    "cohere/command-r-plus": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "cohere/command-r": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    # Perplexity via OpenRouter
    "perplexity/sonar-pro": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=False,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="prompted",
        native_structured_kind="unknown",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    "perplexity/sonar": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=False,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="prompted",
        native_structured_kind="unknown",
        strict_mode_default=False,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    "perplexity/sonar-small-chat": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=False,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="prompted",
        native_structured_kind="unknown",
        strict_mode_default=False,
        max_context_tokens=16384,
        max_output_tokens=4096,
    ),
    "perplexity/sonar-medium-chat": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=False,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="prompted",
        native_structured_kind="unknown",
        strict_mode_default=False,
        max_context_tokens=16384,
        max_output_tokens=4096,
    ),
}


def get_openrouter_profile(model: str) -> ModelProfile:
    """Get the profile for an OpenRouter model.

    Parses the model string to determine the underlying provider and
    returns the appropriate profile. Handles model variant suffixes
    like :free, :extended, :thinking, :online, :nitro, :exacto.

    Args:
        model: The OpenRouter model string (e.g., "openrouter/anthropic/claude-sonnet-4.5",
               "openai/gpt-5:thinking", "deepseek/deepseek-r1:free").

    Returns:
        The appropriate ModelProfile for the model.
    """
    # Check for direct match first
    if model in PROFILES:
        return PROFILES[model]

    # Strip "openrouter/" prefix if present
    if model.startswith("openrouter/"):
        model = model.removeprefix("openrouter/")

    # Check for provider-specific match
    parts = model.split("/")
    if len(parts) >= 2:
        provider = parts[0]
        full_model = "/".join(parts)

        # Check if we have a specific profile for this model
        if full_model in PROFILES:
            return PROFILES[full_model]

        # Otherwise, try to get the provider's default profile
        if provider in PROVIDER_PROFILE_MAPPING:
            # Import the appropriate profile module
            profile_type = PROVIDER_PROFILE_MAPPING[provider]

            if profile_type == "openai":
                from . import openai

                # Try to match the model name in OpenAI profiles
                model_name = parts[-1] if len(parts) > 1 else model
                for key, profile in openai.PROFILES.items():
                    if model_name.startswith(key) or key in model_name:
                        return profile

            elif profile_type == "anthropic":
                from . import anthropic

                model_name = parts[-1] if len(parts) > 1 else model
                for key, profile in anthropic.PROFILES.items():
                    if model_name.startswith(key) or key in model_name:
                        return profile

            elif profile_type == "google":
                from . import google

                model_name = parts[-1] if len(parts) > 1 else model
                for key, profile in google.PROFILES.items():
                    if model_name.startswith(key) or key in model_name:
                        return profile

    # Default OpenRouter profile
    return ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
    )
