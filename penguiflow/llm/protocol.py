"""Protocol adapter for JSONLLMClient compatibility.

Provides a bridge between the new native LLM layer and the existing
JSONLLMClient protocol used by the planner.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .errors import is_retryable
from .pricing import calculate_cost
from .providers import create_provider
from .types import (
    LLMMessage,
    LLMRequest,
    StreamCallback,
    StreamEvent,
    TextPart,
)

logger = logging.getLogger("penguiflow.llm.protocol")


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
        streaming_enabled: bool = False,
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
        llm_messages = self._convert_messages(messages)

        # Build request
        request = self._build_request(llm_messages, response_format)

        # Create streaming callback wrapper if needed
        stream_callback: StreamCallback | None = None
        if stream and self._streaming_enabled and on_stream_chunk is not None:
            def stream_callback_wrapper(event: StreamEvent) -> None:
                if event.delta_text:
                    on_stream_chunk(event.delta_text, False)
                if event.done:
                    on_stream_chunk("", True)
            stream_callback = stream_callback_wrapper

        # Execute request with retry logic
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._provider.complete(
                    request,
                    timeout_s=self._timeout_s,
                    stream=stream and self._streaming_enabled,
                    on_stream_event=stream_callback,
                )

                # Extract content
                content = response.message.text

                # Calculate cost
                cost = 0.0
                if response.usage:
                    cost = calculate_cost(
                        self._provider.model,
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                    )

                # Handle reasoning content if callback provided
                if on_reasoning_chunk is not None and response.reasoning_content:
                    on_reasoning_chunk(response.reasoning_content, False)
                    on_reasoning_chunk("", True)

                return content, cost

            except Exception as e:
                last_error = e
                error_type = e.__class__.__name__

                # Check if error is retryable (timeout, rate limit, server errors)
                if is_retryable(e) and attempt < self._max_retries - 1:
                    backoff_s = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s, ...
                    logger.warning(
                        f"Native LLM adapter error: {e} | provider={self._provider.name}",
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
                    f"Native LLM adapter error: {e} | provider={self._provider.name}",
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
        """Build an LLMRequest from messages and response format."""
        from .types import StructuredOutputSpec

        structured_output: StructuredOutputSpec | None = None

        if response_format and self._json_schema_mode:
            format_type = response_format.get("type")

            if format_type == "json_schema":
                # Extract schema from response_format
                json_schema_spec = response_format.get("json_schema", {})
                schema_name = json_schema_spec.get("name", "response")
                schema = json_schema_spec.get("schema", {})
                strict = json_schema_spec.get("strict", True)

                structured_output = StructuredOutputSpec(
                    name=schema_name,
                    json_schema=schema,
                    strict=strict,
                )

            elif format_type == "json_object":
                # JSON object mode - no strict schema, just ensure JSON output
                # Use a generic JSON output schema
                structured_output = StructuredOutputSpec(
                    name="json_response",
                    json_schema={"type": "object"},
                    strict=False,
                )

        # Build extra parameters for reasoning support
        extra: dict[str, Any] | None = None
        if self._use_native_reasoning and self._reasoning_effort is not None:
            extra = {"reasoning_effort": self._reasoning_effort}

        return LLMRequest(
            model=self._provider.model,
            messages=tuple(messages),
            structured_output=structured_output,
            temperature=self._temperature,
            extra=extra,
        )


def create_native_adapter(
    model: str | Mapping[str, Any],
    *,
    temperature: float = 0.0,
    json_schema_mode: bool = True,
    max_retries: int = 3,
    timeout_s: float = 60.0,
    streaming_enabled: bool = False,
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
        extra_kwargs = {k: v for k, v in model.items()
                       if k not in ("model", "api_key", "base_url", "api_base")}
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
