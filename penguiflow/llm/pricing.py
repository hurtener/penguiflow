"""Pricing information and cost calculation for the LLM layer.

Maintains a pricing table and calculates cost from normalized Usage.
"""

from __future__ import annotations

import importlib
import logging
from decimal import Decimal
from functools import cache
from typing import TYPE_CHECKING, Any

from .types import Cost, Usage

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger("penguiflow.llm.pricing")

# Pricing per 1K tokens: (input_price, output_price) in USD
# Updated March 2026
# OpenRouter prices sourced from https://openrouter.ai/api/v1/models on 2026-03-03.
PRICING: dict[str, tuple[float, float]] = {
    # OpenAI - GPT-5.x Series (OpenRouter/OpenAI catalog, March 2026)
    "gpt-5": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5-chat": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5-pro": (0.015, 0.12),  # $15/$120 per 1M tokens
    "gpt-5-mini": (0.00025, 0.002),  # $0.25/$2 per 1M tokens
    "gpt-5-nano": (0.00005, 0.0004),  # $0.05/$0.40 per 1M tokens
    "gpt-5-image": (0.01, 0.01),  # $10/$10 per 1M tokens
    "gpt-5-image-mini": (0.0025, 0.002),  # $2.50/$2 per 1M tokens
    "gpt-5-codex": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5.1": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5.1-chat": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5.1-codex": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5.1-codex-max": (0.00125, 0.01),  # $1.25/$10 per 1M tokens
    "gpt-5.1-codex-mini": (0.00025, 0.002),  # $0.25/$2 per 1M tokens
    "gpt-5.2": (0.00175, 0.014),  # $1.75/$14 per 1M tokens
    "gpt-5.2-chat": (0.00175, 0.014),  # $1.75/$14 per 1M tokens
    "gpt-5.2-codex": (0.00175, 0.014),  # $1.75/$14 per 1M tokens
    "gpt-5.2-pro": (0.021, 0.168),  # $21/$168 per 1M tokens
    "gpt-5.3-chat": (0.00175, 0.014),  # $1.75/$14 per 1M tokens
    "gpt-5.3-codex": (0.00175, 0.014),  # $1.75/$14 per 1M tokens
    "gpt-5.4": (0.0025, 0.015),  # $2.50/$15 per 1M tokens
    "gpt-5.4-pro": (0.03, 0.18),  # $30/$180 per 1M tokens
    "gpt-5.4-mini": (0.00075, 0.0045),  # $0.75/$4.50 per 1M tokens
    "gpt-5.4-nano": (0.0002, 0.00125),  # $0.20/$1.25 per 1M tokens
    "gpt-5.5": (0.005, 0.03),  # $5/$30 per 1M tokens
    "gpt-5.5-pro": (0.03, 0.18),  # $30/$180 per 1M tokens
    # OpenAI - GPT Series
    "gpt-4o": (0.0025, 0.01),  # $2.50/$10.00 per 1M tokens
    "gpt-4o-mini": (0.00015, 0.0006),  # $0.15/$0.60 per 1M tokens
    "gpt-4.1": (0.002, 0.008),  # $2.00/$8.00 per 1M tokens
    "gpt-4.1-mini": (0.0004, 0.0016),  # $0.40/$1.60 per 1M tokens
    "gpt-4.1-nano": (0.0001, 0.0004),  # $0.10/$0.40 per 1M tokens
    "gpt-4-turbo": (0.01, 0.03),  # Legacy pricing
    "gpt-4-turbo-preview": (0.01, 0.03),  # Legacy pricing
    "gpt-oss-120b": (0.00015, 0.0006),  # $0.15/$0.60 per 1M tokens (open-weight MoE)
    "gpt-4": (0.03, 0.06),  # Legacy pricing
    "gpt-3.5-turbo": (0.0005, 0.0015),  # Legacy pricing
    # OpenAI - Reasoning Models (o-series)
    "o1": (0.015, 0.06),  # $15/$60 per 1M tokens
    "o1-preview": (0.015, 0.06),  # $15/$60 per 1M tokens
    "o1-mini": (0.0011, 0.0044),  # $1.10/$4.40 per 1M tokens
    "o3": (0.002, 0.008),  # $2/$8 per 1M tokens
    "o3-mini": (0.0011, 0.0044),  # $1.10/$4.40 per 1M tokens
    "o3-pro": (0.02, 0.08),  # $20/$80 per 1M tokens
    "o4-mini": (0.0011, 0.0044),  # $1.10/$4.40 per 1M tokens
    # Anthropic - Claude 4.5 Series (Latest)
    "claude-opus-4-5": (0.005, 0.025),  # $5/$25 per 1M tokens
    "claude-sonnet-4-5": (0.003, 0.015),  # $3/$15 per 1M tokens
    "claude-haiku-4-5": (0.001, 0.005),  # $1/$5 per 1M tokens
    # Anthropic - Claude 4.8 Series
    # Assumed same rate card as Opus 4.7 - verify against account pricing.
    "claude-opus-4-8": (0.005, 0.025),  # $5/$25 per 1M tokens
    # Anthropic - Claude 4.7 Series
    "claude-opus-4-7": (0.005, 0.025),  # $5/$25 per 1M tokens
    # Anthropic - Claude 4.6 Series
    "claude-opus-4-6": (0.005, 0.025),  # $5/$25 per 1M tokens
    "claude-sonnet-4-6": (0.003, 0.015),  # $3/$15 per 1M tokens
    # Anthropic - Claude 4 Series
    "claude-opus-4": (0.015, 0.075),  # $15/$75 per 1M tokens
    "claude-opus-4-1": (0.015, 0.075),  # $15/$75 per 1M tokens
    "claude-sonnet-4": (0.003, 0.015),  # $3/$15 per 1M tokens
    # Anthropic - Claude 3.x Series
    "claude-3-7-sonnet": (0.003, 0.015),  # $3/$15 per 1M tokens
    "claude-3-5-sonnet": (0.003, 0.015),  # $3/$15 per 1M tokens
    "claude-3-5-haiku": (0.0008, 0.004),  # $0.80/$4 per 1M tokens
    "claude-3-opus": (0.015, 0.075),  # $15/$75 per 1M tokens
    "claude-3-sonnet": (0.003, 0.015),  # $3/$15 per 1M tokens
    "claude-3-haiku": (0.00025, 0.00125),  # $0.25/$1.25 per 1M tokens
    # Google - Gemini 2.5 Series
    "gemini-2.5-pro": (0.00125, 0.01),  # $1.25/$10 per 1M tokens (<=200k context)
    "gemini-2.5-flash": (0.0003, 0.0025),  # $0.30/$2.50 per 1M tokens
    "gemini-2.5-flash-lite": (0.0001, 0.0004),  # $0.10/$0.40 per 1M tokens
    # Google - Gemini 2.0 Series
    "gemini-2.0-flash": (0.0001, 0.0004),  # $0.10/$0.40 per 1M tokens
    "gemini-2.0-flash-lite": (0.000075, 0.0003),  # $0.075/$0.30 per 1M tokens
    "gemini-2.0-flash-exp": (0.0, 0.0),  # Experimental/Free tier
    "gemini-2.0-flash-thinking": (0.0, 0.0),  # Experimental/Free tier
    # Google - Gemini 3.x Series (OpenRouter catalog, March 2026)
    "gemini-3.5-flash": (0.0015, 0.009),  # $1.50/$9 per 1M tokens
    "gemini-3-flash-preview": (0.0005, 0.003),  # $0.50/$3 per 1M tokens
    "gemini-3-pro-preview": (0.002, 0.012),  # $2/$12 per 1M tokens
    "gemini-3-pro-image-preview": (0.002, 0.012),  # $2/$12 per 1M tokens
    "gemini-3.1-flash-image-preview": (0.00025, 0.0015),  # $0.25/$1.50 per 1M tokens
    "gemini-3.1-flash-lite-preview": (0.00025, 0.0015),  # $0.25/$1.50 per 1M tokens
    "gemini-3.1-pro-preview": (0.002, 0.012),  # $2/$12 per 1M tokens
    "gemini-3.1-pro-preview-customtools": (0.002, 0.012),  # $2/$12 per 1M tokens
    # Google - Gemini 1.5 Series (Legacy)
    "gemini-1.5-pro": (0.00125, 0.005),  # $1.25/$5 per 1M tokens
    "gemini-1.5-flash": (0.000075, 0.0003),  # $0.075/$0.30 per 1M tokens
    "gemini-1.5-flash-8b": (0.0000375, 0.00015),  # $0.0375/$0.15 per 1M tokens
    "gemini-1.0-pro": (0.0005, 0.0015),  # Legacy pricing
    # Bedrock - Anthropic (same as base models)
    "anthropic.claude-opus-4-5": (0.005, 0.025),
    "anthropic.claude-sonnet-4-5": (0.003, 0.015),
    "anthropic.claude-haiku-4-5": (0.001, 0.005),
    "anthropic.claude-opus-4-6": (0.005, 0.025),
    "anthropic.claude-sonnet-4-6": (0.003, 0.015),
    "anthropic.claude-opus-4": (0.015, 0.075),
    "anthropic.claude-sonnet-4": (0.003, 0.015),
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "anthropic.claude-3-5-haiku": (0.0008, 0.004),
    "anthropic.claude-3-opus": (0.015, 0.075),
    "anthropic.claude-3-sonnet": (0.003, 0.015),
    "anthropic.claude-3-haiku": (0.00025, 0.00125),
    # Bedrock - Amazon Nova
    "amazon.nova-pro": (0.0008, 0.0032),  # $0.80/$3.20 per 1M tokens
    "amazon.nova-lite": (0.00006, 0.00024),  # $0.06/$0.24 per 1M tokens
    "amazon.nova-micro": (0.000035, 0.00014),  # $0.035/$0.14 per 1M tokens
    # Bedrock - Meta Llama
    "meta.llama3-1-70b-instruct": (0.00099, 0.00099),
    "meta.llama3-1-8b-instruct": (0.00022, 0.00022),
    "meta.llama3-3-70b-instruct": (0.00099, 0.00099),
    # Databricks (varies by deployment)
    "databricks-meta-llama-3-1-70b-instruct": (0.001, 0.001),
    "databricks-meta-llama-3-1-405b-instruct": (0.005, 0.015),
    "databricks-meta-llama-3-3-70b-instruct": (0.001, 0.001),
    "databricks-dbrx-instruct": (0.00075, 0.00225),
    "databricks-mixtral-8x7b-instruct": (0.0005, 0.001),
    "databricks-claude-3-5-sonnet": (0.003, 0.015),
    "databricks-claude-sonnet-4": (0.003, 0.015),
    "databricks-claude-sonnet-4-5": (0.003, 0.015),
    "databricks-claude-sonnet-4-6": (0.003, 0.015),
    # Anthropic introductory Sonnet 5 rate through 2026-08-31;
    # update to $3/$15 per MTok on 2026-09-01.
    "databricks-claude-sonnet-5": (0.002, 0.01),
    "databricks-claude-opus-4-5": (0.005, 0.025),
    "databricks-claude-opus-4-6": (0.005, 0.025),
    "databricks-claude-opus-4-7": (0.005, 0.025),
    # Assumed same rate card as Opus 4.7 - verify against account pricing.
    "databricks-claude-opus-4-8": (0.005, 0.025),
    "databricks-claude-haiku-4-5": (0.001, 0.005),
    "databricks-gemini-3-5-flash": (0.0015, 0.009),
    "databricks-gpt-5-4-mini": (0.00075, 0.0045),
    "databricks-gpt-5-4-nano": (0.0002, 0.00125),
    "databricks-gpt-5-5": (0.005, 0.03),
    "databricks-gpt-5-5-pro": (0.03, 0.18),
    # DeepSeek
    "deepseek-r1": (0.00056, 0.00168),  # $0.56/$1.68 per 1M tokens
    "deepseek-chat": (0.00089, 0.0011),  # $0.89/$1.10 per 1M tokens (V3)
    "deepseek-reasoner": (0.00056, 0.00168),  # Alias for R1
    # OpenRouter - DeepSeek
    "deepseek/deepseek-r1": (0.00056, 0.00168),  # $0.56/$1.68 per 1M tokens
    "deepseek/deepseek-chat": (0.00089, 0.0011),  # $0.89/$1.10 per 1M tokens
    # OpenRouter - Mistral
    "mistralai/mistral-large": (0.002, 0.006),  # $2/$6 per 1M tokens
    "mistralai/mistral-medium": (0.00275, 0.0081),
    "mistralai/mistral-small": (0.0002, 0.0006),
    "mistralai/mistral-small-2603": (0.00015, 0.0006),  # $0.15/$0.60 per 1M tokens
    # OpenRouter - Meta Llama
    "meta-llama/llama-3.1-70b-instruct": (0.00052, 0.00075),
    "meta-llama/llama-3.1-8b-instruct": (0.00006, 0.00006),
    # OpenRouter - Cohere
    "cohere/command-r-plus": (0.003, 0.015),
    "cohere/command-r": (0.0005, 0.0015),
    # OpenRouter - xAI Grok (March 2026)
    "x-ai/grok-3": (0.003, 0.015),  # $3/$15 per 1M tokens
    "x-ai/grok-3-beta": (0.003, 0.015),  # $3/$15 per 1M tokens
    "x-ai/grok-3-mini": (0.0003, 0.0005),  # $0.30/$0.50 per 1M tokens
    "x-ai/grok-3-mini-beta": (0.0003, 0.0005),  # $0.30/$0.50 per 1M tokens
    "x-ai/grok-4": (0.003, 0.015),  # $3/$15 per 1M tokens
    "x-ai/grok-4-fast": (0.0002, 0.0005),  # $0.20/$0.50 per 1M tokens
    "x-ai/grok-4.1-fast": (0.0002, 0.0005),  # $0.20/$0.50 per 1M tokens
    "x-ai/grok-code-fast-1": (0.0002, 0.0015),  # $0.20/$1.50 per 1M tokens
    # OpenRouter - Qwen 3.5 family (March 2026)
    "qwen/qwen3.5-122b-a10b": (0.0003, 0.0024),  # $0.30/$2.40 per 1M tokens
    "qwen/qwen3.5-27b": (0.00027, 0.00216),  # $0.27/$2.16 per 1M tokens
    "qwen/qwen3.5-35b-a3b": (0.000225, 0.0018),  # $0.225/$1.80 per 1M tokens
    "qwen/qwen3.5-397b-a17b": (0.00055, 0.0035),  # $0.55/$3.50 per 1M tokens
    "qwen/qwen3.5-9b": (0.00005, 0.00015),  # $0.05/$0.15 per 1M tokens
    "qwen3.5-9b": (0.00005, 0.00015),  # $0.05/$0.15 per 1M tokens
    "qwen/qwen3.5-flash-02-23": (0.0001, 0.0004),  # $0.10/$0.40 per 1M tokens
    "qwen/qwen3.5-plus-02-15": (0.0004, 0.0024),  # $0.40/$2.40 per 1M tokens
    # OpenRouter - Inception (March 2026)
    "inception/mercury-2": (0.00025, 0.00075),  # $0.25/$0.75 per 1M tokens
    # OpenRouter - MiniMax and Xiaomi (March 2026)
    "minimax/minimax-m2.7": (0.0003, 0.0012),  # $0.30/$1.20 per 1M tokens
    "xiaomi/mimo-v2-pro": (0.001, 0.003),  # $1/$3 per 1M tokens
    "xiaomi/mimo-v2-omni": (0.0004, 0.002),  # $0.40/$2 per 1M tokens
    # OpenRouter - Z.ai GLM family (March 2026)
    "glm-4.7-flash": (0.00006, 0.0004),  # $0.06/$0.40 per 1M tokens
    "glm-4.7": (0.00038, 0.00198),  # $0.38/$1.98 per 1M tokens
    "glm-5": (0.00072, 0.0023),  # $0.72/$2.30 per 1M tokens
    "glm-5-turbo": (0.00096, 0.0032),  # $0.96/$3.20 per 1M tokens
    "nvidia/nemotron-3-super-120b-a12b": (0.0001, 0.0005),  # $0.10/$0.50 per 1M tokens
}

