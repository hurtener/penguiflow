"""LLM client utilities and wrappers for planner."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeGuard, get_args, get_origin

from pydantic import BaseModel

from ..llm.errors import LLMRateLimitError
from ..llm.profiles import get_profile
from ..llm.types import TextPart
from . import prompts
from .models import (
    ClarificationResponse,
    JSONLLMClient,
    PlannerAction,
    ReflectionCritique,
)
from .trajectory import (
    Trajectory,
    TrajectorySummary,
    extract_background_results,
)

if TYPE_CHECKING:
    from types import ModuleType

    from ..llm.profiles import ModelProfile
    from .models import ReasoningJSONLLMClient
    from .react import ReactPlanner

logger = logging.getLogger("penguiflow.planner")


# ---------------------------------------------------------------------------
# LLM Error Classification
# ---------------------------------------------------------------------------


class LLMErrorType(Enum):
    """Classification of LLM errors for recovery strategy selection."""

    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    RATE_LIMIT = "rate_limit"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TIMEOUT = "timeout"
    BAD_REQUEST_OTHER = "bad_request_other"
    UNKNOWN = "unknown"


_CONTEXT_LENGTH_PATTERNS = (
    "input is too long",
    "context length",
    "maximum context",
    "token limit",
    "context_length_exceeded",
    "max_tokens",
    "too many tokens",
    "exceeds the model",
)


def classify_llm_error(exc: Exception) -> LLMErrorType:
    """Classify an LLM exception for appropriate recovery strategy.

    Args:
        exc: The exception raised by the LLM client.

    Returns:
        The classified error type.
    """
    error_str = str(exc).lower()
    error_type_name = exc.__class__.__name__

    # Check for timeout errors (LLMTimeoutError, TimeoutError,
    # asyncio.TimeoutError)
    if "Timeout" in error_type_name or isinstance(exc, TimeoutError):
        return LLMErrorType.TIMEOUT

    # Check for rate limiting errors
    if "RateLimit" in error_type_name:
        return LLMErrorType.RATE_LIMIT

    # Check for service unavailable errors
    if "ServiceUnavailable" in error_type_name or "Server" in error_type_name:
        return LLMErrorType.SERVICE_UNAVAILABLE

    # Check for context length exceeded errors
    if any(pattern in error_str for pattern in _CONTEXT_LENGTH_PATTERNS):
        return LLMErrorType.CONTEXT_LENGTH_EXCEEDED

    # Check for other bad request errors
    if "BadRequest" in error_type_name:
        return LLMErrorType.BAD_REQUEST_OTHER

    return LLMErrorType.UNKNOWN


def extract_clean_error_message(exc: Exception) -> str:
    """Extract a user-friendly error message from an LLM exception.

    Handles nested JSON error messages from providers like Databricks.

    Args:
        exc: The exception to extract the message from.

    Returns:
        A clean, user-readable error message.
    """
    error_str = str(exc)

    # Try to extract nested JSON message (common in Databricks errors)
    # Pattern: {"error_code":"BAD_REQUEST","message":"{"message":"..."}"}
    try:
        # Find the innermost "message" value
        import re

        # Look for nested message patterns
        matches = re.findall(r'"message"\s*:\s*"([^"]+)"', error_str)
        if matches:
            # Return the last (most nested) message
            return str(matches[-1])
    except Exception:
        pass

    # Fallback: extract after the last colon for standard exceptions
    if ": " in error_str:
        return error_str.split(": ", 1)[-1].strip('"{}')

    return error_str


def _extract_json_from_text(text: str) -> str:
    """Extract JSON object content from mixed text + fenced code blocks.

    Handles multiple scenarios including:
    - Plain JSON object
    - JSON in fenced code blocks (```json ... ```)
    - Thinking/reasoning content followed by JSON (RFC fallback parsing)
    """
    text = text.strip()

    # Try fenced code block first (```json ... ```)
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)

    # Fallback: find first { and last } to extract JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return text


def _supports_reasoning(model_name: str) -> bool:
    """Check if a model supports native reasoning via LiteLLM.

    Uses LiteLLM's supports_reasoning() function when available. Falls back to
    heuristic detection for known reasoning models.

    Args:
        model_name: The model identifier string (e.g., "openai/o1",
            "deepseek-reasoner").

    Returns:
        True if the model supports native reasoning content.
    """
    try:
        import litellm

        # Use LiteLLM's built-in detection if available.
        if hasattr(litellm, "supports_reasoning"):
            result = litellm.supports_reasoning(model=model_name)
            # Only trust explicit True; for False/unknown we fall back to
            # heuristics because LiteLLM may not recognize unprefixed or custom
            # model IDs.
            if result is True:
                return True
    except Exception:
        pass

    # Fallback heuristic for known reasoning models
    lower = model_name.lower()
    reasoning_indicators = (
        "o1",  # OpenAI o1 family
        "o3",  # OpenAI o3 family
        "deepseek-reasoner",
        "deepseek-r1",
        "reasoning",
    )
    return any(indicator in lower for indicator in reasoning_indicators)


def _coerce_llm_response(result: object) -> tuple[str, float]:
    """Normalise JSON LLM client responses to ``(content, cost)`` tuples.

    Raises:
        TypeError: If the client returned anything other than a string or a
            ``(string, number)`` tuple.
    """
    if isinstance(result, tuple):
        content, cost = result
        if isinstance(content, str) and isinstance(cost, (int, float)):
            return _extract_json_from_text(content), float(cost)
    elif isinstance(result, str):
        return _extract_json_from_text(result), 0.0
    msg = (
        "Expected JSONLLMClient to return a string or (string, float) "
        f"tuple, received {type(result)!r}"
    )
    raise TypeError(msg)


def _sanitize_schema_value(
    value: object, *, strict_mode: bool, require_all_fields: bool
) -> object:
    """Sanitize ``value`` when it is a schema; return other values as-is."""
    if not isinstance(value, dict):
        return value
    return _sanitize_json_schema(
        value, strict_mode=strict_mode, require_all_fields=require_all_fields
    )


_UNSUPPORTED_SCHEMA_CONSTRAINTS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
        "format",
    }
)
# Marks a schema keyword that the sanitized schema drops.
_DROP = object()


def _sanitize_schema_item(
    key: str, value: object, *, strict_mode: bool, require_all_fields: bool
) -> object:
    """Sanitize one schema keyword's value; ``_DROP`` removes the keyword."""
    if key in _UNSUPPORTED_SCHEMA_CONSTRAINTS:
        return _DROP
    if key in ("properties", "$defs") and isinstance(value, dict):
        # $defs holds the nested definitions Pydantic uses for referenced
        # models.
        return {
            name: _sanitize_schema_value(
                sub_schema,
                strict_mode=strict_mode,
                require_all_fields=require_all_fields,
            )
            for name, sub_schema in value.items()
        }
    if key == "items" and isinstance(value, dict):
        return _sanitize_json_schema(
            value,
            strict_mode=strict_mode,
            require_all_fields=require_all_fields,
        )
    if key == "additionalProperties":
        # In strict mode additionalProperties must be false (not true or a
        # schema; OpenAI strict mode doesn't support typed
        # additionalProperties): it is set after the keyword loop.
        if strict_mode:
            return _DROP
        if isinstance(value, dict):
            return _sanitize_json_schema(value, strict_mode=strict_mode)
        return value
    if key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
        return [
            _sanitize_schema_value(
                item,
                strict_mode=strict_mode,
                require_all_fields=require_all_fields,
            )
            for item in value
        ]
    return value


