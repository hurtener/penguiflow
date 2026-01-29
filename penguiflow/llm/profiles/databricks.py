"""Databricks model profiles.

Defines capabilities and configuration for Databricks Foundation Model APIs.

Updated January 2026 based on:
https://docs.databricks.com/en/machine-learning/foundation-models/supported-models.html
Databricks SDK v0.77.0+
"""

from __future__ import annotations

from . import ModelProfile

# Databricks-specific limits
MAX_SCHEMA_KEYS = 64
MAX_TOOLS = 32
MAX_TOOL_SCHEMA_KEYS = 16

PROFILES: dict[str, ModelProfile] = {
    # ==========================================================================
    # OpenAI GPT-5 Series on Databricks (January 2026)
    # ==========================================================================
    "databricks-gpt-5-2": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Advanced reasoning capabilities
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=400000,
        max_output_tokens=128000,
    ),
    "databricks-gpt-5-1": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Instant and Thinking modes
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=400000,
        max_output_tokens=128000,
    ),
    "databricks-gpt-5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=400000,
        max_output_tokens=128000,
    ),
    "databricks-gpt-5-mini": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Cost-optimized reasoning
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=400000,
        max_output_tokens=128000,
    ),
    "databricks-gpt-5-nano": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,  # High-throughput, classification focused
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=400000,
        max_output_tokens=128000,
    ),
    "databricks-gpt-oss-120b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Adjustable reasoning effort
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=32000,
    ),
    "databricks-gpt-oss-20b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Lightweight reasoning
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=16000,
    ),
    # ==========================================================================
    # Anthropic Claude Series on Databricks (January 2026)
    # ==========================================================================
    "databricks-claude-opus-4-5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,  # Only schema-guided, not json_object
        supports_tools=True,
        supports_reasoning=True,  # Supports extended thinking
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=200000,
        max_output_tokens=64000,
    ),
    "databricks-claude-sonnet-4-5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,
        supports_tools=True,
        supports_reasoning=True,  # Hybrid reasoning, near-instant or extended thinking
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=200000,
        max_output_tokens=64000,
    ),
    "databricks-claude-haiku-4-5": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,
        supports_tools=True,
        supports_reasoning=True,  # Fast, cost-effective
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=200000,
        max_output_tokens=64000,
    ),
    "databricks-claude-sonnet-4": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,
        supports_tools=True,
        supports_reasoning=True,  # Hybrid reasoning with extended thinking
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=200000,
        max_output_tokens=64000,
    ),
    "databricks-claude-opus-4-1": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=200000,
        max_output_tokens=32000,
    ),
    # Claude 3.7 Sonnet - Retiring April 12, 2026
    "databricks-claude-3-7-sonnet": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=False,
        supports_tools=True,
        supports_reasoning=True,  # Hybrid reasoning
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=200000,
        max_output_tokens=8192,
    ),
    # ==========================================================================
    # Google Gemini Series on Databricks (January 2026)
    # ==========================================================================
    "databricks-gemini-3-flash": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Multimodal, video analysis, fast
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=1048576,
        max_output_tokens=65536,
    ),
    "databricks-gemini-3-pro": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Hybrid reasoning, advanced multimodal
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=1048576,
        max_output_tokens=65536,
    ),
    "databricks-gemini-2-5-pro": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Deep Think Mode
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=1048576,
        max_output_tokens=65536,
    ),
    "databricks-gemini-2-5-flash": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,  # Hybrid reasoning
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=1048576,
        max_output_tokens=65536,
    ),
    "databricks-gemma-3-12b": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,  # Multimodal, 140+ languages
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    # ==========================================================================
    # Meta Llama Series on Databricks (January 2026)
    # ==========================================================================
    "databricks-llama-4-maverick": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,  # MoE architecture, multilingual
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "databricks-meta-llama-3-3-70b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,  # Dialogue-optimized, multilingual
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    # Retiring February 15, 2026 (pay-per-token)
    "databricks-meta-llama-3-1-405b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    "databricks-meta-llama-3-1-8b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,  # Lightweight, multilingual
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=4096,
    ),
    # ==========================================================================
    # Alibaba Qwen on Databricks (January 2026)
    # ==========================================================================
    "databricks-qwen3-next-80b-a3b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,  # Ultra-long contexts, multi-step workflows
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=128000,
        max_output_tokens=8192,
    ),
    # ==========================================================================
    # Legacy Models (kept for backward compatibility)
    # ==========================================================================
    # DBRX - Databricks' own model
    "databricks-dbrx-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=32768,
        max_output_tokens=4096,
    ),
    # Mixtral
    "databricks-mixtral-8x7b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_tools=MAX_TOOLS,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=32768,
        max_output_tokens=4096,
    ),
    # MPT
    "databricks-mpt-30b-instruct": ModelProfile(
        supports_schema_guided_output=True,
        supports_json_only_output=True,
        supports_tools=False,  # MPT has limited tool support
        supports_reasoning=False,
        supports_streaming=True,
        default_output_mode="native",
        native_structured_kind="databricks_constrained_decoding",
        schema_transformer_name="DatabricksJsonSchemaTransformer",
        strict_mode_default=True,
        max_schema_keys=MAX_SCHEMA_KEYS,
        max_context_tokens=8192,
        max_output_tokens=2048,
    ),
}