_REGISTERED_PRICING: dict[str, tuple[float, float]] = {}
_PROVIDER_PREFIXES = ("databricks-",)


def _pricing_candidates(model: str) -> list[str]:
    """Generate exact and normalized candidates for nested model identifiers."""
    seen: set[str] = set()
    candidates: list[str] = []

    def add(candidate: str) -> None:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

        normalized = candidate.replace(".", "-")
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    parts = model.strip().split("/")
    for idx in range(len(parts)):
        candidate = "/".join(parts[idx:])
        add(candidate)

        for prefix in _PROVIDER_PREFIXES:
            if candidate.startswith(prefix):
                add(candidate.removeprefix(prefix))

    return candidates


@cache
def _genai_prices_module() -> Any | None:
    """Import genai-prices lazily because pricing automation is optional."""
    try:
        return importlib.import_module("genai_prices")
    except ImportError:
        return None


def _lookup_registered_pricing(candidates: Iterable[str]) -> tuple[float, float] | None:
    candidate_list = list(candidates)
    for candidate in candidate_list:
        if candidate in _REGISTERED_PRICING:
            return _REGISTERED_PRICING[candidate]

    prefix_matches: list[tuple[int, tuple[float, float]]] = []
    for key, price in _REGISTERED_PRICING.items():
        for candidate in candidate_list:
            if candidate.startswith(key):
                prefix_matches.append((len(key), price))
                break
    if prefix_matches:
        return max(prefix_matches, key=lambda item: item[0])[1]

    return None