def _close_object_schema(
    sanitized: dict[str, Any], *, require_all_fields: bool
) -> None:
    """Forbid additional properties on an object schema (strict mode).

    This is required by the OpenAI/OpenRouter structured outputs API.
    """
    if sanitized.get("type") != "object" and "properties" not in sanitized:
        return
    sanitized["additionalProperties"] = False
    properties = sanitized.get("properties", {}) or {}
    if properties and require_all_fields:
        required_keys = set(sanitized.get("required", []))
        required_keys.update(properties.keys())
        if required_keys:
            sanitized["required"] = sorted(required_keys)


def _sanitize_json_schema(
    schema: dict[str, Any],
    *,
    strict_mode: bool = False,
    require_all_fields: bool = False,
    inline_defs: bool = False,
) -> dict[str, Any]:
    """Remove JSON schema constraints that providers commonly reject.

    Args:
        schema: The JSON schema to sanitize.
        strict_mode: If True, adds 'additionalProperties: false' to all object
            schemas, as required by OpenAI/OpenRouter structured outputs.
        require_all_fields: If True, marks all object properties as required
            (used for providers that demand exhaustive required lists).
        inline_defs: If True, replaces local $defs references with inline
            schemas to satisfy providers that cannot resolve $refs in
            response_format.
    """
    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        item = _sanitize_schema_item(
            key,
            value,
            strict_mode=strict_mode,
            require_all_fields=require_all_fields,
        )
        if item is not _DROP:
            sanitized[key] = item
    if strict_mode:
        _close_object_schema(sanitized, require_all_fields=require_all_fields)
    if inline_defs:
        sanitized = _inline_defs(sanitized, sanitized.get("$defs", {}))
    return sanitized


def _build_minimal_planner_schema() -> dict[str, Any]:
    """OpenAI-strict PlannerAction schema without $refs or advanced features.

    This mirrors the unified action format (only ``next_node`` + ``args``). We
    keep ``args`` permissive (additional properties allowed) because tool arg
    shapes are tool-dependent and cannot be exhaustively listed here.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "next_node": {"type": "string"},
            "args": {"type": "object"},
        },
        "required": ["next_node", "args"],
        "additionalProperties": False,
    }
    return schema


def _build_planner_action_schema_conditional_finish(
    structured_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """PlannerAction schema with a finish-specific requirement.

    Pydantic's `PlannerAction` intentionally excludes `thought` from the JSON
    schema, and cannot express a conditional requirement:
      if next_node == "final_response" then args.answer is required.

    Some models (notably Gemini) otherwise tend to emit:
      {"next_node": "final_response", "args": {}}
    which forces an avoidable finish_repair cycle.
    """
    base: dict[str, Any] = {
        "type": "object",
        "properties": {
            "next_node": {"type": "string"},
            # args is intentionally open-ended; tool args vary by next_node.
            "args": {"type": "object"},
        },
        "required": ["next_node", "args"],
        "additionalProperties": False,
    }

    finish_args_properties: dict[str, Any] = {
        "answer": {"type": "string", "minLength": 1}
    }
    finish_args_required = ["answer"]
    if structured_schema is not None:
        # final_response_model: the structured payload is required on finish.
        finish_args_properties["structured"] = dict(structured_schema)
        finish_args_required.append("structured")

    conditional: dict[str, Any] = {
        "if": {
            "properties": {"next_node": {"enum": ["final_response"]}},
            "required": ["next_node"],
        },
        "then": {
            "properties": {
                "args": {
                    "type": "object",
                    "properties": finish_args_properties,
                    "required": finish_args_required,
                }
            },
            "required": ["args"],
        },
    }

    # Keep a top-level object type for stricter OpenAI-compatible validators
    # (e.g., some OpenRouter upstream providers) that reject schemas without
    # an explicit root "type".
    return {"type": "object", "allOf": [base, conditional]}


def _supports_conditional_finish_schema(model_name: str) -> bool:
    """Whether a model should use the conditional finish schema.

    The conditional ``allOf`` schema was introduced to improve finish-action
    quality for Gemini-family models. Some OpenAI-compatible providers reject
    ``allOf`` in response-format schemas, so we limit this shape to models that
    benefit from it.
    """
    lower = model_name.lower()
    return (
        "gemini" in lower or lower.startswith("google/") or "/google/" in lower
    )


def _inline_defs(
    schema: dict[str, Any], defs: dict[str, Any]
) -> dict[str, Any]:
    """Inline local $defs references (limited scope for response_format)."""

    def resolve(node: object) -> object:
        if isinstance(node, dict):
            return resolve_mapping(node)
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    def resolve_mapping(node: dict[str, Any]) -> object:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            if key in defs:
                return resolve(deepcopy(defs[key]))
        return {k: resolve(v) for k, v in node.items() if k != "$defs"}

    inlined = resolve_mapping(schema)
    if isinstance(inlined, dict):
        inlined.pop("$defs", None)
        return inlined
    return schema


def _response_format_policy(model_name: str) -> str:
    """Select response_format strategy based on model/provider capabilities."""
    lower = model_name.lower()

    if "maverick" in lower:
        return "json_object"

    weak_schema_models = (
        "anthropic" in lower
        or "claude" in lower
        or "xai" in lower
        or "grok" in lower
        or "mistral" in lower
        or "gemini" in lower
        or "google" in lower
        or "llama" in lower
        or "qwen" in lower
        or "deepseek" in lower
        or "cohere" in lower
        or "nvidia" in lower
        or "nim/" in lower
    )

    openai_like = (
        "openai" in lower
        or (lower.startswith("gpt-") and "oss" not in lower)
        or (
            ("gpt-" in lower or "o1" in lower or "o3" in lower)
            and "openrouter" in lower
        )
    ) or model_name.startswith("openai/")

    if weak_schema_models:
        return "json_object"
    if openai_like:
        return "json_object"
    return "sanitized_schema"


def _artifact_placeholder(value: object) -> str:
    """Create a compact placeholder for artifact fields."""
    type_name = type(value).__name__
    size_hint: str | None = None
    try:
        if (
            isinstance(value, (str, bytes, bytearray, Sequence))
            and not isinstance(value, Mapping)
        ) or isinstance(value, Mapping):
            size_hint = str(len(value))
    except Exception:
        size_hint = None
    return (
        f"<artifact:{type_name}>"
        if size_hint is None
        else f"<artifact:{type_name} size={size_hint}>"
    )


def _unwrap_model(annotation: object) -> type[BaseModel] | None:
    """Extract a BaseModel subclass from a possibly-nested annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    origin = get_origin(annotation)
    if origin is None:
        return None

    for arg in get_args(annotation):
        model = _unwrap_model(arg)
        if model is not None:
            return model
    return None


