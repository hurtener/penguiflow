"""Native LLM request policy resolution.

Centralizes provider/model-specific behavior for structured output and reasoning
when using ``NativeLLMAdapter``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StructuredMode = Literal["json_schema", "json_object", "text"]


def _is_structured_mode(mode: StructuredMode) -> bool:
    return mode in {"json_schema", "json_object"}


def _databricks_supports_structured_reasoning(model: str) -> bool:
    """Whether Databricks model family supports structured output + native reasoning.

    Why: Databricks-backed Claude and Gemini 2.5 reject requests that effectively
    combine structured output with forced tool use and thinking/reasoning.
    Keep this family logic centralized to avoid scattering model checks.
    """
    model_norm = model.strip().lower()
    if model_norm.startswith("databricks/"):
        model_norm = model_norm.removeprefix("databricks/")

    incompatible_prefixes = (
        "databricks-claude-",
        "databricks-gemini-2-5-",
    )
    return not model_norm.startswith(incompatible_prefixes)


@dataclass(frozen=True)
class NativeRequestPolicy:
    """Resolved policy for a single request attempt."""

    mode: StructuredMode
    inject_reasoning_effort: bool
    emit_reasoning_callbacks: bool


def next_mode(mode: StructuredMode) -> StructuredMode | None:
    """Downgrade chain for structured output compatibility."""

    if mode == "json_schema":
        return "json_object"
    if mode == "json_object":
        return "text"
    return None


def _normalize_openrouter_route(model: str) -> tuple[str, str]:
    route = model.lower().strip()
    if route.startswith("openrouter/"):
        route = route.removeprefix("openrouter/")
    provider = route.split("/", 1)[0] if route else ""
    return route, provider


def _openrouter_preferred_mode(requested: StructuredMode, model: str) -> StructuredMode:
    """Conservative OpenRouter policy.

    - openai/google routes: keep requested mode (typically json_schema)
    - stepfun routes: force text mode
    - all other routes: force json_object
    """

    _, provider = _normalize_openrouter_route(model)
    if provider in {"openai", "google"}:
        return requested
    if provider == "stepfun":
        return "text"
    return "json_object"


def _is_claude_model(model: str) -> bool:
    route = model.lower().strip()
    if route.startswith("openrouter/"):
        route = route.removeprefix("openrouter/")
    if route.startswith("databricks/"):
        route = route.removeprefix("databricks/")
    model_leaf = route.split("/", 1)[-1] if "/" in route else route
    return "claude" in model_leaf


def resolve_policy(
    *,
    provider_name: str,
    model: str,
    requested_mode: StructuredMode,
    mode_override: StructuredMode | None,
    structured_reasoning_fallback_off: bool,
    use_native_reasoning: bool,
) -> NativeRequestPolicy:
    """Resolve request policy for one attempt.

    ``structured_reasoning_fallback_off`` applies only to NIM structured calls
    and is enabled adaptively after an error.
    """

    mode = requested_mode
    if mode_override is not None:
        mode = mode_override
    elif provider_name == "openrouter":
        mode = _openrouter_preferred_mode(requested_mode, model)
    elif provider_name == "nim" and requested_mode == "json_schema":
        # NIM structured calls are more stable with json_object semantics.
        mode = "json_object"
    elif (
        provider_name in {"anthropic", "databricks"}
        and requested_mode in {"json_schema", "json_object"}
        and use_native_reasoning
        and _is_claude_model(model)
    ):
        # Anthropic Claude endpoints do not support forced structured output
        # together with thinking/reasoning.
        mode = "text"

    inject_reasoning_effort = use_native_reasoning
    emit_reasoning_callbacks = True
    is_structured = _is_structured_mode(requested_mode)
    if provider_name == "nim" and is_structured and structured_reasoning_fallback_off:
        inject_reasoning_effort = False
        emit_reasoning_callbacks = False
    if provider_name == "databricks" and is_structured and not _databricks_supports_structured_reasoning(model):
        inject_reasoning_effort = False
        emit_reasoning_callbacks = False

    return NativeRequestPolicy(
        mode=mode,
        inject_reasoning_effort=inject_reasoning_effort,
        emit_reasoning_callbacks=emit_reasoning_callbacks,
    )


__all__ = ["NativeRequestPolicy", "StructuredMode", "next_mode", "resolve_policy"]