def _lookup_static_pricing(candidates: list[str]) -> tuple[float, float] | None:
    for candidate in candidates:
        if candidate in PRICING:
            return PRICING[candidate]

    # Prefix match for versioned models
    for key, price in PRICING.items():
        for candidate in candidates:
            if candidate.startswith(key):
                return price

    return None


def _lookup_exact_static_zero_pricing(candidates: list[str]) -> tuple[float, float] | None:
    """Preserve explicit static free-tier entries before broad upstream matches."""
    for candidate in candidates:
        if PRICING.get(candidate) == (0.0, 0.0):
            return (0.0, 0.0)
    return None


def _lookup_genai_pricing(model: str) -> tuple[float, float] | None:
    """Resolve base per-1K prices through genai-prices.

    The public ``get_pricing()`` facade has always returned base per-1K rates,
    not a usage-specific bill. Querying a small 1K/1K synthetic usage keeps
    tiered models on their base tier while ``calculate_cost()`` can still use
    the real token counts below.
    """
    cost = _calculate_genai_cost(model, input_tokens=1000, output_tokens=1000)
    if cost is None:
        return None
    return (cost.input_cost, cost.output_cost)


def _calculate_genai_cost(model: str, *, input_tokens: int, output_tokens: int) -> Cost | None:
    genai_prices = _genai_prices_module()
    if genai_prices is None:
        return None

    for candidate in _pricing_candidates(model):
        try:
            usage = genai_prices.Usage(input_tokens=input_tokens, output_tokens=output_tokens)
            calculation = genai_prices.calc_price(usage, model_ref=candidate)
        except (LookupError, ValueError):
            continue
        except Exception:
            logger.debug("genai-prices lookup failed for model %r", candidate, exc_info=True)
            continue

        input_cost = _decimal_to_float(calculation.input_price)
        output_cost = _decimal_to_float(calculation.output_price)
        return Cost(
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
        )

    return None


