"""NVIDIA NIM model profiles.

Profiles are conservative in v1 to maximize compatibility:
- Prefer tools mode over strict native schema-guided output
- Keep strict mode disabled by default
"""

from __future__ import annotations

from . import ModelProfile

PROFILES: dict[str, ModelProfile] = {
    # Qwen 3.5 397B on NIM
    "qwen/qwen3.5-397b-a17b": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        thinking_tags=("<think>", "</think>"),
    ),
    # Minimax
    "minimaxai/minimax-m2.1": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        thinking_tags=("<think>", "</think>"),
    ),
    # GLM
    "z-ai/glm5": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        thinking_tags=("<think>", "</think>"),
    ),
    # Kimi
    "moonshotai/kimi-k2.5": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        thinking_tags=("<think>", "</think>"),
    ),
    # DeepSeek
    "deepseek-ai/deepseek-v3.1-terminus": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        thinking_tags=("<think>", "</think>"),
    ),
    # Step
    "stepfun-ai/step-3.5-flash": ModelProfile(
        supports_schema_guided_output=False,
        supports_json_only_output=True,
        supports_tools=True,
        supports_reasoning=True,
        supports_streaming=True,
        default_output_mode="tools",
        native_structured_kind="openai_compatible_tools",
        strict_mode_default=False,
        thinking_tags=("<think>", "</think>"),
    ),
}