def _redact_artifacts(
    out_model: type[BaseModel],
    observation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Redact artifact-marked fields from an observation for LLM context."""
    if observation is None:
        return {}

    redacted: dict[str, Any] = {}
    fields = getattr(out_model, "model_fields", {}) or {}

    for field_name, value in observation.items():
        field_info = fields.get(field_name)
        extra = (
            field_info.json_schema_extra or {}
            if field_info is not None
            else {}
        )
        if extra.get("artifact"):
            redacted[field_name] = _artifact_placeholder(value)
            continue

        nested_model: type[BaseModel] | None = None
        if field_info is not None:
            nested_model = _unwrap_model(field_info.annotation)

        if nested_model and isinstance(value, Mapping):
            redacted[field_name] = _redact_artifacts(nested_model, value)
            continue
        if (
            nested_model
            and isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
        ):
            redacted[field_name] = [
                _redact_artifacts(nested_model, item)
                if isinstance(item, Mapping)
                else item
                for item in value
            ]
            continue

        redacted[field_name] = value

    return redacted


def _import_litellm() -> ModuleType:
    """Import LiteLLM, explaining how to install it when it is missing."""
    try:
        module = importlib.import_module("litellm")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = (
            "LiteLLM is not installed. Install "
            "penguiflow-lite[planner] or provide a custom llm_client."
        )
        raise RuntimeError(msg) from exc
    return module


def _litellm_response_format(
    response_format: Mapping[str, Any], model_name: str
) -> Mapping[str, Any]:
    """Adapt a JSON-schema response format to what the model supports."""
    if "json_schema" not in response_format:
        return response_format
    policy = _response_format_policy(model_name)
    schema_payload = response_format["json_schema"]
    sanitized_format = dict(response_format)

    if policy == "no_format":
        logger.debug(
            "json_schema_disabled",
            extra={
                "model": model_name,
                "reason": "no_format_policy",
            },
        )
        return {"type": "text"}
    if policy == "json_object":
        logger.debug(
            "json_schema_downgraded",
            extra={
                "model": model_name,
                "reason": "json_object_policy",
                "fallback": "json_object",
            },
        )
        return {"type": "json_object"}
    if policy == "strict_schema":
        sanitized_format["json_schema"] = {
            "name": schema_payload["name"],
            "strict": True,
            "schema": _build_minimal_planner_schema(),
        }
        logger.debug(
            "json_schema_strict",
            extra={"model": model_name, "strict_mode": True},
        )
        return sanitized_format
    sanitized_format["json_schema"] = {
        "name": schema_payload["name"],
        "schema": _sanitize_json_schema(
            schema_payload["schema"],
            strict_mode=True,
            require_all_fields=False,
            inline_defs=False,
        ),
    }
    logger.debug(
        "json_schema_sanitized",
        extra={
            "model": model_name,
            "strict_mode": False,
            "schema_sanitized": True,
        },
    )
    return sanitized_format


def _chunk_delta(chunk: object) -> object:
    """Return the first choice's ``delta`` of a streamed chunk, if any."""
    if isinstance(chunk, Mapping):
        choices = chunk.get("choices") or []
        if not choices:
            return None
        return choices[0].get("delta") or {}
    try:
        return getattr(getattr(chunk, "choices", [])[0], "delta", None)
    except Exception:
        return None


def _delta_field(delta: object, *names: str) -> object:
    """Return the first truthy field among ``names`` of a stream delta."""
    for name in names:
        value = (
            delta.get(name)
            if isinstance(delta, Mapping)
            else getattr(delta, name, None)
        )
        if value:
            return value
    return None


def _notify(
    callback: Callable[[str, bool], None],
    text: str,
    *,
    done: bool,
    error_event: str,
) -> None:
    """Call a stream callback, logging (not raising) its errors."""
    try:
        callback(text, done)
    except Exception:
        logger.exception(error_event)


def _stream_cost(
    litellm: ModuleType,
    *,
    model: str,
    content: str,
    usage: Mapping[str, Any],
) -> float:
    """Price a streamed completion from its usage chunk (0.0 on failure)."""
    try:
        # Construct a minimal response object for accurate cost calculation
        from litellm import ModelResponse
        from litellm.types.utils import Usage as LiteLLMUsage

        mock_response = ModelResponse(
            id="stream",
            model=model,
            choices=[
                {
                    "message": {"content": content},
                    "index": 0,
                    "finish_reason": "stop",
                }
            ],
            usage=LiteLLMUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
        )
        cost = float(
            litellm.completion_cost(completion_response=mock_response) or 0.0
        )
    except Exception:
        return 0.0
    return cost


class _LiteLLMJSONClient:
    def __init__(
        self,
        llm: str | Mapping[str, Any],
        *,
        temperature: float,
        json_schema_mode: bool,
        max_retries: int = 3,
        timeout_s: float = 360.0,
        streaming_enabled: bool = False,
        use_native_reasoning: bool = True,
        reasoning_effort: str | None = None,
        reasoning_display: str | None = None,
        retry_rate_limit_errors: bool = True,
    ) -> None:
        import warnings

        warnings.warn(
            "The litellm-backed client (use_native_llm=False) is "
            "deprecated and will be "
            "removed in a future release. Use the native LLM layer (default) "
            "or "
            "transport='pydantic-ai' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._llm = llm
        self._temperature = temperature
        self._json_schema_mode = json_schema_mode
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._streaming_enabled = streaming_enabled
        self._use_native_reasoning = use_native_reasoning
        self._reasoning_effort = reasoning_effort
        self._reasoning_display = reasoning_display
        self._retry_rate_limit_errors = retry_rate_limit_errors

    @property
    def reasoning_display(self) -> str | None:
        """The reasoning display mode sent to LiteLLM."""
        return self._reasoning_display

    @property
    def retry_rate_limit_errors(self) -> bool:
        """Whether rate-limit errors are retried in place."""
        return self._retry_rate_limit_errors

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        litellm = _import_litellm()
        params, model_name, profile = self._base_params(messages)
        self._apply_reasoning_params(params, model_name, profile)
        if self._json_schema_mode and response_format is not None:
            params["response_format"] = _litellm_response_format(
                response_format, model_name
            )

        allow_streaming = (
            stream
            and self._streaming_enabled
            and (
                not isinstance(params.get("response_format"), Mapping)
                or params.get("response_format", {}).get("type")
                in (None, "json_object", "json_schema")
            )
        )

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with asyncio.timeout(self._timeout_s):
                    if allow_streaming and on_stream_chunk is not None:
                        return await self._complete_streaming(
                            litellm=litellm,
                            on_reasoning_chunk=on_reasoning_chunk,
                            on_stream_chunk=on_stream_chunk,
                            params=params,
                        )

                    return await self._complete_once(
                        attempt=attempt,
                        litellm=litellm,
                        on_reasoning_chunk=on_reasoning_chunk,
                        params=params,
                    )
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "llm_timeout",
                    extra={
                        "attempt": attempt + 1,
                        "timeout_s": self._timeout_s,
                    },
                )
            except Exception as exc:
                last_error = exc
                if not await self._retry_after(exc, attempt):
                    raise

        logger.error(
            "llm_retries_exhausted",
            extra={
                "max_retries": self._max_retries,
                "last_error": str(last_error),
            },
        )
        msg = f"LLM call failed after {self._max_retries} retries"
        raise RuntimeError(msg) from last_error

    def _base_params(
        self, messages: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[str, Any], str, ModelProfile]:
        """Return the request params, the model name and its profile."""
        params: dict[str, Any]
        if isinstance(self._llm, str):
            params = {"model": self._llm}
        else:
            params = dict(self._llm)
        model_name = (
            self._llm
            if isinstance(self._llm, str)
            else self._llm.get("model", "")
        )
        profile = get_profile(model_name)
        if not isinstance(self._llm, str):
            for param in profile.unsupported_request_params:
                params.pop(param, None)
            if not profile.supports_temperature:
                params.pop("temperature", None)
        if profile.supports_temperature:
            params.setdefault("temperature", self._temperature)
        params["messages"] = list(messages)
        return params, model_name, profile

    def _apply_reasoning_params(
        self, params: dict[str, Any], model_name: str, profile: ModelProfile
    ) -> None:
        """Request native reasoning when the model supports it.

        Only pass reasoning_effort if the model actually supports native
        reasoning. Some providers (e.g., Databricks) crash during parameter
        mapping if reasoning_effort is passed to non-reasoning models, even
        with drop_params=True.
        """
        if not (
            self._use_native_reasoning
            and self._reasoning_effort is not None
            and (
                _supports_reasoning(model_name)
                or profile.reasoning_request_style == "adaptive_effort"
            )
        ):
            return
        if profile.reasoning_request_style == "adaptive_effort":
            params.update(self._adaptive_thinking_params(profile))
        else:
            params["reasoning_effort"] = self._reasoning_effort
        # Providers vary in support; drop unsupported params instead of
        # failing.
        params.setdefault("drop_params", True)

    def _adaptive_thinking_params(
        self, profile: ModelProfile
    ) -> dict[str, Any]:
        """Return the ``thinking`` params of adaptive-effort models."""
        effort = str(self._reasoning_effort).strip().lower()
        if effort in ("none", "off", "disabled", "false", "0"):
            return {"thinking": {"type": "disabled"}}
        thinking: dict[str, Any] = {"type": "adaptive"}
        display = self._reasoning_display
        if display not in {"summarized", "omitted"}:
            display = profile.reasoning_display_default
        if display is not None:
            thinking["display"] = display
        return {"thinking": thinking, "output_config": {"effort": effort}}

    async def _retry_after(self, exc: Exception, attempt: int) -> bool:
        """Back off before retrying a rate-limited or unavailable call.

        Returns:
            Whether the call should be retried.

        Raises:
            LLMRateLimitError: On rate limits when they are not retried here.
        """
        error_type = exc.__class__.__name__
        if (
            "RateLimit" not in error_type
            and "ServiceUnavailable" not in error_type
        ):
            return False
        if "RateLimit" in error_type and not self._retry_rate_limit_errors:
            raise LLMRateLimitError(
                message=str(exc),
                provider="litellm",
                status_code=429,
            ) from exc
        backoff_s = 2**attempt
        logger.warning(
            "llm_retry",
            extra={
                "attempt": attempt + 1,
                "error": str(exc),
                "backoff_s": backoff_s,
            },
        )
        if attempt >= self._max_retries - 1:
            return False
        await asyncio.sleep(backoff_s)
        return True

    async def _complete_streaming(
        self,
        *,
        litellm: ModuleType,
        on_reasoning_chunk: Callable[[str, bool], None] | None,
        on_stream_chunk: Callable[[str, bool], None],
        params: dict[str, Any],
    ) -> tuple[str, float]:
        """Run one streaming completion, forwarding chunks to the callbacks."""
        stream_params = dict(params)
        stream_params["stream"] = True
        stream_opts = dict(stream_params.get("stream_options") or {})
        stream_opts.setdefault("include_usage", True)
        stream_params["stream_options"] = stream_opts

        response = await litellm.acompletion(**stream_params)
        pieces: list[str] = []
        usage_payload: Mapping[str, Any] | None = None

        async for chunk in response:
            chunk_usage = (
                chunk.get("usage")
                if isinstance(chunk, Mapping)
                else getattr(chunk, "usage", None)
            )
            if chunk_usage:
                usage_payload = chunk_usage
            delta = _chunk_delta(chunk)
            delta_content = _delta_field(delta, "content")
            if delta_content:
                pieces.append(str(delta_content))
                _notify(
                    on_stream_chunk,
                    str(delta_content),
                    done=False,
                    error_event="llm_stream_chunk_callback_error",
                )
            if on_reasoning_chunk is None:
                continue
            delta_reasoning = _delta_field(
                delta, "reasoning_content", "reasoning", "thinking"
            )
            if delta_reasoning:
                _notify(
                    on_reasoning_chunk,
                    str(delta_reasoning),
                    done=False,
                    error_event="llm_reasoning_chunk_callback_error",
                )

        _notify(
            on_stream_chunk,
            "",
            done=True,
            error_event="llm_stream_chunk_callback_error",
        )
        if on_reasoning_chunk is not None:
            _notify(
                on_reasoning_chunk,
                "",
                done=True,
                error_event="llm_reasoning_chunk_callback_error",
            )

        content = "".join(pieces)
        cost = (
            _stream_cost(
                litellm,
                model=stream_params.get("model", ""),
                content=content,
                usage=usage_payload,
            )
            if usage_payload
            else 0.0
        )
        return content, cost

    async def _complete_once(
        self,
        *,
        attempt: int,
        litellm: ModuleType,
        on_reasoning_chunk: Callable[[str, bool], None] | None,
        params: dict[str, Any],
    ) -> tuple[str, float]:
        """Run one non-streaming completion and return its text and cost."""
        response = await litellm.acompletion(**params)
        content_text: str | None = None
        reasoning_content: str | None = None
        if isinstance(response, Mapping):
            choice = response.get("choices", [{}])[0]
            message = (
                choice.get("message", {})
                if isinstance(choice, Mapping)
                else {}
            )
            content_val = message.get("content")
            content_text = (
                content_val if isinstance(content_val, str) else None
            )
            reasoning_val = message.get("reasoning_content")
            reasoning_content = (
                reasoning_val if isinstance(reasoning_val, str) else None
            )
        else:  # pragma: no cover - defensive for non-mapping clients
            content_val = getattr(response.choices[0].message, "content", None)
            content_text = (
                content_val if isinstance(content_val, str) else None
            )
            reasoning_val = getattr(
                response.choices[0].message,
                "reasoning_content",
                None,
            )
            reasoning_content = (
                reasoning_val if isinstance(reasoning_val, str) else None
            )

        if content_text is None:
            _log_empty_content(
                content_val=content_val,
                reasoning_content=reasoning_content,
                response=response,
            )
            msg = "LiteLLM returned empty content"
            raise RuntimeError(msg)

        if on_reasoning_chunk is not None and reasoning_content:
            try:
                on_reasoning_chunk(str(reasoning_content), False)
                on_reasoning_chunk("", True)
            except Exception:
                logger.exception("llm_reasoning_chunk_callback_error")

        cost = (
            float(
                response.get("_hidden_params", {}).get("response_cost", 0.0)
                or 0.0
            )
            if isinstance(response, Mapping)
            else 0.0
        )
        logger.debug(
            "llm_call_success",
            extra={
                "attempt": attempt + 1,
                "cost_usd": cost,
                "tokens": (
                    response.get("usage", {}).get("total_tokens", 0)
                    if isinstance(response, Mapping)
                    else 0
                ),
            },
        )

        return content_text, cost


def _first_choice(response: object) -> object:
    """Return the first ``choices`` entry of an LLM response, if any."""
    if isinstance(response, Mapping):
        return response.get("choices", [{}])[0]
    choices = getattr(response, "choices", None)
    return choices[0] if choices else None


def _field(obj: object, name: str, default: object = None) -> object:
    """Read ``name`` from a mapping key or an object attribute."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _describe_tool_calls(tool_calls: object) -> str:
    """Summarize tool calls for the empty-content diagnostics."""
    if not tool_calls:
        return "none"
    if not isinstance(tool_calls, list):
        return f"type={type(tool_calls).__name__}"
    function = _field(tool_calls[0], "function")
    name = _field(function, "name", "unknown") if function else "unknown"
    return f"list[{len(tool_calls)}] first={name}"


def _log_empty_content(
    *, content_val: object, reasoning_content: str | None, response: object
) -> None:
    """Log why a LiteLLM response came back without text content."""
    # DEBUG_REMOVE: Log full response details when content is empty. Check
    # for tool_calls which might explain why content is empty.
    is_mapping = isinstance(response, Mapping)
    choice = _first_choice(response)
    message = _field(choice, "message", {} if is_mapping else None)
    tool_calls = _field(message, "tool_calls") if message else None
    message_keys = (
        list(message.keys())
        if isinstance(message, Mapping)
        else (dir(message)[:10] if message else "none")
    )
    logger.warning(
        "DEBUG_llm_empty_content",
        extra={
            "response_type": type(response).__name__,
            "response_keys": list(response.keys())
            if isinstance(response, Mapping)
            else "not_mapping",
            "choices_count": len(response.get("choices", []))
            if isinstance(response, Mapping)
            else "n/a",
            "message_keys": message_keys,
            "content_val_type": type(content_val).__name__
            if content_val is not None
            else "None",
            "content_val_repr": repr(content_val)[:200]
            if content_val is not None
            else "None",
            "reasoning_content": reasoning_content[:200]
            if reasoning_content
            else "None",
            "finish_reason": _field(
                choice,
                "finish_reason",
                None if isinstance(choice, Mapping) else "n/a",
            ),
            "tool_calls": _describe_tool_calls(tool_calls),
        },
    )


def supports_callback_streaming(
    client: object,
) -> TypeGuard[ReasoningJSONLLMClient]:
    """Whether a planner client honors the callback-based streaming contract.

    Returns True for the native adapter, the LiteLLM JSON client, and the
    fallback wrappers that delegate to either of those. Including the wrappers
    means enabling ``llm_fallback`` no longer silently disables streaming /
    reasoning callbacks. (A 429 *after* output has already streamed cannot be
    transparently retried — the wrapper raises so the planner retries the step
    on a fresh model; mid-stream output is not replayed.)
    """
    from penguiflow.llm.fallback import (
        FallbackLLMClient,
        GenericFallbackLLMClient,
    )
    from penguiflow.llm.protocol import NativeLLMAdapter

    return isinstance(
        client,
        (
            _LiteLLMJSONClient,
            NativeLLMAdapter,
            FallbackLLMClient,
            GenericFallbackLLMClient,
        ),
    )


def _estimate_size(messages: Sequence[Mapping[str, Any]]) -> int:
    """Estimate token count for messages."""
    total_chars = 0
    for item in messages:
        content = item.get("content", "")
        role = item.get("role", "")
        total_chars += (
            len(content) if isinstance(content, str) else len(str(content))
        )
        total_chars += len(role) + 20
    estimated_tokens = int(total_chars / 3.5)
    logger.debug(
        "token_estimate",
        extra={"chars": total_chars, "estimated_tokens": estimated_tokens},
    )
    return estimated_tokens


async def build_messages(
    planner: ReactPlanner, trajectory: Trajectory
) -> list[dict[str, Any]]:
    """Build the chat messages for the planner's next LLM call."""
    conversation_memory, external_memory, llm_context, proactive_report = (
        _split_llm_context(trajectory)
    )

    system_prompt = _with_repair_guidance(planner)
    system_prompt = _with_run_guidance(
        trajectory,
        system_prompt=system_prompt,
        proactive_report=proactive_report,
    )

    messages = _base_messages(
        trajectory,
        conversation_memory=conversation_memory,
        external_memory=external_memory,
        llm_context=llm_context,
        system_prompt=system_prompt,
    )

    history_messages = _history_messages(planner, trajectory)

    if planner.token_budget is None:
        return messages + history_messages

    return await _condensed_messages(
        planner,
        trajectory,
        history_messages=history_messages,
        messages=messages,
        token_budget=planner.token_budget,
    )


def _split_llm_context(
    trajectory: Trajectory,
) -> tuple[object, object, Mapping[str, Any] | None, object]:
    """Separate memories and background results from the llm_context."""
    llm_context = trajectory.llm_context
    conversation_memory = None
    external_memory = None
    proactive_report = None
    if isinstance(llm_context, Mapping):
        if "conversation_memory" in llm_context:
            conversation_memory = llm_context.get("conversation_memory")
            llm_context = {
                k: v
                for k, v in llm_context.items()
                if k != "conversation_memory"
            }
        if "external_memory" in llm_context:
            external_memory = llm_context.get("external_memory")
            llm_context = {
                k: v for k, v in llm_context.items() if k != "external_memory"
            }
        if "proactive_report" in llm_context:
            proactive_report = llm_context.get("proactive_report")
        cleaned, extracted = extract_background_results(llm_context)
        if extracted:
            for task_id, result in extracted.items():
                if task_id not in trajectory.background_results:
                    trajectory.background_results[task_id] = result
        llm_context = cleaned or {}
    return conversation_memory, external_memory, llm_context, proactive_report


def _with_repair_guidance(planner: ReactPlanner) -> str:
    """Add tiered repair guidance learned from earlier runs to the prompt."""
    # Build system prompt with optional guidance
    system_prompt = planner.system_prompt

    # Inject finish_repair guidance if model has needed repair in past runs
    # Uses planner's internal tracking (persists across runs, no orchestrator
    # wiring needed)
    finish_repair_count = planner.finish_repair_history_count
    arg_fill_count = planner.arg_fill_repair_history_count
    multi_action_count = int(getattr(planner, "multi_action_history_count", 0))
    render_component_count = int(
        getattr(planner, "render_component_failure_history_count", 0)
    )

    logger.debug(
        "build_messages_repair_counts",
        extra={
            "finish_repair_history_count": finish_repair_count,
            "arg_fill_history_count": arg_fill_count,
            "multi_action_history_count": multi_action_count,
            "render_component_failure_history_count": render_component_count,
        },
    )

    finish_guidance = prompts.render_finish_guidance(finish_repair_count)
    if finish_guidance is not None:
        tier = (
            "critical"
            if finish_repair_count >= 3
            else ("warning" if finish_repair_count >= 2 else "reminder")
        )
        logger.info(
            "injecting_finish_guidance",
            extra={"tier": tier, "count": finish_repair_count},
        )
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                finish_guidance,
            )
            or system_prompt
        )

    # Inject arg_fill guidance if model has repeatedly failed to provide valid
    # args
    arg_fill_guidance = prompts.render_arg_fill_guidance(arg_fill_count)
    if arg_fill_guidance is not None:
        tier = (
            "critical"
            if arg_fill_count >= 3
            else ("warning" if arg_fill_count >= 2 else "reminder")
        )
        logger.info(
            "injecting_arg_fill_guidance",
            extra={"tier": tier, "count": arg_fill_count},
        )
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                arg_fill_guidance,
            )
            or system_prompt
        )

    multi_action_guidance = prompts.render_multi_action_guidance(
        multi_action_count
    )
    if multi_action_guidance is not None:
        tier = (
            "critical"
            if multi_action_count >= 3
            else ("warning" if multi_action_count >= 2 else "reminder")
        )
        logger.info(
            "injecting_multi_action_guidance",
            extra={"tier": tier, "count": multi_action_count},
        )
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                multi_action_guidance,
            )
            or system_prompt
        )

    render_component_guidance = prompts.render_render_component_guidance(
        render_component_count
    )
    if render_component_guidance is not None:
        tier = (
            "critical"
            if render_component_count >= 3
            else ("warning" if render_component_count >= 2 else "reminder")
        )
        logger.info(
            "injecting_render_component_guidance",
            extra={"tier": tier, "count": render_component_count},
        )
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                render_component_guidance,
            )
            or system_prompt
        )
    return system_prompt


