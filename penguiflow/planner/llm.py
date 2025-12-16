"""LLM client utilities and wrappers for planner."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from . import prompts
from .models import (
    ClarificationResponse,
    JSONLLMClient,
    PlannerAction,
    ReflectionCritique,
)
from .trajectory import Trajectory, TrajectorySummary

logger = logging.getLogger("penguiflow.planner")


def _extract_json_from_text(text: str) -> str:
    """Extract JSON object content from mixed text + fenced code blocks."""

    text = text.strip()
    fence_match = re.search(r"```(?:json)?\\s*(\\{.*?\\})\\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _coerce_llm_response(result: str | tuple[str, float]) -> tuple[str, float]:
    """Normalise JSON LLM client responses to ``(content, cost)`` tuples."""

    if isinstance(result, tuple):
        content, cost = result
        return _extract_json_from_text(content), float(cost)
    if isinstance(result, str):
        return _extract_json_from_text(result), 0.0
    msg = f"Expected JSONLLMClient to return a string or (string, float) tuple, received {type(result)!r}"
    raise TypeError(msg)


def _sanitize_json_schema(
    schema: dict[str, Any],
    *,
    strict_mode: bool = False,
    require_all_fields: bool = False,
    inline_defs: bool = False,
) -> dict[str, Any]:
    """Remove advanced JSON schema constraints for broader provider compatibility.

    Args:
        schema: The JSON schema to sanitize.
        strict_mode: If True, adds 'additionalProperties: false' to all object schemas
                     as required by OpenAI/OpenRouter structured outputs.
        require_all_fields: If True, marks all object properties as required (used for
                            providers that demand exhaustive required lists).
        inline_defs: If True, replaces local $defs references with inline schemas to
                     satisfy providers that cannot resolve $refs in response_format.
    """

    if not isinstance(schema, dict):
        return schema

    sanitized: dict[str, Any] = {}
    unsupported_constraints = {
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

    for key, value in schema.items():
        if key in unsupported_constraints:
            continue

        if key == "properties" and isinstance(value, dict):
            sanitized[key] = {
                prop_name: _sanitize_json_schema(
                    prop_schema,
                    strict_mode=strict_mode,
                    require_all_fields=require_all_fields,
                )
                for prop_name, prop_schema in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            sanitized[key] = _sanitize_json_schema(
                value,
                strict_mode=strict_mode,
                require_all_fields=require_all_fields,
            )
        elif key == "additionalProperties":
            # In strict mode, we need additionalProperties: false
            # If it's a dict schema (for typed additional props), skip sanitizing but note
            # that OpenAI strict mode doesn't support typed additionalProperties
            if strict_mode:
                # For strict mode, additionalProperties must be false (not true or a schema)
                # We'll handle this after the loop
                continue
            elif isinstance(value, dict):
                sanitized[key] = _sanitize_json_schema(value, strict_mode=strict_mode)
            else:
                sanitized[key] = value
        elif key == "allOf" and isinstance(value, list):
            sanitized[key] = [
                _sanitize_json_schema(
                    item,
                    strict_mode=strict_mode,
                    require_all_fields=require_all_fields,
                )
                for item in value
            ]
        elif key == "anyOf" and isinstance(value, list):
            sanitized[key] = [
                _sanitize_json_schema(
                    item,
                    strict_mode=strict_mode,
                    require_all_fields=require_all_fields,
                )
                for item in value
            ]
        elif key == "oneOf" and isinstance(value, list):
            sanitized[key] = [
                _sanitize_json_schema(
                    item,
                    strict_mode=strict_mode,
                    require_all_fields=require_all_fields,
                )
                for item in value
            ]
        elif key == "$defs" and isinstance(value, dict):
            # Process nested definitions (used by Pydantic for referenced models)
            sanitized[key] = {
                def_name: _sanitize_json_schema(
                    def_schema,
                    strict_mode=strict_mode,
                    require_all_fields=require_all_fields,
                )
                for def_name, def_schema in value.items()
            }
        else:
            sanitized[key] = value

    # Add additionalProperties: false for object schemas in strict mode
    # This is required by OpenAI/OpenRouter structured outputs API
    if strict_mode:
        is_object_schema = sanitized.get("type") == "object" or "properties" in sanitized
        if is_object_schema:
            sanitized["additionalProperties"] = False
            properties = sanitized.get("properties", {}) or {}
            if properties and require_all_fields:
                required_keys = set(sanitized.get("required", []))
                required_keys.update(properties.keys())
                if required_keys:
                    sanitized["required"] = sorted(required_keys)

    if inline_defs:
        sanitized = _inline_defs(sanitized, sanitized.get("$defs", {}))

    return sanitized


def _build_minimal_planner_schema() -> dict[str, Any]:
    """OpenAI-strict-friendly PlannerAction schema without $refs or advanced features."""

    join_injection = {
        "type": "object",
        "properties": {
            "mapping": {
                "type": "object",
                "additionalProperties": False,
            },
        },
        "required": ["mapping"],
        "additionalProperties": False,
    }

    join_obj = {
        "type": "object",
        "properties": {
            "node": {"type": "string"},
            "args": {"type": "object", "additionalProperties": False},
            "inject": {"anyOf": [join_injection, {"type": "null"}], "default": None},
        },
        "required": ["node", "args", "inject"],
        "additionalProperties": False,
    }

    parallel_call = {
        "type": "object",
        "properties": {
            "node": {"type": "string"},
            "args": {"type": "object", "additionalProperties": False},
        },
        "required": ["node", "args"],
        "additionalProperties": False,
    }

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "next_node": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "args": {
                "anyOf": [{"type": "object", "additionalProperties": False}, {"type": "null"}],
                "default": None,
            },
            "plan": {
                "anyOf": [
                    {
                        "type": "array",
                        "items": parallel_call,
                    },
                    {"type": "null"},
                ],
                "default": None,
            },
            "join": {"anyOf": [join_obj, {"type": "null"}], "default": None},
        },
        "required": ["thought", "next_node", "args", "plan", "join"],
        "additionalProperties": False,
    }
    return schema


def _inline_defs(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Inline local $defs references (limited scope for response_format)."""

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref = node["$ref"]
                if ref.startswith("#/$defs/"):
                    key = ref.split("/")[-1]
                    if key in defs:
                        return resolve(deepcopy(defs[key]))
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    inlined = resolve(schema)
    if isinstance(inlined, dict):
        inlined.pop("$defs", None)
    return inlined


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
        or (("gpt-" in lower or "o1" in lower or "o3" in lower) and "openrouter" in lower)
    ) or model_name.startswith("openai/")

    if weak_schema_models:
        return "json_object"
    if openai_like:
        return "json_object"
    return "sanitized_schema"


