"""Schema planning for the LLM layer.

The SchemaPlan ties transformers + provider limits to mode selection,
providing compatibility signals and graceful degradation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from .transformer import estimate_object_key_count, has_composition_keywords, has_refs

if TYPE_CHECKING:
    from ..profiles import ModelProfile


class OutputMode(Enum):
    """Structured output mode."""

    NATIVE = "native"  # Provider-native schema-guided output
    TOOLS = "tools"  # Tool/function calling
    PROMPTED = "prompted"  # Schema in prompt + parse/retry


@dataclass(frozen=True)
class SchemaPlan:
    """Result of schema planning for a specific provider.

    Contains the transformed schema along with compatibility information
    that guides mode selection and degradation.
    """

    requested_schema: dict[str, Any]
    transformed_schema: dict[str, Any]
    strict_requested: bool
    strict_applied: bool
    compatible_with_native: bool
    compatible_with_tools: bool
    reasons: tuple[str, ...] = ()
    estimated_total_keys: int | None = None
    has_recursive_refs: bool = False
    has_composition: bool = False


def plan_schema(
    profile: ModelProfile,
    schema: dict[str, Any],
    *,
    mode: OutputMode | None = None,
) -> SchemaPlan:
    """Plan schema transformation for a specific provider and mode.

    Args:
        profile: The model profile with capabilities.
        schema: The JSON schema to plan for.
        mode: Optional specific mode to plan for.

    Returns:
        A SchemaPlan with transformation results and compatibility info.
    """
    from ..profiles import get_schema_transformer

    strict_requested = profile.strict_mode_default
    if mode == OutputMode.PROMPTED:
        strict_requested = False

    transformed = schema
    reasons: list[str] = []
    strict_applied = strict_requested

    # Apply schema transformation
    transformer = get_schema_transformer(profile, schema, strict=strict_requested)
    if transformer:
        transformed = transformer.transform()
        if strict_requested and not transformer.is_strict_compatible:
            strict_applied = False
            reasons.append("Schema required lossy transformations; strict disabled.")
        reasons.extend(transformer._warnings)

    # Analyze schema complexity
    estimated_keys = estimate_object_key_count(transformed)
    has_composition = has_composition_keywords(schema)
    has_recursive = has_refs(schema) and transformer is not None and bool(transformer._recursive_refs)

    # Provider-specific viability checks
    compatible_with_native = True
    compatible_with_tools = True

    # Check key limits (e.g., Databricks max 64 keys)
    if profile.max_schema_keys is not None and estimated_keys > profile.max_schema_keys:
        compatible_with_native = False
        reasons.append(f"Schema exceeds {profile.max_schema_keys}-key limit ({estimated_keys} keys).")

    # Check for composition support
    if profile.native_structured_kind == "databricks_constrained_decoding" and has_composition:
        compatible_with_native = False
        reasons.append("Databricks constrained decoding does not support anyOf/oneOf/allOf.")

    # Check for recursive refs
    if has_recursive:
        # Most providers don't handle recursive refs well in native mode
        compatible_with_native = False
        reasons.append("Schema contains recursive references.")

    # Check native support
    if not profile.supports_schema_guided_output:
        compatible_with_native = False
        if "Does not support schema-guided output." not in reasons:
            reasons.append("Does not support schema-guided output.")

    # Check tools support
    if not profile.supports_tools:
        compatible_with_tools = False
        reasons.append("Does not support tool calling.")

    return SchemaPlan(
        requested_schema=schema,
        transformed_schema=transformed,
        strict_requested=strict_requested,
        strict_applied=strict_applied,
        compatible_with_native=compatible_with_native,
        compatible_with_tools=compatible_with_tools,
        reasons=tuple(reasons),
        estimated_total_keys=estimated_keys,
        has_recursive_refs=has_recursive,
        has_composition=has_composition,
    )


def choose_output_mode(
    profile: ModelProfile,
    schema: dict[str, Any],
) -> tuple[OutputMode, SchemaPlan]:
    """Choose the best output mode for a schema and provider.

    Follows the mode preference ladder:
    1. Profile's default mode (if compatible)
    2. NATIVE (if supported and compatible)
    3. TOOLS (if supported and compatible)
    4. PROMPTED (always available)

    Args:
        profile: The model profile.
        schema: The JSON schema for structured output.

    Returns:
        Tuple of (chosen mode, schema plan).
    """
    # Build preference order
    preference: list[OutputMode] = [
        OutputMode(profile.default_output_mode),
        OutputMode.NATIVE,
        OutputMode.TOOLS,
        OutputMode.PROMPTED,
    ]

    # Remove duplicates while preserving order
    seen: set[OutputMode] = set()
    ordered: list[OutputMode] = []
    for m in preference:
        if m not in seen:
            seen.add(m)
            ordered.append(m)

    last_plan: SchemaPlan | None = None

    for mode in ordered:
        plan = plan_schema(profile, schema, mode=mode)
        last_plan = plan

        if mode == OutputMode.NATIVE:
            if profile.supports_schema_guided_output and plan.compatible_with_native:
                return mode, plan

        elif mode == OutputMode.TOOLS:
            if profile.supports_tools and plan.compatible_with_tools:
                return mode, plan

        elif mode == OutputMode.PROMPTED:
            return mode, plan

    # Defensive fallback
    assert last_plan is not None
    return OutputMode.PROMPTED, last_plan