def _decimal_to_float(value: Decimal | float | int) -> float:
    return float(value)


def get_pricing(model: str) -> tuple[float, float]:
    """Get pricing for a model.

    Args:
        model: Model identifier.

    Returns:
        Tuple of (input_price_per_1k, output_price_per_1k) in USD.
    """
    candidates = _pricing_candidates(model)

    if registered := _lookup_registered_pricing(candidates):
        return registered

    if static_zero_price := _lookup_exact_static_zero_pricing(candidates):
        return static_zero_price

    if genai_price := _lookup_genai_pricing(model):
        return genai_price

    if static_price := _lookup_static_pricing(candidates):
        return static_price

    # Default: unknown pricing (free)
    logger.warning(
        f"Unknown model '{model}' - cost tracking will not work. Use register_pricing() to add pricing for this model."
    )
    return (0.0, 0.0)


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a completion in USD.

    Args:
        model: Model identifier.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Total cost in USD.
    """
    candidates = _pricing_candidates(model)
    if registered := _lookup_registered_pricing(candidates):
        input_price, output_price = registered
        return (input_tokens * input_price / 1000) + (output_tokens * output_price / 1000)

    if static_zero_price := _lookup_exact_static_zero_pricing(candidates):
        input_price, output_price = static_zero_price
        return (input_tokens * input_price / 1000) + (output_tokens * output_price / 1000)

    if genai_cost := _calculate_genai_cost(model, input_tokens=input_tokens, output_tokens=output_tokens):
        return genai_cost.total_cost

    if static_price := _lookup_static_pricing(candidates):
        input_price, output_price = static_price
    else:
        input_price, output_price = get_pricing(model)
    return (input_tokens * input_price / 1000) + (output_tokens * output_price / 1000)


def calculate_cost_from_usage(model: str, usage: Usage) -> Cost:
    """Calculate detailed cost from usage.

    Args:
        model: Model identifier.
        usage: Token usage statistics.

    Returns:
        Cost breakdown.
    """
    candidates = _pricing_candidates(model)
    if registered := _lookup_registered_pricing(candidates):
        input_price, output_price = registered
        input_cost = usage.input_tokens * input_price / 1000
        output_cost = usage.output_tokens * output_price / 1000
        return Cost(input_cost=input_cost, output_cost=output_cost, total_cost=input_cost + output_cost)

    if static_zero_price := _lookup_exact_static_zero_pricing(candidates):
        input_price, output_price = static_zero_price
        input_cost = usage.input_tokens * input_price / 1000
        output_cost = usage.output_tokens * output_price / 1000
        return Cost(input_cost=input_cost, output_cost=output_cost, total_cost=input_cost + output_cost)

    if genai_cost := _calculate_genai_cost(
        model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    ):
        return genai_cost

    input_price, output_price = get_pricing(model)
    input_cost = usage.input_tokens * input_price / 1000
    output_cost = usage.output_tokens * output_price / 1000

    return Cost(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
    )


def register_pricing(model: str, input_price: float, output_price: float) -> None:
    """Register custom pricing for a model.

    Args:
        model: Model identifier.
        input_price: Price per 1K input tokens in USD.
        output_price: Price per 1K output tokens in USD.
    """
    _REGISTERED_PRICING[model] = (input_price, output_price)
    PRICING[model] = (input_price, output_price)
