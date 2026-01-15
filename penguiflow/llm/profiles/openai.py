"""OpenAI model profiles.

Defines capabilities and configuration for OpenAI/GPT models.

Model Families (as of January 2026):
- GPT-4.1: Latest flagship models with 1M token context window
- GPT-4o: Optimized multimodal models (gpt-4o, gpt-4o-mini)
- GPT-4 Turbo: Previous generation high-capability models
- GPT-OSS-120B: Open-weight 117B MoE model for agentic use cases
- GPT-4/3.5: Legacy models (still supported)
- o-series: Reasoning models (o1, o3, o4-mini) with chain-of-thought

SDK Version: Compatible with openai>=2.0.0 (current: 2.15.0)
Reference: https://platform.openai.com/docs/models
"""

from __future__ import annotations

from . import ModelProfile

PROFILES: dict[str, ModelProfile] = {
    # GPT-4.1 family (latest, 1M context)
    "gpt-4.1": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=32768,
    ),
    "gpt-4.1-mini": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=32768,
    ),
    "gpt-4.1-nano": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=1000000,
        max_output_tokens=16384,
    ),
    # GPT-4o family
    "gpt-4o": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=16384,
    ),
    "gpt-4o-mini": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=16384,
    ),
    # GPT-4 Turbo
    "gpt-4-turbo": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "gpt-4-turbo-preview": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    # GPT-OSS-120B: Open-weight 117B MoE model
    # 5.1B active params per forward pass, optimized for H100 with MXFP4 quantization
    # Supports configurable reasoning depth, full chain-of-thought, native tool use
    # Pricing: $0.15/1M input, $0.60/1M output
    "gpt-oss-120b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=131072,  # 131.1K
        max_output_tokens=131072,   # 131.1K
    ),
    # GPT-4 (legacy)
    "gpt-4": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=False,
        max_context_tokens=8192,
        max_output_tokens=4096,
    ),
    # GPT-3.5 (legacy)
    "gpt-3.5-turbo": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        strict_mode_default=False,
        max_context_tokens=16385,
        max_output_tokens=4096,
    ),
    # o4-mini reasoning model (latest small reasoning)
    "o4-mini": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=100000,
    ),
    # o3 family (advanced reasoning)
    "o3": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=100000,
    ),
    "o3-pro": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=100000,
    ),
    "o3-mini": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=100000,
    ),
    # o1 reasoning models (legacy)
    "o1": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=200000,
        max_output_tokens=100000,
    ),
    "o1-preview": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=False,  # o1-preview doesn't support tools
        supports_reasoning=True,
        supports_streaming=False,  # o1-preview doesn't support streaming
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=32768,
    ),
    "o1-mini": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=False,  # o1-mini doesn't support tools
        supports_reasoning=True,
        supports_streaming=False,  # o1-mini doesn't support streaming
        default_output_mode="native",
        native_structured_kind="openai_response_format",
        schema_transformer_name="OpenAIJsonSchemaTransformer",
        reasoning_effort_param="reasoning_effort",
        strict_mode_default=True,
        max_context_tokens=128000,
        max_output_tokens=65536,
    ),
}
