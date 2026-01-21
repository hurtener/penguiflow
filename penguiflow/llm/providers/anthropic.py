"""Anthropic provider implementation.

Uses the official anthropic SDK for direct API access.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from ..errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    is_context_length_error,
)
from ..profiles import ModelProfile, get_profile
from ..types import (
    CompletionResponse,
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from .base import Provider

if TYPE_CHECKING:
    from ..types import CancelToken, StreamCallback


class AnthropicProvider(Provider):
    """Anthropic provider using native SDK.

    Supports:
    - Claude 4.5 models (Opus, Sonnet, Haiku) - latest generation
    - Claude 4.x models (Opus, Sonnet)
    - Claude 3.7 and 3.5 models (legacy)
    - Tool use for structured outputs
    - Streaming with usage tracking
    - Multi-turn conversations with tool results
    - Extended thinking (reasoning) for supported models

    Reference: https://docs.anthropic.com/en/api/messages
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
        max_tokens: int = 8192,
    ):
        """Initialize the Anthropic provider.

        Args:
            model: Model identifier (e.g., "claude-sonnet-4-5-20250929", "claude-opus-4-5").
            api_key: Anthropic API key (uses ANTHROPIC_API_KEY env var if not provided).
            profile: Model profile override.
            timeout: Default timeout in seconds.
            max_tokens: Default max tokens for responses.
        """
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise ImportError(
                "Anthropic SDK not installed. Install with: pip install anthropic>=0.75.0"
            ) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout
        self._default_max_tokens = max_tokens

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=timeout,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        """Execute a completion request."""
        if cancel and cancel.is_cancelled():
            raise LLMCancelledError(message="Request cancelled", provider="anthropic")

        system_text, messages = self._to_anthropic_messages(request.messages)
        params = self._build_params(request, system_text, messages)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                return await self._stream_completion(params, on_stream_event, timeout, cancel)

            async with asyncio.timeout(timeout):
                response = await self._client.messages.create(**params)

            return self._from_anthropic_response(response)

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="anthropic",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(
                message="Request cancelled", provider="anthropic"
            ) from None
        except Exception as e:
            raise self._map_error(e) from e

    async def _stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        timeout: float,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        text_acc: list[str] = []
        reasoning_acc: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        current_tool: dict[str, Any] | None = None
        current_block_type: str | None = None
        usage: Usage | None = None
        finish_reason: str | None = None

        try:
            async with asyncio.timeout(timeout):
                async with self._client.messages.stream(**params) as stream:
                    async for event in stream:
                        if cancel and cancel.is_cancelled():
                            raise LLMCancelledError(message="Request cancelled", provider="anthropic")

                        if event.type == "content_block_start":
                            current_block_type = event.content_block.type
                            if event.content_block.type in ("thinking", "redacted_thinking"):
                                initial = (
                                    getattr(event.content_block, "thinking", None)
                                    or getattr(event.content_block, "text", None)
                                    or ""
                                )
                                if initial:
                                    reasoning_acc.append(str(initial))
                                    on_stream_event(StreamEvent(delta_reasoning=str(initial)))
                            if event.content_block.type == "tool_use":
                                current_tool = {
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input": "",
                                }

                        elif event.type == "content_block_delta":
                            if current_block_type in ("thinking", "redacted_thinking"):
                                delta_thinking = (
                                    getattr(event.delta, "thinking", None)
                                    or getattr(event.delta, "text", None)
                                    or ""
                                )
                                if delta_thinking:
                                    reasoning_acc.append(str(delta_thinking))
                                    on_stream_event(StreamEvent(delta_reasoning=str(delta_thinking)))
                            elif hasattr(event.delta, "text"):
                                text_acc.append(event.delta.text)
                                on_stream_event(StreamEvent(delta_text=event.delta.text))
                            elif hasattr(event.delta, "partial_json") and current_tool:
                                current_tool["input"] += event.delta.partial_json

                        elif event.type == "content_block_stop":
                            if current_tool:
                                tool_calls.append(current_tool)
                                current_tool = None
                            current_block_type = None

                        elif event.type == "message_delta":
                            finish_reason = event.delta.stop_reason

                        elif event.type == "message_start":
                            if event.message.usage:
                                usage = Usage(
                                    input_tokens=event.message.usage.input_tokens,
                                    output_tokens=0,
                                    total_tokens=event.message.usage.input_tokens,
                                )

                        elif event.type == "message_stop":
                            final_message = await stream.get_final_message()
                            usage = Usage(
                                input_tokens=final_message.usage.input_tokens,
                                output_tokens=final_message.usage.output_tokens,
                                total_tokens=final_message.usage.input_tokens + final_message.usage.output_tokens,
                            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Stream timed out after {timeout}s",
                provider="anthropic",
                raw=e,
            ) from e

        # Build final message
        parts: list[Any] = []
        full_text = "".join(text_acc)
        if full_text:
            parts.append(TextPart(text=full_text))

        for tc in tool_calls:
            parts.append(
                ToolCallPart(
                    name=tc["name"],
                    arguments_json=tc["input"],
                    call_id=tc["id"],
                )
            )

        on_stream_event(StreamEvent(done=True, usage=usage, finish_reason=finish_reason))

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage or Usage.zero(),
            raw_response=None,
            reasoning_content="".join(reasoning_acc) or None,
            finish_reason=finish_reason,
        )

    def _to_anthropic_messages(
        self, messages: tuple[Any, ...] | list[Any]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert typed messages to Anthropic format.

        Returns:
            Tuple of (system_text, messages_list).
        """
        system_text: str | None = None
        result: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == "system":
                # Anthropic uses a separate system parameter
                system_text = msg.text
                continue

            content: list[dict[str, Any]] = []

            for part in msg.parts:
                if isinstance(part, TextPart):
                    content.append({"type": "text", "text": part.text})
                elif isinstance(part, ImagePart):
                    import base64

                    b64 = base64.b64encode(part.data).decode("utf-8")
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": part.media_type,
                            "data": b64,
                        },
                    })
                elif isinstance(part, ToolCallPart):
                    content.append({
                        "type": "tool_use",
                        "id": part.call_id or f"call_{uuid.uuid4().hex[:16]}",
                        "name": part.name,
                        "input": json.loads(part.arguments_json) if part.arguments_json else {},
                    })
                elif isinstance(part, ToolResultPart):
                    # Tool results are separate messages in Anthropic
                    result.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": part.call_id or "",
                            "content": part.result_json,
                            "is_error": part.is_error,
                        }],
                    })
                    continue

            if content:
                result.append({
                    "role": msg.role if msg.role != "tool" else "user",
                    "content": content,
                })

        return system_text, result

    def _build_params(
        self,
        request: LLMRequest,
        system_text: str | None,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build Anthropic API parameters from request."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens or self._default_max_tokens,
        }

        if system_text:
            params["system"] = system_text

        if request.temperature > 0:
            params["temperature"] = request.temperature

        if request.tools:
            params["tools"] = self._to_anthropic_tools(request.tools)

        if request.tool_choice:
            params["tool_choice"] = {"type": "tool", "name": request.tool_choice}

        # Handle structured output via tool use
        if request.structured_output:
            params = self._add_structured_output(params, request.structured_output)

        if request.extra:
            params.update(request.extra)

        return params

    def _to_anthropic_tools(self, tools: tuple[Any, ...] | list[Any] | None) -> list[dict[str, Any]] | None:
        """Convert typed tools to Anthropic format."""
        if not tools:
            return None

        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.json_schema,
            }
            for tool in tools
        ]

    def _add_structured_output(
        self, params: dict[str, Any], structured_output: Any
    ) -> dict[str, Any]:
        """Add structured output via forced tool use."""
        # Anthropic uses tool_use for structured output
        tool_def = {
            "name": structured_output.name,
            "description": "Return structured data in the specified format.",
            "input_schema": structured_output.json_schema,
        }

        if "tools" not in params:
            params["tools"] = []
        params["tools"].append(tool_def)
        params["tool_choice"] = {"type": "tool", "name": structured_output.name}

        return params

    def _from_anthropic_response(self, response: Any) -> CompletionResponse:
        """Convert Anthropic response to CompletionResponse."""
        parts: list[Any] = []
        reasoning_acc: list[str] = []

        for block in response.content:
            if block.type == "text":
                parts.append(TextPart(text=block.text))
            elif block.type in ("thinking", "redacted_thinking"):
                thinking_text = (
                    getattr(block, "thinking", None)
                    or getattr(block, "text", None)
                    or ""
                )
                if thinking_text:
                    reasoning_acc.append(str(thinking_text))
            elif block.type == "tool_use":
                parts.append(
                    ToolCallPart(
                        name=block.name,
                        arguments_json=json.dumps(block.input),
                        call_id=block.id,
                    )
                )

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        )

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage,
            raw_response=response,
            reasoning_content="".join(reasoning_acc) or None,
            finish_reason=response.stop_reason,
        )

    def _map_error(self, exc: Exception) -> LLMError:
        """Map Anthropic SDK exceptions to LLMError."""
        try:
            from anthropic import (
                APIConnectionError,
                APIStatusError,
                AuthenticationError,
                BadRequestError,
                RateLimitError,
            )

            if isinstance(exc, AuthenticationError):
                return LLMAuthError(
                    message=str(exc),
                    provider="anthropic",
                    status_code=401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=str(exc),
                    provider="anthropic",
                    status_code=429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc):
                    return LLMContextLengthError(
                        message=str(exc),
                        provider="anthropic",
                        status_code=400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="anthropic",
                    status_code=400,
                    raw=exc,
                )

            if isinstance(exc, APIStatusError):
                status = getattr(exc, "status_code", 500)
                if status >= 500:
                    return LLMServerError(
                        message=str(exc),
                        provider="anthropic",
                        status_code=status,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="anthropic",
                    status_code=status,
                    raw=exc,
                )

            if isinstance(exc, APIConnectionError):
                return LLMServerError(
                    message=str(exc),
                    provider="anthropic",
                    raw=exc,
                )

        except ImportError:
            pass

        return LLMError(
            message=str(exc),
            provider="anthropic",
            raw=exc,
        )
