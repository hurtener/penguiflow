"""Protocol adapter for JSONLLMClient compatibility.

Provides a bridge between the new native LLM layer and the existing
JSONLLMClient protocol used by the planner.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from .errors import is_retryable
from .native_policy import StructuredMode, next_mode, resolve_policy
from .pricing import calculate_cost, get_pricing
from .providers import create_provider
from .types import (
    LLMMessage,
    LLMRequest,
    StreamCallback,
    StreamEvent,
    TextPart,
)

logger = logging.getLogger("penguiflow.llm.protocol")


def _normalize_json_schema(schema: Any) -> dict[str, Any]:
    """Normalize schema shape for stricter OpenAI-compatible validators.

    Some providers reject valid composition-only schemas like ``{"allOf": [...]}``
    unless the root also declares ``{"type": "object"}``.
    """

    if not isinstance(schema, Mapping):
        return {"type": "object"}

    normalized = dict(schema)
    if normalized.get("type") is None and any(
        key in normalized for key in ("properties", "required", "allOf", "anyOf", "oneOf", "if", "then", "else")
    ):
        normalized["type"] = "object"
    return normalized


def _is_invalid_json_schema_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "invalid_json_schema" in text
        or "invalid schema for response_format" in text
        or "response_format is invalid" in text
        or "output_format.schema" in text
        or "additionalproperties" in text
        or "must be text or json_object" in text
        or "response_format json_object is not supported" in text
    )


def _requested_mode(response_format: Mapping[str, Any] | None) -> StructuredMode:
    if not response_format:
        return "text"
    mode = str(response_format.get("type") or "text")
    if mode == "json_schema":
        return "json_schema"
    if mode == "json_object":
        return "json_object"
    return "text"


def _prepare_messages_for_provider(
    *,
    provider_name: str,
    model: str,
    messages: list[LLMMessage],
) -> list[LLMMessage]:
    """Apply provider-specific message normalization before request build."""
    if provider_name != "nim":
        return messages

    system_like_roles = {"system", "developer"}
    seen_non_system = False
    needs_reorder = False
    for msg in messages:
        role = str(msg.role).lower()
        if role in system_like_roles:
            if seen_non_system:
                needs_reorder = True
                break
        else:
            seen_non_system = True

    if not needs_reorder:
        return messages

    systems = [msg for msg in messages if str(msg.role).lower() in system_like_roles]
    others = [msg for msg in messages if str(msg.role).lower() not in system_like_roles]
    logger.warning(
        "native_reordered_system_messages",
        extra={
            "provider": provider_name,
            "model": model,
            "system_count": len(systems),
            "non_system_count": len(others),
        },
    )
    normalized = [*systems, *others]

    # Some NIM backends are sensitive to multiple system/developer messages.
    # Collapse them into a single leading system message.
    merged_system_texts: list[str] = []
    leading_system_count = 0
    for msg in normalized:
        if str(msg.role).lower() in system_like_roles:
            leading_system_count += 1
            text = msg.text.strip()
            if text:
                merged_system_texts.append(text)
            continue
        break

    if leading_system_count > 1:
        merged_content = "\n\n".join(merged_system_texts)
        merged = [
            LLMMessage(role="system", parts=(TextPart(text=merged_content),)),
            *normalized[leading_system_count:],
        ]
        logger.warning(
            "native_collapsed_system_messages",
            extra={
                "provider": provider_name,
                "model": model,
                "collapsed_count": leading_system_count,
            },
        )
        return merged

    return normalized


class NativeLLMAdapter:
    """Adapter that implements JSONLLMClient protocol using the native LLM layer.

    This class provides backward compatibility with the existing planner
    infrastructure while using the new native provider implementations.

    Example:
        from penguiflow.llm.protocol import NativeLLMAdapter

        # Create adapter that implements JSONLLMClient protocol
        client = NativeLLMAdapter("openai/gpt-4o")

        # Use with existing planner code
        result = await client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            response_format={"type": "json_object"},
        )
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_s: float = 120.0,
        json_schema_mode: bool = True,
        streaming_enabled: bool = True,
        use_native_reasoning: bool = True,
        reasoning_effort: str | None = None,
        **provider_kwargs: Any,
    ) -> None:
        """Initialize the adapter.

        Args:
            model: Model identifier (e.g., "gpt-4o", "claude-3-5-sonnet").
            api_key: API key (uses environment variable if not provided).
            base_url: Base URL override.
            temperature: Default temperature.
            max_retries: Maximum retry attempts.
            timeout_s: Request timeout in seconds.
            json_schema_mode: Enable JSON schema mode for structured output.
            streaming_enabled: Enable streaming support.
            use_native_reasoning: Enable native reasoning for supported models.
            reasoning_effort: Reasoning effort level (e.g., "low", "medium", "high").
            **provider_kwargs: Additional provider-specific configuration.
        """
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._json_schema_mode = json_schema_mode
        self._streaming_enabled = streaming_enabled
        self._use_native_reasoning = use_native_reasoning
        self._reasoning_effort = reasoning_effort

        # Create the underlying provider
        self._provider = create_provider(
            model,
            api_key=api_key,
            base_url=base_url,
            **provider_kwargs,
        )

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        stream: bool = False,
        on_stream_chunk: Callable[[str, bool], None] | None = None,
        on_reasoning_chunk: Callable[[str, bool], None] | None = None,
    ) -> tuple[str, float]:
        """Execute a completion following the JSONLLMClient protocol.

        Args:
            messages: Conversation messages in dict format.
            response_format: Optional response format specification.
            stream: Enable streaming.
            on_stream_chunk: Callback for streaming text chunks.
            on_reasoning_chunk: Callback for reasoning content chunks.

        Returns:
            Tuple of (content, cost) matching JSONLLMClient protocol.
        """
        # Convert dict messages to LLMMessage format
        llm_messages = _prepare_messages_for_provider(
            provider_name=self._provider.provider_name,
            model=self._provider.model,
            messages=self._convert_messages(messages),
        )
        requested_mode = _requested_mode(response_format)
        mode_override: StructuredMode | None = None
        nim_structured_reasoning_fallback_off = False

        request, emit_reasoning_callbacks = self._build_request_with_runtime(
            llm_messages,
            response_format,
            requested_mode=requested_mode,
            mode_override=mode_override,
            nim_structured_reasoning_fallback_off=nim_structured_reasoning_fallback_off,
        )

        # Create streaming callback wrapper if needed
        stream_callback: StreamCallback | None = None
        streaming_active = (
            stream and self._streaming_enabled and (on_stream_chunk is not None or on_reasoning_chunk is not None)
        )
        saw_reasoning_delta = False

        if streaming_active:

            def stream_callback_wrapper(event: StreamEvent) -> None:
                nonlocal saw_reasoning_delta
                if event.delta_text and on_stream_chunk is not None:
                    on_stream_chunk(event.delta_text, False)
                if event.delta_reasoning and on_reasoning_chunk is not None and emit_reasoning_callbacks:
                    saw_reasoning_delta = True
                    on_reasoning_chunk(event.delta_reasoning, False)
                if event.done:
                    return

            stream_callback = stream_callback_wrapper

        # Execute request with retry logic
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._provider.complete(
                    request,
                    timeout_s=self._timeout_s,
                    stream=streaming_active,
                    on_stream_event=stream_callback,
                )

                # Extract content
                content = response.message.text
                if not content.strip():
                    # Some providers represent "structured output" as a tool call rather than
                    # plain assistant text. In that case, the JSON payload is typically in the
                    # tool-call arguments and message.text will be empty.
                    tool_calls = response.message.tool_calls
                    if tool_calls:
                        candidate = tool_calls[0].arguments_json
                        if isinstance(candidate, str) and candidate.strip():
                            content = candidate.strip()

                # Calculate cost
                cost = 0.0
                if response.usage:
                    input_tokens = response.usage.input_tokens
                    output_tokens = response.usage.output_tokens

                    # Best-effort usage fallback: some OpenAI-compatible proxies (and some
                    # streaming modes) return usage as 0/0 even when pricing is known.
                    # We approximate token counts from the actual request/response text.
                    if input_tokens == 0 or output_tokens == 0:
                        input_price, output_price = get_pricing(self._provider.model)
                        if input_price > 0.0 or output_price > 0.0:
                            if input_tokens == 0:
                                prompt_blob = json.dumps(
                                    {
                                        "messages": list(messages),
                                        "response_format": response_format,
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                                input_tokens = max(1, int(len(prompt_blob) / 3.5))

                            if output_tokens == 0:
                                output_blob = content + (response.reasoning_content or "")
                                if output_blob:
                                    output_tokens = max(1, int(len(output_blob) / 3.5))

                            logger.debug(
                                "llm_usage_estimated",
                                extra={
                                    "provider": self._provider.provider_name,
                                    "model": self._provider.model,
                                    "estimated_input_tokens": input_tokens,
                                    "estimated_output_tokens": output_tokens,
                                },
                            )

                    cost = calculate_cost(
                        self._provider.model,
                        input_tokens,
                        output_tokens,
                    )

                # If we streamed, finalize callbacks and optionally backfill reasoning.
                if streaming_active:
                    if (
                        on_reasoning_chunk is not None
                        and response.reasoning_content
                        and not saw_reasoning_delta
                        and emit_reasoning_callbacks
                    ):
                        on_reasoning_chunk(response.reasoning_content, False)
                    if on_stream_chunk is not None:
                        on_stream_chunk("", True)
                    if on_reasoning_chunk is not None and emit_reasoning_callbacks:
                        on_reasoning_chunk("", True)
                else:
                    # Non-streaming reasoning callback (matches LiteLLM behavior)
                    if on_reasoning_chunk is not None and response.reasoning_content and emit_reasoning_callbacks:
                        on_reasoning_chunk(response.reasoning_content, False)
                        on_reasoning_chunk("", True)

                return content, cost

            except Exception as e:
                last_error = e
                error_type = e.__class__.__name__

                is_structured_requested = requested_mode in {"json_schema", "json_object"}

                if (
                    is_structured_requested
                    and self._provider.provider_name == "nim"
                    and not nim_structured_reasoning_fallback_off
                    and not is_retryable(e)
                ):
                    nim_structured_reasoning_fallback_off = True
                    request, emit_reasoning_callbacks = self._build_request_with_runtime(
                        llm_messages,
                        response_format,
                        requested_mode=requested_mode,
                        mode_override=mode_override,
                        nim_structured_reasoning_fallback_off=nim_structured_reasoning_fallback_off,
                    )
                    logger.warning(
                        "nim_structured_reasoning_fallback_off",
                        extra={
                            "provider": self._provider.provider_name,
                            "model": self._provider.model,
                            "error_type": error_type,
                        },
                    )
                    continue

                if is_structured_requested and _is_invalid_json_schema_error(e):
                    current_mode = mode_override or requested_mode
                    downgraded_mode = next_mode(current_mode)
                    if downgraded_mode is not None:
                        mode_override = downgraded_mode
                        request, emit_reasoning_callbacks = self._build_request_with_runtime(
                            llm_messages,
                            response_format,
                            requested_mode=requested_mode,
                            mode_override=mode_override,
                            nim_structured_reasoning_fallback_off=nim_structured_reasoning_fallback_off,
                        )
                        logger.warning(
                            "native_response_format_downgraded",
                            extra={
                                "provider": self._provider.provider_name,
                                "model": self._provider.model,
                                "from_mode": current_mode,
                                "to_mode": downgraded_mode,
                            },
                        )
                        continue

                # Check if error is retryable (timeout, rate limit, server errors)
                if is_retryable(e) and attempt < self._max_retries - 1:
                    backoff_s = 2**attempt  # Exponential backoff: 1s, 2s, 4s, ...
                    logger.warning(
                        f"Native LLM adapter error: {e} | provider={self._provider.provider_name}",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": self._max_retries,
                            "backoff_s": backoff_s,
                            "error_type": error_type,
                        },
                    )
                    await asyncio.sleep(backoff_s)
                    continue

                # Non-retryable error or final attempt
                logger.warning(
                    f"Native LLM adapter error: {e} | provider={self._provider.provider_name}",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self._max_retries,
                        "error_type": error_type,
                        "retryable": is_retryable(e),
                    },
                )
                raise

        # Should not reach here, but handle just in case
        msg = f"LLM call failed after {self._max_retries} retries"
        raise RuntimeError(msg) from last_error

    async def stream_events(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        response_format: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream events as an async iterator.

        This provides a LiteLLM-style "pull" streaming API (async iteration) on top
        of the provider callback-based streaming contract.
        """
        if not self._streaming_enabled:
            raise RuntimeError("Streaming is disabled for this adapter (set streaming_enabled=True).")

        llm_messages = _prepare_messages_for_provider(
            provider_name=self._provider.provider_name,
            model=self._provider.model,
            messages=self._convert_messages(messages),
        )
        requested_mode = _requested_mode(response_format)
        request, _ = self._build_request_with_runtime(
            llm_messages,
            response_format,
            requested_mode=requested_mode,
            mode_override=None,
            nim_structured_reasoning_fallback_off=False,
        )

        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        saw_done = False

        def on_stream_event(event: StreamEvent) -> None:
            nonlocal saw_done
            if event.done:
                saw_done = True
            queue.put_nowait(event)

        async def run() -> None:
            try:
                await self._provider.complete(
                    request,
                    timeout_s=timeout_s or self._timeout_s,
                    stream=True,
                    on_stream_event=on_stream_event,
                )
            finally:
                if not saw_done:
                    queue.put_nowait(StreamEvent(done=True))

        task = asyncio.create_task(run())

        try:
            while True:
                event = await queue.get()
                yield event
                if event.done:
                    break
            await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _convert_messages(self, messages: Sequence[Mapping[str, str]]) -> list[LLMMessage]:
        """Convert dict messages to LLMMessage format."""
        result: list[LLMMessage] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Map role to valid LLMMessage role
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"

            result.append(LLMMessage(role=role, parts=[TextPart(text=content)]))  # type: ignore

        return result

    def _build_request(
        self,
        messages: list[LLMMessage],
        response_format: Mapping[str, Any] | None,
    ) -> LLMRequest:
        """Build an LLMRequest from messages and response format.

        This is the default (first-attempt) build path used by tests and callers
        that do not need runtime retry overrides.
        """
        request, _ = self._build_request_with_runtime(
            messages,
            response_format,
            requested_mode=_requested_mode(response_format),
            mode_override=None,
            nim_structured_reasoning_fallback_off=False,
        )
        return request

    def _build_request_with_runtime(
        self,
        messages: list[LLMMessage],
        response_format: Mapping[str, Any] | None,
        *,
        requested_mode: StructuredMode,
        mode_override: StructuredMode | None,
        nim_structured_reasoning_fallback_off: bool,
    ) -> tuple[LLMRequest, bool]:
        """Build request for a specific attempt with policy overrides."""
        from .types import StructuredOutputSpec

        structured_output: StructuredOutputSpec | None = None
        policy = resolve_policy(
            provider_name=self._provider.provider_name,
            model=self._provider.model,
            requested_mode=requested_mode,
            mode_override=mode_override,
            structured_reasoning_fallback_off=nim_structured_reasoning_fallback_off,
            use_native_reasoning=self._use_native_reasoning,
        )

        if response_format and self._json_schema_mode:
            if requested_mode != policy.mode:
                logger.info(
                    "native_response_format_preemptive_override",
                    extra={
                        "provider": self._provider.provider_name,
                        "model": self._provider.model,
                        "from_mode": requested_mode,
                        "to_mode": policy.mode,
                    },
                )

            if policy.mode == "json_schema":
                json_schema_spec_raw = response_format.get("json_schema", {})
                json_schema_spec = json_schema_spec_raw if isinstance(json_schema_spec_raw, Mapping) else {}
                schema_name = json_schema_spec.get("name", "response")
                schema = _normalize_json_schema(json_schema_spec.get("schema", {}))
                strict = json_schema_spec.get("strict", True)
                structured_output = StructuredOutputSpec(
                    name=schema_name,
                    json_schema=schema,
                    strict=strict,
                )
            elif policy.mode == "json_object":
                structured_output = StructuredOutputSpec(
                    name="json_response",
                    json_schema={"type": "object"},
                    strict=False,
                )
            else:
                structured_output = None

        # Build extra parameters for reasoning support
        extra: dict[str, Any] | None = None
        if policy.inject_reasoning_effort and self._reasoning_effort is not None:
            extra = {"reasoning_effort": self._reasoning_effort}
        if self._provider.provider_name == "nim" and requested_mode in {"json_schema", "json_object"}:
            logger.debug(
                "nim_structured_request_mode",
                extra={
                    "provider": self._provider.provider_name,
                    "model": self._provider.model,
                    "requested_mode": requested_mode,
                    "effective_mode": policy.mode,
                    "inject_reasoning_effort": policy.inject_reasoning_effort,
                    "emit_reasoning_callbacks": policy.emit_reasoning_callbacks,
                },
            )

        return (
            LLMRequest(
                model=self._provider.model,
                messages=tuple(messages),
                structured_output=structured_output,
                temperature=self._temperature,
                extra=extra,
            ),
            policy.emit_reasoning_callbacks,
        )


def create_native_adapter(
    model: str | Mapping[str, Any],
    *,
    temperature: float = 0.0,
    json_schema_mode: bool = True,
    max_retries: int = 3,
    timeout_s: float = 360.0,
    streaming_enabled: bool = True,
    use_native_reasoning: bool = True,
    reasoning_effort: str | None = None,
    **kwargs: Any,
) -> NativeLLMAdapter:
    """Factory function to create a NativeLLMAdapter.

    Accepts the same configuration style as the existing _LiteLLMJSONClient
    for easy migration.

    Args:
        model: Model identifier string or config dict.
        temperature: Default temperature.
        json_schema_mode: Enable JSON schema mode.
        max_retries: Maximum retry attempts.
        timeout_s: Request timeout.
        streaming_enabled: Enable streaming.
        use_native_reasoning: Enable native reasoning for supported models.
        reasoning_effort: Reasoning effort level (e.g., "low", "medium", "high").
        **kwargs: Additional provider configuration.

    Returns:
        Configured NativeLLMAdapter instance.
    """
    if isinstance(model, Mapping):
        # Extract model name from config dict
        model_name = model.get("model", "")
        api_key = model.get("api_key")
        base_url = model.get("base_url") or model.get("api_base")
        # Merge other config as kwargs
        extra_kwargs = {k: v for k, v in model.items() if k not in ("model", "api_key", "base_url", "api_base")}
        kwargs.update(extra_kwargs)
    else:
        model_name = model
        api_key = kwargs.pop("api_key", None)
        base_url = kwargs.pop("base_url", None)

    return NativeLLMAdapter(
        model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        json_schema_mode=json_schema_mode,
        max_retries=max_retries,
        timeout_s=timeout_s,
        streaming_enabled=streaming_enabled,
        use_native_reasoning=use_native_reasoning,
        reasoning_effort=reasoning_effort,
        **kwargs,
    )


__all__ = [
    "NativeLLMAdapter",
    "create_native_adapter",
]