def _artifact_placeholder(value: Any) -> str:
    """Create a compact placeholder for artifact fields."""

    type_name = type(value).__name__
    size_hint: str | None = None
    try:
        if isinstance(value, (str, bytes, bytearray, Sequence)) and not isinstance(value, Mapping):
            size_hint = str(len(value))
        elif isinstance(value, Mapping):
            size_hint = str(len(value))
    except Exception:
        size_hint = None
    return f"<artifact:{type_name}>" if size_hint is None else f"<artifact:{type_name} size={size_hint}>"


def _unwrap_model(annotation: Any) -> type[BaseModel] | None:
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
) -> Any:
    """Redact artifact-marked fields from an observation for LLM context."""

    if observation is None:
        return {}
    if not isinstance(observation, Mapping):
        return observation

    redacted: dict[str, Any] = {}
    fields = getattr(out_model, "model_fields", {}) or {}

    for field_name, value in observation.items():
        field_info = fields.get(field_name)
        extra = field_info.json_schema_extra or {} if field_info is not None else {}
        if extra.get("artifact"):
            redacted[field_name] = _artifact_placeholder(value)
            continue

        nested_model: type[BaseModel] | None = None
        if field_info is not None:
            nested_model = _unwrap_model(field_info.annotation)

        if nested_model and isinstance(value, Mapping):
            redacted[field_name] = _redact_artifacts(nested_model, value)
            continue
        if nested_model and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            redacted[field_name] = [
                _redact_artifacts(nested_model, item) if isinstance(item, Mapping) else item for item in value
            ]
            continue

        redacted[field_name] = value

    return redacted