def _with_run_guidance(
    trajectory: Trajectory, *, system_prompt: str, proactive_report: object
) -> str:
    """Add proactive-report, tool and skill guidance to the system prompt."""
    if proactive_report is not None:
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                prompts.render_proactive_report_guidance(),
            )
            or system_prompt
        )

    skills_directory = (
        trajectory.metadata.get("skills_directory")
        if isinstance(trajectory.metadata, Mapping)
        else None
    )
    skills_context = (
        trajectory.metadata.get("skills_context")
        if isinstance(trajectory.metadata, Mapping)
        else None
    )
    tools_directory = (
        trajectory.metadata.get("tools_directory")
        if isinstance(trajectory.metadata, Mapping)
        else None
    )
    tool_hints = (
        trajectory.metadata.get("tool_hints")
        if isinstance(trajectory.metadata, Mapping)
        else None
    )

    if tools_directory or tool_hints:
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                tools_directory if isinstance(tools_directory, str) else None,
                tool_hints if isinstance(tool_hints, str) else None,
            )
            or system_prompt
        )
    if skills_directory or skills_context:
        system_prompt = (
            prompts.merge_prompt_extras(
                system_prompt,
                skills_directory
                if isinstance(skills_directory, str)
                else None,
                skills_context if isinstance(skills_context, str) else None,
            )
            or system_prompt
        )
    return system_prompt


