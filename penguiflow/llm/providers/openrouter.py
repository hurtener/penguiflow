"""OpenRouter provider implementation.

Uses the OpenAI SDK for OpenRouter's OpenAI-compatible API.
OpenRouter provides unified access to 500+ models from multiple providers
including OpenAI GPT-5.x, Anthropic Claude 4.x, Google Gemini 3.x,
DeepSeek R1, Meta Llama 4, Qwen3, and more.

Reference: https://openrouter.ai/docs/quickstart

Key features (January 2026):
- Model routing with automatic failover
- Model variants: :free, :extended, :thinking, :online, :nitro, :exacto
- Streaming via Server-Sent Events (SSE)
- Tool/function calling with parallel execution
- Structured outputs with JSON Schema validation
- Response healing for malformed JSON
- Prompt caching (OpenAI, Anthropic, DeepSeek)
- Web search with citation annotations
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
from ..profiles import ModelProfile
from ..profiles.openrouter import get_openrouter_profile
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


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter provider with model routing.

    Parses model strings like "openrouter/anthropic/claude-sonnet-4.5"
    and routes to appropriate profile based on the underlying provider.

    Supports 500+ models including:
    - OpenAI: gpt-5, gpt-5.1, gpt-5.2-pro (up to 400K context)
    - Anthropic: claude-opus-4.5, claude-sonnet-4.5 (up to 1M context)
    - Google: gemini-3-pro-preview, gemini-2.5-pro/flash (up to 1M context)
    - DeepSeek: deepseek-r1 (671B), deepseek-v3.x (up to 163K context)
    - Meta: llama-4-maverick, llama-4-scout, llama-3.3-70b (up to 131K context)
    - Qwen: qwen3, qwen3-vl (up to 262K context)

    Model variants available via suffix:
    - :free - Free tier with rate limits
    - :extended - Extended context window
    - :thinking - Reasoning-enabled (like o1)
    - :online - Web search enabled
    - :nitro - Low-latency inference
    - :exacto - High-precision structured outputs

    Reference: https://openrouter.ai/docs/quickstart
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        profile: ModelProfile | None = None,
        app_url: str | None = None,
        app_title: str | None = None,
        timeout: float = 360.0,
    ) -> None:
        """Initialize the OpenRouter provider.

        Args:
            model: Model identifier (e.g., "openrouter/anthropic/claude-sonnet-4.5").
                   Can include variants like "openai/gpt-5:thinking".
            api_key: OpenRouter API key (uses OPENROUTER_API_KEY env var if not provided).
            profile: Model profile override.
            app_url: Application URL for OpenRouter attribution (HTTP-Referer header).
            app_title: Application title for OpenRouter attribution (X-Title header).
            timeout: Default timeout in seconds.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError(
                "OpenAI SDK not installed. Install with: pip install openai>=1.58.0"
            ) from e

        # Parse model string
        self._original_model = model
        self._model, self._provider_hint = self._parse_model(model)

        # Get profile based on underlying provider
        if profile:
            self._profile = profile
        else:
            self._profile = get_openrouter_profile(model)

        self._timeout = timeout

        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError(
                "OpenRouter API key required. Set OPENROUTER_API_KEY "
                "environment variable or pass api_key explicitly."
            )

        app_url = app_url or os.environ.get("OPENROUTER_APP_URL", "")
        app_title = app_title or os.environ.get("OPENROUTER_APP_TITLE", "penguiflow")

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=timeout,
            default_headers={
                "HTTP-Referer": app_url,
                "X-Title": app_title,
            },
        )

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._model

    def _parse_model(self, model: str) -> tuple[str, str | None]:
        """Parse model string and extract provider hint.

        Args:
            model: Model string like "openrouter/anthropic/claude-3-5-sonnet".

        Returns:
            Tuple of (model_for_api, provider_hint).
        """
        parts = model.split("/")

        # Remove "openrouter" prefix if present
        if parts[0] == "openrouter":
            parts = parts[1:]

        if len(parts) >= 2:
            provider_hint = parts[0]
            model_name = "/".join(parts)
            return model_name, provider_hint

        return model, None

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
            raise LLMCancelledError(message="Request cancelled", provider="openrouter")

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
                reasoning_content=self._extract_openai_reasoning_content(response.choices[0].message),
                finish_reason=response.choices[0].finish_reason,
            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="openrouter",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(
                message="Request cancelled", provider="openrouter"
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
        stream_options = dict(params.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        params["stream_options"] = stream_options

        text_acc: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage: Usage | None = None
        finish_reason: str | None = None
        reasoning_acc: list[str] = []

        try:
            async with asyncio.timeout(timeout):
                stream = await self._client.chat.completions.create(**params)
                async for chunk in stream:
                    if cancel and cancel.is_cancelled():
                        raise LLMCancelledError(message="Request cancelled", provider="openrouter")

                    if not chunk.choices:
                        if hasattr(chunk, "usage") and chunk.usage:
                            usage = Usage(
                                input_tokens=chunk.usage.prompt_tokens,
                                output_tokens=chunk.usage.completion_tokens,
                                total_tokens=chunk.usage.total_tokens,
                            )
                        continue

                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    if delta.content:
                        text_acc.append(delta.content)
                        on_stream_event(StreamEvent(delta_text=delta.content))

                    delta_reasoning = self._extract_openai_delta_reasoning(delta)
                    if delta_reasoning:
                        reasoning_acc.append(delta_reasoning)
                        on_stream_event(StreamEvent(delta_reasoning=delta_reasoning))

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
                provider="openrouter",
                raw=e,
            ) from e

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
            reasoning_content="".join(reasoning_acc) or None,
            finish_reason=finish_reason,
        )

    def _build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build OpenRouter API parameters from request.

        Supports OpenRouter-specific options via request.extra:
        - transforms: List of transforms to apply (e.g., ["middle-out"])
        - route: Routing preference ("fallback" for automatic failover)
        - provider: Provider preferences (order, allow, require_parameters)
        - models: Fallback model list for automatic routing
        """
        params: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(request.messages),
            "temperature": request.temperature,
        }

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

        if request.extra:
            extra = dict(request.extra)
            # Handle OpenRouter-specific options
            openrouter_keys = ["transforms", "route", "provider", "models"]
            for key in openrouter_keys:
                if key in extra:
                    params[key] = extra.pop(key)
            params.update(extra)

        return params

    def _map_error(self, exc: Exception) -> LLMError:
        """Map OpenRouter/OpenAI SDK exceptions to LLMError."""
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
                    provider="openrouter",
                    status_code=401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=str(exc),
                    provider="openrouter",
                    status_code=429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc):
                    return LLMContextLengthError(
                        message=str(exc),
                        provider="openrouter",
                        status_code=400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="openrouter",
                    status_code=400,
                    raw=exc,
                )

            if isinstance(exc, APIStatusError):
                status = getattr(exc, "status_code", 500)
                if status >= 500:
                    return LLMServerError(
                        message=str(exc),
                        provider="openrouter",
                        status_code=status,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="openrouter",
                    status_code=status,
                    raw=exc,
                )

            if isinstance(exc, APIConnectionError):
                return LLMServerError(
                    message=str(exc),
                    provider="openrouter",
                    raw=exc,
                )

        except ImportError:
            pass

        return LLMError(
            message=str(exc),
            provider="openrouter",
            raw=exc,
        )
