"""OpenAI provider implementation.

Uses the official openai SDK (v2.x) for direct API access.
Supports both the Chat Completions API and newer Responses API patterns.

SDK Reference: https://github.com/openai/openai-python
"""

from __future__ import annotations

import asyncio
import os
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
    LLMMessage,
    LLMRequest,
    StreamEvent,
    TextPart,
    ToolCallPart,
    Usage,
)
from .base import OpenAICompatibleProvider

if TYPE_CHECKING:
    from ..types import CancelToken, StreamCallback


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI provider using native SDK (v2.x).

    Supports:
    - GPT-4.1 family (latest, 1M context window)
    - GPT-4o family (gpt-4o, gpt-4o-mini)
    - Reasoning models (o1, o3, o4-mini)
    - Legacy models (GPT-4 Turbo, GPT-3.5 Turbo)
    - Native structured outputs via response_format
    - Streaming with usage tracking
    - Function/tool calling
    - Realtime API (via separate client)

    Reference: https://platform.openai.com/docs/api-reference/chat
    SDK: https://github.com/openai/openai-python (v2.15.0+)
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: ModelProfile | None = None,
        organization: str | None = None,
        timeout: float = 60.0,
    ):
        """Initialize the OpenAI provider.

        Args:
            model: Model identifier (e.g., "gpt-4o").
            api_key: OpenAI API key (uses OPENAI_API_KEY env var if not provided).
            base_url: Base URL override for OpenAI-compatible APIs.
            profile: Model profile override.
            organization: OpenAI organization ID.
            timeout: Default timeout in seconds.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError(
                "OpenAI SDK not installed. Install with: pip install openai>=2.0.0"
            ) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout

        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            organization=organization,
            timeout=timeout,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

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
            raise LLMCancelledError(message="Request cancelled", provider="openai")

        params = self._build_params(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                return await self._stream_completion(params, on_stream_event, timeout, cancel)

            async with asyncio.timeout(timeout):
                response = await self._client.chat.completions.create(**params)

            message, usage = self._from_openai_response(response)

            return CompletionResponse(
                message=message,
                usage=usage,
                raw_response=response,
                reasoning_content=getattr(response.choices[0].message, "reasoning_content", None),
                finish_reason=response.choices[0].finish_reason,
            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="openai",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(
                message="Request cancelled", provider="openai"
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
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        text_acc: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage: Usage | None = None
        finish_reason: str | None = None
        reasoning_content: str | None = None

        try:
            async with asyncio.timeout(timeout):
                stream = await self._client.chat.completions.create(**params)
                async for chunk in stream:
                    if cancel and cancel.is_cancelled():
                        raise LLMCancelledError(message="Request cancelled", provider="openai")

                    if not chunk.choices:
                        # Usage chunk at the end
                        if hasattr(chunk, "usage") and chunk.usage:
                            usage = Usage(
                                input_tokens=chunk.usage.prompt_tokens,
                                output_tokens=chunk.usage.completion_tokens,
                                total_tokens=chunk.usage.total_tokens,
                            )
                        continue

                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    # Handle text content
                    if delta.content:
                        text_acc.append(delta.content)
                        on_stream_event(StreamEvent(delta_text=delta.content))

                    # Handle reasoning content
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        if reasoning_content is None:
                            reasoning_content = ""
                        reasoning_content += delta.reasoning_content
                        on_stream_event(StreamEvent(delta_reasoning=delta.reasoning_content))

                    # Handle tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["arguments"] += tc.function.arguments

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Stream timed out after {timeout}s",
                provider="openai",
                raw=e,
            ) from e

        # Build final message
        parts: list[Any] = []
        full_text = "".join(text_acc)
        if full_text:
            parts.append(TextPart(text=full_text))

        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            parts.append(
                ToolCallPart(
                    name=tc["name"],
                    arguments_json=tc["arguments"],
                    call_id=tc["id"],
                )
            )

        on_stream_event(StreamEvent(done=True, usage=usage, finish_reason=finish_reason))

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage or Usage.zero(),
            raw_response=None,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
        )

    def _build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build OpenAI API parameters from request."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(request.messages),
        }

        # Temperature (some models don't support it)
        if not self._profile.supports_reasoning or request.temperature > 0:
            params["temperature"] = request.temperature

        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens

        if request.tools:
            params["tools"] = self._to_openai_tools(request.tools)

        if request.tool_choice:
            params["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool_choice},
            }

        if request.structured_output:
            params["response_format"] = self._to_openai_response_format(request.structured_output)

        # Handle extra parameters
        if request.extra:
            extra = dict(request.extra)
            # Handle reasoning_effort for reasoning models
            if self._profile.reasoning_effort_param and self._profile.reasoning_effort_param in extra:
                params[self._profile.reasoning_effort_param] = extra.pop(self._profile.reasoning_effort_param)
            params.update(extra)

        return params

    def _map_error(self, exc: Exception) -> LLMError:
        """Map OpenAI SDK exceptions to LLMError."""
        try:
            from openai import (
                APIConnectionError,
                APIStatusError,
                AuthenticationError,
                BadRequestError,
                RateLimitError,
            )

            if isinstance(exc, AuthenticationError):
                return LLMAuthError(
                    message=str(exc),
                    provider="openai",
                    status_code=exc.status_code if hasattr(exc, "status_code") else 401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=str(exc),
                    provider="openai",
                    status_code=exc.status_code if hasattr(exc, "status_code") else 429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc):
                    return LLMContextLengthError(
                        message=str(exc),
                        provider="openai",
                        status_code=exc.status_code if hasattr(exc, "status_code") else 400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="openai",
                    status_code=exc.status_code if hasattr(exc, "status_code") else 400,
                    raw=exc,
                )

            if isinstance(exc, APIStatusError):
                status = exc.status_code if hasattr(exc, "status_code") else 500
                if status >= 500:
                    return LLMServerError(
                        message=str(exc),
                        provider="openai",
                        status_code=status,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="openai",
                    status_code=status,
                    raw=exc,
                )

            if isinstance(exc, APIConnectionError):
                return LLMServerError(
                    message=str(exc),
                    provider="openai",
                    raw=exc,
                )

        except ImportError:
            pass

        return LLMError(
            message=str(exc),
            provider="openai",
            raw=exc,
        )