def _base_messages(
    trajectory: Trajectory,
    *,
    conversation_memory: object,
    external_memory: object,
    llm_context: Mapping[str, Any] | None,
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Build the system, memory and user messages that open the prompt."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    if external_memory is not None:
        messages.append(
            {
                "role": "system",
                "content": prompts.render_read_only_external_memory(
                    external_memory
                ),
            }
        )
    if conversation_memory is not None:
        messages.append(
            {
                "role": "system",
                "content": prompts.render_read_only_conversation_memory(
                    conversation_memory
                ),
            }
        )
    user_prompt = prompts.build_user_prompt(
        trajectory.query,
        llm_context,
    )
    user_content: Any = user_prompt
    if trajectory.input_parts:
        user_content = [TextPart(text=user_prompt), *trajectory.input_parts]
    messages.append({"role": "user", "content": user_content})
    return messages


def _history_messages(
    planner: ReactPlanner, trajectory: Trajectory
) -> list[dict[str, Any]]:
    """Render the trajectory steps (and pending user inputs) as messages."""
    history_messages: list[dict[str, Any]] = []
    for step in trajectory.steps:
        action_for_llm = {
            "next_node": step.action.next_node,
            "args": dict(step.action.args),
        }
        action_payload = json.dumps(
            action_for_llm,
            ensure_ascii=False,
            sort_keys=True,
        )
        history_messages.append(
            {"role": "assistant", "content": action_payload}
        )
        observation_payload = step.serialise_for_llm()
        if (
            step.llm_observation is None
            and step.action.is_tool_call()
            and isinstance(observation_payload, Mapping)
        ):
            spec = getattr(planner, "spec_by_name", {}).get(
                step.action.next_node
            )
            if spec is not None:
                observation_payload = _redact_artifacts(
                    spec.out_model, observation_payload
                )

        history_messages.append(
            {
                "role": "user",
                "content": prompts.render_observation(
                    observation=observation_payload,
                    error=step.error,
                    failure=step.failure,
                ),
            }
        )

    if trajectory.steering_inputs:
        for payload in trajectory.steering_inputs:
            history_messages.append(
                {
                    "role": "user",
                    "content": prompts.render_steering_input(payload),
                }
            )

    if trajectory.resume_user_input:
        history_messages.append(
            {
                "role": "user",
                "content": prompts.render_resume_user_input(
                    trajectory.resume_user_input
                ),
            }
        )
    return history_messages


async def _condensed_messages(
    planner: ReactPlanner,
    trajectory: Trajectory,
    *,
    history_messages: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    token_budget: int,
) -> list[dict[str, Any]]:
    """Fit the history in the token budget, summarizing older steps."""
    candidate = messages + history_messages
    if _estimate_size(candidate) <= token_budget:
        return candidate

    summary = await summarise_trajectory(planner, trajectory)
    summary_message = {
        "role": "system",
        "content": prompts.render_summary(summary.compact()),
    }
    condensed: list[dict[str, Any]] = [*messages, summary_message]
    if trajectory.steps:
        last_step = trajectory.steps[-1]
        last_action_for_llm = {
            "next_node": last_step.action.next_node,
            "args": dict(last_step.action.args),
        }
        condensed.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    last_action_for_llm,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
        last_observation_payload = last_step.serialise_for_llm()
        if (
            last_step.llm_observation is None
            and last_step.action.is_tool_call()
            and isinstance(last_observation_payload, Mapping)
        ):
            spec = getattr(planner, "spec_by_name", {}).get(
                last_step.action.next_node
            )
            if spec is not None:
                last_observation_payload = _redact_artifacts(
                    spec.out_model, last_observation_payload
                )

        condensed.append(
            {
                "role": "user",
                "content": prompts.render_observation(
                    observation=last_observation_payload,
                    error=last_step.error,
                    failure=last_step.failure,
                ),
            }
        )
    if trajectory.steering_inputs:
        for payload in trajectory.steering_inputs:
            condensed.append(
                {
                    "role": "user",
                    "content": prompts.render_steering_input(payload),
                }
            )
    if trajectory.resume_user_input:
        condensed.append(
            {
                "role": "user",
                "content": prompts.render_resume_user_input(
                    trajectory.resume_user_input
                ),
            }
        )
    return condensed


async def summarise_trajectory(
    planner: ReactPlanner, trajectory: Trajectory
) -> TrajectorySummary:
    """Summarize the trajectory, with the LLM when a summarizer is set."""
    if trajectory.summary is not None:
        return trajectory.summary

    base_summary = trajectory.compress()
    summary_text = prompts.render_summary(base_summary.compact())
    if (
        planner.summarizer_client is not None
        and planner.token_budget is not None
        and len(summary_text) > planner.token_budget
    ):
        messages = prompts.build_summarizer_messages(
            trajectory.query,
            trajectory.to_history(),
            base_summary.compact(),
        )
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "trajectory_summary",
                "schema": TrajectorySummary.model_json_schema(),
            },
        }
        try:
            llm_result = await planner.summarizer_client.complete(
                messages=messages,
                response_format=response_format,
            )
            raw, cost = _coerce_llm_response(llm_result)
            planner.cost_tracker.record_summarizer_call(cost)
            summary = TrajectorySummary.model_validate_json(raw)
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning(
                "summarizer_failed_fallback",
                extra={
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            base_summary.note = "rule_based_fallback"
        else:
            summary.note = summary.note or "llm"
            trajectory.summary = summary
            logger.debug("trajectory_summarized", extra={"method": "llm"})
            return summary
    trajectory.summary = base_summary
    logger.debug("trajectory_summarized", extra={"method": "rule_based"})
    return base_summary


async def critique_answer(
    planner: ReactPlanner,
    trajectory: Trajectory,
    candidate: object,
) -> ReflectionCritique:
    """Critique a candidate answer with the reflection LLM."""
    if planner.reflection_config is None:
        msg = "Reflection not configured"
        raise RuntimeError(msg)

    client = (
        planner.reflection_client
        if planner.reflection_config.use_separate_llm
        and planner.reflection_client is not None
        else planner.client
    )

    from . import reflection_prompts

    system_prompt = reflection_prompts.build_critique_system_prompt(
        planner.reflection_config.criteria
    )
    user_prompt = reflection_prompts.build_critique_user_prompt(
        trajectory.query,
        candidate,
        trajectory,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "reflection_critique",
            "schema": ReflectionCritique.model_json_schema(),
        },
    }

    llm_result = await client.complete(
        messages=messages, response_format=response_format
    )
    raw, cost = _coerce_llm_response(llm_result)
    planner.cost_tracker.record_reflection_call(cost)
    critique = ReflectionCritique.model_validate_json(raw)

    if (
        critique.score >= planner.reflection_config.quality_threshold
        and not critique.passed
    ):
        critique.passed = True

    return critique


async def request_revision(
    planner: ReactPlanner,
    trajectory: Trajectory,
    critique: ReflectionCritique,
    *,
    on_stream_chunk: Callable[[str, bool], None] | None = None,
) -> PlannerAction:
    """Ask the LLM to revise the answer from the critique."""
    from . import reflection_prompts

    base_messages = await build_messages(planner, trajectory)
    revision_prompt = reflection_prompts.build_revision_prompt(
        trajectory.steps[-1].action.thought if trajectory.steps else "",
        critique,
    )

    messages = list(base_messages)
    messages.append({"role": "user", "content": revision_prompt})

    # Enable streaming for revision if callback provided and client supports it
    stream_allowed = (
        on_stream_chunk is not None
        and planner.stream_final_response
        and supports_callback_streaming(planner.client)
    )

    llm_result = await planner.client.complete(
        messages=messages,
        response_format=planner.response_format,
        stream=stream_allowed,
        on_stream_chunk=on_stream_chunk if stream_allowed else None,
    )
    raw, cost = _coerce_llm_response(llm_result)
    planner.cost_tracker.record_main_call(cost)
    from .migration import normalize_action

    return normalize_action(raw)


async def generate_clarification(
    planner: ReactPlanner,
    trajectory: Trajectory,
    failed_answer: object,
    critique: ReflectionCritique,
    revision_attempts: int,
) -> str:
    """Ask the LLM to explain why the query cannot be answered well."""
    system_prompt = """You are a helpful assistant that is transparent about \
limitations.

When you cannot satisfactorily answer a query with available tools/data, you \
should:
1. Honestly explain what you tried and why it didn't fully address the query
2. Ask specific clarifying questions to better understand what the user needs
3. Suggest what additional information, tools, or context would help you \
provide a proper answer

Your goal is to guide the user toward providing what you need to answer their \
query properly."""

    attempted_tools = [
        step.action.next_node
        for step in trajectory.steps
        if step.action.is_tool_call()
    ]
    attempts_summary = (
        "\n".join([f"- {tool}" for tool in attempted_tools])
        if attempted_tools
        else "None recorded"
    )

    issues_text = (
        "\n".join(f"- {issue}" for issue in critique.issues)
        if critique.issues
        else "None specified"
    )
    user_prompt = f"""The query was: "{trajectory.query}"

I attempted to answer this query but the quality was deemed unsatisfactory \
(score: {critique.score:.2f}/1.0).

**What I tried:**
{attempts_summary}

**My attempted answer:**
{failed_answer}

**Quality feedback received:**
{critique.feedback}

**Issues identified:**
{issues_text}

Given this situation, generate a STRUCTURED clarification response with:
1. `text`: Honest explanation of limitations and what was tried
2. `confidence`: Set to "unsatisfied"
3. `attempted_approaches`: List of tools/approaches I tried
4. `clarifying_questions`: 2-4 specific questions to ask the user
5. `suggestions`: What would help me answer this properly (data sources, \
tools, context)
6. `reflection_score`: {critique.score}
7. `revision_attempts`: {revision_attempts}

Be transparent, helpful, and guide the user toward providing what's needed."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    from .dspy_client import DSPyLLMClient

    if isinstance(planner.client, DSPyLLMClient):
        if planner.clarification_client is None:
            logger.warning(
                "clarification_client_missing", extra={"client_type": "DSPy"}
            )
            planner.clarification_client = DSPyLLMClient.from_base_client(
                planner.client, ClarificationResponse
            )
        client: JSONLLMClient = planner.clarification_client
    else:
        client = planner.client

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "clarification_response",
            "schema": ClarificationResponse.model_json_schema(),
        },
    }

    llm_result = await client.complete(
        messages=messages,
        response_format=response_format,
    )
    raw, cost = _coerce_llm_response(llm_result)
    planner.cost_tracker.record_main_call(cost)

    clarification = ClarificationResponse.model_validate_json(raw)

    attempts_list = chr(10).join(
        [
            f"  {i + 1}. {approach}"
            for i, approach in enumerate(clarification.attempted_approaches)
        ]
    )
    questions_list = chr(10).join(
        [f"  - {q}" for q in clarification.clarifying_questions]
    )
    suggestions_list = chr(10).join(
        [f"  - {s}" for s in clarification.suggestions]
    )

    score_line = (
        f"[Confidence: {clarification.confidence} | "
        f"Quality Score: {clarification.reflection_score:.2f}/1.0 | "
        f"Revision Attempts: {clarification.revision_attempts}]"
    )

    return f"""{clarification.text}

**What I Tried:**
{attempts_list}

**To Help Me Answer This:**
{questions_list}

**Suggestions:**
{suggestions_list}

{score_line}"""


__all__ = [
    "JSONLLMClient",
    "LLMErrorType",
    "_LiteLLMJSONClient",
    "_coerce_llm_response",
    "_estimate_size",
    "_extract_json_from_text",
    "_redact_artifacts",
    "_sanitize_json_schema",
    "_supports_reasoning",
    "build_messages",
    "classify_llm_error",
    "critique_answer",
    "extract_clean_error_message",
    "generate_clarification",
    "request_revision",
    "summarise_trajectory",
]
