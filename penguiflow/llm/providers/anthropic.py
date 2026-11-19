"""Anthropic provider implementation.

Uses the official anthropic SDK for direct API access.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from typing_extensions import override

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
from ._params import resolve_temperature
from .base import Provider

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types import Message

    from ..types import (
        CancelToken,
        ContentPart,
        StreamCallback,
        StructuredOutputSpec,
    )


_THINKING_BLOCKS = ("thinking", "redacted_thinking")


def _thinking_text(source: object) -> str:
    """Return the ``thinking`` (or ``text``) text of a block or delta."""
    value = getattr(source, "thinking", None) or getattr(source, "text", None)
    return str(value) if value else ""


class _AnthropicStreamState:
    """Accumulates Anthropic stream events into a completion response."""

    def __init__(self, on_stream_event: StreamCallback) -> None:
        self._emit = on_stream_event
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: list[dict[str, str]] = []
        self._current_tool: dict[str, str] | None = None
        self._block_type: str | None = None
        self._usage: Usage | None = None
        self._finish_reason: str | None = None

    def handle(self, event: object) -> None:
        """Dispatch one stream event by its ``type``."""
        event_type = getattr(event, "type", None)
        if event_type == "content_block_start":
            self._start_block(getattr(event, "content_block", None))
        elif event_type == "content_block_delta":
            self._add_delta(getattr(event, "delta", None))
        elif event_type == "content_block_stop":
            if self._current_tool:
                self._tool_calls.append(self._current_tool)
                self._current_tool = None
            self._block_type = None
        elif event_type == "message_delta":
            self._finish_reason = getattr(
                getattr(event, "delta", None), "stop_reason", None
            )
        elif event_type == "message_start":
            usage = getattr(getattr(event, "message", None), "usage", None)
            if usage:
                self._usage = Usage(
                    input_tokens=usage.input_tokens,
                    output_tokens=0,
                    total_tokens=usage.input_tokens,
                )

    def _add_reasoning(self, text: str) -> None:
        self._reasoning.append(text)
        self._emit(StreamEvent(delta_reasoning=text))

    def _start_block(self, block: object) -> None:
        block_type = getattr(block, "type", None)
        self._block_type = block_type
        if block_type in _THINKING_BLOCKS:
            initial = _thinking_text(block)
            if initial:
                self._add_reasoning(initial)
        if block_type == "tool_use":
            self._current_tool = {
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": "",
            }

    def _add_delta(self, delta: object) -> None:
        if self._block_type in _THINKING_BLOCKS:
            thinking = _thinking_text(delta)
            if thinking:
                self._add_reasoning(thinking)
        elif hasattr(delta, "text"):
            self._text.append(delta.text)
            self._emit(StreamEvent(delta_text=delta.text))
        elif hasattr(delta, "partial_json") and self._current_tool:
            self._current_tool["input"] += delta.partial_json

    def set_final_usage(self, final_message: object) -> None:
        """Take the final token usage from the completed message."""
        usage = getattr(final_message, "usage", None)
        if usage is None:
            return
        self._usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.input_tokens + usage.output_tokens,
        )

    def response(self) -> CompletionResponse:
        """Emit the ``done`` event and build the completion response."""
        parts: list[ContentPart] = []
        full_text = "".join(self._text)
        if full_text:
            parts.append(TextPart(text=full_text))
        parts.extend(
            ToolCallPart(
                name=call["name"],
                arguments_json=call["input"],
                call_id=call["id"],
            )
            for call in self._tool_calls
        )
        self._emit(
            StreamEvent(
                done=True, usage=self._usage, finish_reason=self._finish_reason
            )
        )
        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=self._usage or Usage.zero(),
            raw_response=None,
            reasoning_content="".join(self._reasoning) or None,
            finish_reason=self._finish_reason,
        )


def _to_anthropic_messages(
    messages: tuple[Any, ...] | list[Any],
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
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": part.media_type,
                            "data": b64,
                        },
                    }
                )
            elif isinstance(part, ToolCallPart):
                content.append(
                    {
                        "type": "tool_use",
                        "id": part.call_id or f"call_{uuid.uuid4().hex[:16]}",
                        "name": part.name,
                        "input": json.loads(part.arguments_json)
                        if part.arguments_json
                        else {},
                    }
                )
            elif isinstance(part, ToolResultPart):
                # Tool results are separate messages in Anthropic
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": part.call_id or "",
                                "content": part.result_json,
                                "is_error": part.is_error,
                            }
                        ],
                    }
                )
                continue

        if content:
            result.append(
                {
                    "role": msg.role if msg.role != "tool" else "user",
                    "content": content,
                }
            )

    return system_text, result


def _from_anthropic_response(response: Message) -> CompletionResponse:
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
        total_tokens=response.usage.input_tokens
        + response.usage.output_tokens,
    )

    return CompletionResponse(
        message=LLMMessage(role="assistant", parts=parts),
        usage=usage,
        raw_response=response,
        reasoning_content="".join(reasoning_acc) or None,
        finish_reason=response.stop_reason,
    )


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
        client: AsyncAnthropic | None = None,
        max_tokens: int = 8192,
    ) -> None:
        """Initialize the Anthropic provider.

        Args:
            model: Model identifier (e.g., "claude-sonnet-4-5-20250929",
                "claude-opus-4-5").
            api_key: Anthropic API key (uses ANTHROPIC_API_KEY env var if not
                provided).
            profile: Model profile override.
            timeout: Default timeout in seconds.
            client: Pre-built SDK client to use instead of creating one.
            max_tokens: Default max tokens for responses.
        """
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            msg = (
                "Anthropic SDK not installed. Install with: pip install "
                "anthropic>=0.75.0"
            )
            raise ImportError(msg) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout
        self._default_max_tokens = max_tokens

        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = (
            client
            if client is not None
            else AsyncAnthropic(
                api_key=api_key,
                timeout=timeout,
            )
        )

    @property
    def default_max_tokens(self) -> int:
        """The max_tokens sent when a request sets none."""
        return self._default_max_tokens

    @property
    @override
    def provider_name(self) -> str:
        return "anthropic"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return self._model

    @property
    def client(self) -> AsyncAnthropic:
        """The SDK client the provider sends requests with."""
        return self._client

    @property
    def timeout(self) -> float:
        """The default request timeout, in seconds."""
        return self._timeout

    @override
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
            raise LLMCancelledError(
                message="Request cancelled",
                provider="anthropic",
                retryable=False,
            )

        system_text, messages = _to_anthropic_messages(request.messages)
        params = self.build_params(request, system_text, messages)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                try:
                    async with asyncio.timeout(timeout):
                        return await self.stream_completion(
                            params, on_stream_event, cancel
                        )
                except TimeoutError as e:
                    raise LLMTimeoutError(
                        message=f"Stream timed out after {timeout}s",
                        provider="anthropic",
                        raw=e,
                    ) from e

            async with asyncio.timeout(timeout):
                response = await self._client.messages.create(**params)

            return _from_anthropic_response(response)

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
            raise self.map_error(e) from e

    async def stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        state = _AnthropicStreamState(on_stream_event)
        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                if cancel and cancel.is_cancelled():
                    raise LLMCancelledError(
                        message="Request cancelled",
                        provider="anthropic",
                        retryable=False,
                    )
                if event.type == "message_stop":
                    state.set_final_usage(await stream.get_final_message())
                else:
                    state.handle(event)
        return state.response()

    def build_params(
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

        temp = resolve_temperature(
            self._profile,
            request.temperature,
            model=self._model,
            forced_off=self.temperature_unsupported,
        )
        if temp is not None:
            params["temperature"] = temp

        if request.tools:
            params["tools"] = self._to_anthropic_tools(request.tools)

        if request.tool_choice:
            params["tool_choice"] = {
                "type": "tool",
                "name": request.tool_choice,
            }

        # Handle structured output via tool use
        if request.structured_output:
            params = self._add_structured_output(
                params, request.structured_output
            )

        if request.extra:
            params.update(request.extra)

        return params

    def _to_anthropic_tools(
        self, tools: tuple[Any, ...] | list[Any] | None
    ) -> list[dict[str, Any]] | None:
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
        self, params: dict[str, Any], structured_output: StructuredOutputSpec
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
        params["tool_choice"] = {
            "type": "tool",
            "name": structured_output.name,
        }

        return params

    def map_error(self, exc: Exception) -> LLMError:
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