class _LiteLLMJSONClient:
    def __init__(
        self,
        llm: str | Mapping[str, Any],
        *,
        temperature: float,
        json_schema_mode: bool,
        max_retries: int = 3,
        timeout_s: float = 60.0,
        streaming_enabled: bool = False,
    ) -> None:
        self._llm = llm
        self._temperature = temperature
        self._json_schema_mode = json_schema_mode
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._streaming_enabled = streaming_enabled

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        try:
            import litellm
        except ModuleNotFoundError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "LiteLLM is not installed. Install penguiflow[planner] or provide a custom llm_client."
            ) from exc

        params: dict[str, Any]
        if isinstance(self._llm, str):
            params = {"model": self._llm}
        else:
            params = dict(self._llm)
        params.setdefault("temperature", self._temperature)
        params["messages"] = list(messages)
        if self._json_schema_mode and response_format is not None:
            model_name = self._llm if isinstance(self._llm, str) else self._llm.get("model", "")
            policy = _response_format_policy(model_name)

            if "json_schema" in response_format:
                schema_payload = response_format["json_schema"]
                sanitized_format = dict(response_format)

                if policy == "no_format":
                    params["response_format"] = {"type": "text"}
                    logger.debug(
                        "json_schema_disabled",
                        extra={
                            "model": model_name,
                            "reason": "no_format_policy",
                        },
                    )
                elif policy == "json_object":
                    params["response_format"] = {"type": "json_object"}
                    logger.debug(
                        "json_schema_downgraded",
                        extra={
                            "model": model_name,
                            "reason": "json_object_policy",
                            "fallback": "json_object",
                        },
                    )
                elif policy == "strict_schema":
                    sanitized_schema = _build_minimal_planner_schema()
                    sanitized_format["json_schema"] = {
                        "name": schema_payload["name"],
                        "strict": True,
                        "schema": sanitized_schema,
                    }
                    params["response_format"] = sanitized_format
                    logger.debug(
                        "json_schema_strict",
                        extra={"model": model_name, "strict_mode": True},
                    )
                else:
                    sanitized_schema = _sanitize_json_schema(
                        schema_payload["schema"],
                        strict_mode=True,
                        require_all_fields=False,
                        inline_defs=False,
                    )
                    sanitized_format["json_schema"] = {
                        "name": schema_payload["name"],
                        "schema": sanitized_schema,
                    }
                    params["response_format"] = sanitized_format
                    logger.debug(
                        "json_schema_sanitized",
                        extra={
                            "model": model_name,
                            "strict_mode": False,
                            "schema_sanitized": True,
                        },
                    )
            else:
                params["response_format"] = response_format

        allow_streaming = (
            stream
            and self._streaming_enabled
            and (
                not isinstance(params.get("response_format"), Mapping)
                or params.get("response_format", {}).get("type") in (None, "json_object", "json_schema")
            )
        )

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                async with asyncio.timeout(self._timeout_s):
                    if allow_streaming and on_stream_chunk is not None:
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

                            delta_content: str | None = None
                            if isinstance(chunk, Mapping):
                                choices = chunk.get("choices") or []
                                if choices:
                                    delta = choices[0].get("delta") or {}
                                    delta_content = delta.get("content")
                            else:
                                try:
                                    delta = getattr(getattr(chunk, "choices", [])[0], "delta", None)
                                    if delta is not None:
                                        delta_content = getattr(delta, "content", None)
                                except Exception:
                                    delta_content = None

                            if delta_content:
                                pieces.append(delta_content)
                                try:
                                    on_stream_chunk(delta_content, False)
                                except Exception:
                                    logger.exception("llm_stream_chunk_callback_error")

                        try:
                            on_stream_chunk("", True)
                        except Exception:
                            logger.exception("llm_stream_chunk_callback_error")

                        content = "".join(pieces)
                        cost = 0.0
                        if usage_payload:
                            try:
                                cost = float(
                                    litellm.completion_cost(
                                        model=stream_params.get("model", ""),
                                        prompt_tokens=usage_payload.get("prompt_tokens", 0),
                                        completion_tokens=usage_payload.get("completion_tokens", 0),
                                    )
                                    or 0.0
                                )
                            except Exception:
                                cost = 0.0

                        return content, cost

                    response = await litellm.acompletion(**params)
                    choice = response["choices"][0]
                    content = choice["message"]["content"]
                    if content is None:
                        raise RuntimeError("LiteLLM returned empty content")

                    cost = float(response.get("_hidden_params", {}).get("response_cost", 0.0) or 0.0)
                    logger.debug(
                        "llm_call_success",
                        extra={
                            "attempt": attempt + 1,
                            "cost_usd": cost,
                            "tokens": response.get("usage", {}).get("total_tokens", 0),
                        },
                    )

                    return content, cost
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "llm_timeout",
                    extra={"attempt": attempt + 1, "timeout_s": self._timeout_s},
                )
            except Exception as exc:
                last_error = exc
                error_type = exc.__class__.__name__
                if "RateLimit" in error_type or "ServiceUnavailable" in error_type:
                    backoff_s = 2**attempt
                    logger.warning(
                        "llm_retry",
                        extra={
                            "attempt": attempt + 1,
                            "error": str(exc),
                            "backoff_s": backoff_s,
                        },
                    )
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(backoff_s)
                        continue
                raise

        logger.error(
            "llm_retries_exhausted",
            extra={"max_retries": self._max_retries, "last_error": str(last_error)},
        )
        msg = f"LLM call failed after {self._max_retries} retries"
        raise RuntimeError(msg) from last_error


def _estimate_size(messages: Sequence[Mapping[str, str]]) -> int:
    """Estimate token count for messages."""

    total_chars = 0
    for item in messages:
        content = item.get("content", "")
        role = item.get("role", "")
        total_chars += len(content)
        total_chars += len(role) + 20
    estimated_tokens = int(total_chars / 3.5)
    logger.debug(
        "token_estimate",
        extra={"chars": total_chars, "estimated_tokens": estimated_tokens},
    )
    return estimated_tokens


async def build_messages(planner: Any, trajectory: Trajectory) -> list[dict[str, str]]:
    llm_context = trajectory.llm_context
    conversation_memory = None
    if isinstance(llm_context, Mapping) and "conversation_memory" in llm_context:
        conversation_memory = llm_context.get("conversation_memory")
        llm_context = {k: v for k, v in llm_context.items() if k != "conversation_memory"}

    messages: list[dict[str, str]] = [
        {"role": "system", "content": planner._system_prompt},
    ]
    if conversation_memory is not None:
        messages.append(
            {
                "role": "system",
                "content": prompts.render_read_only_conversation_memory(conversation_memory),
            }
        )
    messages.extend(
        [
        {
            "role": "user",
            "content": prompts.build_user_prompt(
                trajectory.query,
                llm_context,
            ),
        },
        ]
    )

    history_messages: list[dict[str, str]] = []
    for step in trajectory.steps:
        action_payload = json.dumps(
            step.action.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
        history_messages.append({"role": "assistant", "content": action_payload})
        observation_payload = step.serialise_for_llm()
        if step.llm_observation is None and step.action.next_node and isinstance(observation_payload, Mapping):
            spec = getattr(planner, "_spec_by_name", {}).get(step.action.next_node)
            if spec is not None:
                observation_payload = _redact_artifacts(spec.out_model, observation_payload)

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

    if trajectory.resume_user_input:
        history_messages.append(
            {
                "role": "user",
                "content": prompts.render_resume_user_input(trajectory.resume_user_input),
            }
        )

    if planner._token_budget is None:
        return messages + history_messages

    candidate = messages + history_messages
    if _estimate_size(candidate) <= planner._token_budget:
        return candidate

    summary = await summarise_trajectory(planner, trajectory)
    summary_message = {
        "role": "system",
        "content": prompts.render_summary(summary.compact()),
    }
    condensed: list[dict[str, str]] = messages + [summary_message]
    if trajectory.steps:
        last_step = trajectory.steps[-1]
        condensed.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    last_step.action.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
        last_observation_payload = last_step.serialise_for_llm()
        if (
            last_step.llm_observation is None
            and last_step.action.next_node
            and isinstance(last_observation_payload, Mapping)
        ):
            spec = getattr(planner, "_spec_by_name", {}).get(last_step.action.next_node)
            if spec is not None:
                last_observation_payload = _redact_artifacts(spec.out_model, last_observation_payload)

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
    if trajectory.resume_user_input:
        condensed.append(
            {
                "role": "user",
                "content": prompts.render_resume_user_input(trajectory.resume_user_input),
            }
        )
    return condensed


async def summarise_trajectory(planner: Any, trajectory: Trajectory) -> TrajectorySummary:
    if trajectory.summary is not None:
        return trajectory.summary

    base_summary = trajectory.compress()
    summary_text = prompts.render_summary(base_summary.compact())
    if (
        planner._summarizer_client is not None
        and planner._token_budget is not None
        and len(summary_text) > planner._token_budget
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
            llm_result = await planner._summarizer_client.complete(
                messages=messages,
                response_format=response_format,
            )
            raw, cost = _coerce_llm_response(llm_result)
            planner._cost_tracker.record_summarizer_call(cost)
            summary = TrajectorySummary.model_validate_json(raw)
            summary.note = summary.note or "llm"
            trajectory.summary = summary
            logger.debug("trajectory_summarized", extra={"method": "llm"})
            return summary
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning(
                "summarizer_failed_fallback",
                extra={"error": str(exc), "error_type": exc.__class__.__name__},
            )
            base_summary.note = "rule_based_fallback"
    trajectory.summary = base_summary
    logger.debug("trajectory_summarized", extra={"method": "rule_based"})
    return base_summary


async def critique_answer(
    planner: Any,
    trajectory: Trajectory,
    candidate: Any,
) -> ReflectionCritique:
    if planner._reflection_config is None:
        raise RuntimeError("Reflection not configured")

    client = (
        planner._reflection_client
        if planner._reflection_config.use_separate_llm and planner._reflection_client is not None
        else planner._client
    )
    if client is None:
        raise RuntimeError("Reflection client unavailable")

    from . import reflection_prompts

    system_prompt = reflection_prompts.build_critique_system_prompt(planner._reflection_config.criteria)
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

    llm_result = await client.complete(messages=messages, response_format=response_format)
    raw, cost = _coerce_llm_response(llm_result)
    planner._cost_tracker.record_reflection_call(cost)
    critique = ReflectionCritique.model_validate_json(raw)

    if critique.score >= planner._reflection_config.quality_threshold and not critique.passed:
        critique.passed = True

    return critique


async def request_revision(
    planner: Any,
    trajectory: Trajectory,
    critique: ReflectionCritique,
    *,
    on_stream_chunk: Callable[[str, bool], None] | None = None,
) -> PlannerAction:
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
        and planner._stream_final_response
        and isinstance(planner._client, _LiteLLMJSONClient)
    )

    llm_result = await planner._client.complete(
        messages=messages,
        response_format=planner._response_format,
        stream=stream_allowed,
        on_stream_chunk=on_stream_chunk if stream_allowed else None,
    )
    raw, cost = _coerce_llm_response(llm_result)
    planner._cost_tracker.record_main_call(cost)
    return PlannerAction.model_validate_json(raw)


async def generate_clarification(
    planner: Any,
    trajectory: Trajectory,
    failed_answer: str | dict[str, Any] | Any,
    critique: ReflectionCritique,
    revision_attempts: int,
) -> str:
    system_prompt = """You are a helpful assistant that is transparent about limitations.

When you cannot satisfactorily answer a query with available tools/data, you should:
1. Honestly explain what you tried and why it didn't fully address the query
2. Ask specific clarifying questions to better understand what the user needs
3. Suggest what additional information, tools, or context would help you provide a proper answer

Your goal is to guide the user toward providing what you need to answer their query properly."""

    attempted_tools = [step.action.next_node for step in trajectory.steps if step.action.next_node]
    attempts_summary = "\n".join([f"- {tool}" for tool in attempted_tools]) if attempted_tools else "None recorded"

    user_prompt = f"""The query was: "{trajectory.query}"

I attempted to answer this query but the quality was deemed unsatisfactory (score: {critique.score:.2f}/1.0).

**What I tried:**
{attempts_summary}

**My attempted answer:**
{failed_answer}

**Quality feedback received:**
{critique.feedback}

**Issues identified:**
{chr(10).join([f"- {issue}" for issue in critique.issues]) if critique.issues else "None specified"}

Given this situation, generate a STRUCTURED clarification response with:
1. `text`: Honest explanation of limitations and what was tried
2. `confidence`: Set to "unsatisfied"
3. `attempted_approaches`: List of tools/approaches I tried
4. `clarifying_questions`: 2-4 specific questions to ask the user
5. `suggestions`: What would help me answer this properly (data sources, tools, context)
6. `reflection_score`: {critique.score}
7. `revision_attempts`: {revision_attempts}

Be transparent, helpful, and guide the user toward providing what's needed."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    from .dspy_client import DSPyLLMClient

    if isinstance(planner._client, DSPyLLMClient):
        if planner._clarification_client is None:
            logger.warning("clarification_client_missing", extra={"client_type": "DSPy"})
            planner._clarification_client = DSPyLLMClient.from_base_client(planner._client, ClarificationResponse)
        client: JSONLLMClient = planner._clarification_client
    else:
        client = planner._client

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
    planner._cost_tracker.record_main_call(cost)

    clarification = ClarificationResponse.model_validate_json(raw)

    attempts_list = chr(10).join(
        [f"  {i + 1}. {approach}" for i, approach in enumerate(clarification.attempted_approaches)]
    )
    questions_list = chr(10).join([f"  - {q}" for q in clarification.clarifying_questions])
    suggestions_list = chr(10).join([f"  - {s}" for s in clarification.suggestions])

    score_line = (
        f"[Confidence: {clarification.confidence} | "
        f"Quality Score: {clarification.reflection_score:.2f}/1.0 | "
        f"Revision Attempts: {clarification.revision_attempts}]"
    )

    formatted_text = f"""{clarification.text}

**What I Tried:**
{attempts_list}

**To Help Me Answer This:**
{questions_list}

**Suggestions:**
{suggestions_list}

{score_line}"""

    return formatted_text


__all__ = [
    "JSONLLMClient",
    "_LiteLLMJSONClient",
    "_coerce_llm_response",
    "_sanitize_json_schema",
    "_estimate_size",
    "_redact_artifacts",
    "build_messages",
    "summarise_trajectory",
    "critique_answer",
    "request_revision",
    "generate_clarification",
]
