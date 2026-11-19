"""OpenRouter provider implementation.

Uses the OpenAI SDK for OpenRouter's OpenAI-compatible API. OpenRouter provides
unified access to 500+ models from multiple providers including OpenAI GPT-5.x,
Anthropic Claude 4.x, Google Gemini 3.x, DeepSeek R1, Meta Llama 4, Qwen3, and
more.

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
from ..profiles.openrouter import get_openrouter_profile
from ..types import (
    CompletionResponse,
    LLMRequest,
)
from ._params import resolve_temperature
from .base import (
    OpenAICompatibleProvider,
    OpenAIStreamAccumulator,
    _extract_openai_delta_reasoning,
    _extract_openai_reasoning_content,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_response_format,
    _to_openai_tools,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from ..profiles import ModelProfile
    from ..types import CancelToken, StreamCallback

_XAI_ROUTE_PREFIXES = frozenset({"x-ai", "xai"})
_REASONING_OFF_VALUES = frozenset(
    {"off", "none", "false", "0", "disable", "disabled", "no"}
)


def _parse_model(model: str) -> tuple[str, str | None]:
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


def _coerce_reasoning_enabled(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if normalized in {
            "0",
            "false",
            "no",
            "off",
            "disable",
            "disabled",
        }:
            return False
    return None


def _reasoning_enabled_from_effort(
    reasoning_effort: object,
) -> bool | None:
    if reasoning_effort is None:
        return None
    effort = str(reasoning_effort).strip().lower()
    if not effort:
        return None
    return effort not in _REASONING_OFF_VALUES


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter provider with model routing.

    Parses model strings like "openrouter/anthropic/claude-sonnet-4.5" and
    routes to appropriate profile based on the underlying provider.

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
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize the OpenRouter provider.

        Args:
            model: Model identifier (e.g.,
                "openrouter/anthropic/claude-sonnet-4.5").
                   Can include variants like "openai/gpt-5:thinking".
            api_key: OpenRouter API key (uses OPENROUTER_API_KEY env var if not
                provided).
            profile: Model profile override.
            app_url: Application URL for OpenRouter attribution (HTTP-Referer
                header).
            app_title: Application title for OpenRouter attribution (X-Title
                header).
            timeout: Default timeout in seconds.
            client: Pre-built SDK client to use instead of creating one.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            msg = (
                "OpenAI SDK not installed. Install with: pip install "
                "openai>=1.58.0"
            )
            raise ImportError(msg) from e

        # Parse model string
        self._original_model = model
        self._model, self._provider_hint = _parse_model(model)

        # Get profile based on underlying provider
        if profile:
            self._profile = profile
        else:
            self._profile = get_openrouter_profile(model)

        self._timeout = timeout

        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            msg = (
                "OpenRouter API key required. Set OPENROUTER_API_KEY "
                "environment variable or pass api_key explicitly."
            )
            raise ValueError(msg)

        app_url = app_url or os.environ.get("OPENROUTER_APP_URL", "")
        app_title = app_title or os.environ.get(
            "OPENROUTER_APP_TITLE", "penguiflow"
        )

        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=timeout,
                default_headers={
                    "HTTP-Referer": app_url,
                    "X-Title": app_title,
                },
            )
        )

    @property
    def original_model(self) -> str:
        """The model identifier as passed to the constructor."""
        return self._original_model

    @property
    @override
    def provider_name(self) -> str:
        return "openrouter"

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return self._model

    @property
    def client(self) -> AsyncOpenAI:
        """The SDK client the provider sends requests with."""
        return self._client

    @property
    def timeout(self) -> float:
        """The default request timeout, in seconds."""
        return self._timeout

    def _is_xai_route(self) -> bool:
        provider = self._model.split("/", 1)[0].lower().strip()
        return provider in _XAI_ROUTE_PREFIXES

    def _normalize_reasoning_controls(self, extra: dict[str, Any]) -> None:
        reasoning_effort = extra.pop("reasoning_effort", None)
        reasoning_enabled = extra.pop("reasoning_enabled", None)
        include_reasoning = extra.pop("include_reasoning", None)
        explicit_reasoning = extra.pop("reasoning", None)

        raw_extra_body = extra.get("extra_body")
        extra_body = (
            dict(raw_extra_body) if isinstance(raw_extra_body, dict) else {}
        )

        if explicit_reasoning is not None:
            extra_body["reasoning"] = explicit_reasoning
        else:
            enabled = _coerce_reasoning_enabled(reasoning_enabled)
            if enabled is None and self._is_xai_route():
                enabled = _reasoning_enabled_from_effort(reasoning_effort)
            if enabled is not None:
                extra_body["reasoning"] = {"enabled": enabled}

        if include_reasoning is not None:
            extra_body["include_reasoning"] = include_reasoning

        if extra_body:
            extra["extra_body"] = extra_body
        else:
            extra.pop("extra_body", None)

        if not self._is_xai_route() and reasoning_effort is not None:
            extra["reasoning_effort"] = reasoning_effort

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
                provider="openrouter",
                retryable=False,
            )

        params = self.build_params(request)
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
                        provider="openrouter",
                        raw=e,
                    ) from e

            async with asyncio.timeout(timeout):
                response = await self._client.chat.completions.create(**params)

            message, usage = _from_openai_response(response)

            return CompletionResponse(
                message=message,
                usage=usage,
                raw_response=response,
                reasoning_content=_extract_openai_reasoning_content(
                    response.choices[0].message
                ),
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
            raise self.map_error(e) from e

    async def stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        params["stream"] = True
        stream_options = dict(params.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        params["stream_options"] = stream_options
        accumulator = OpenAIStreamAccumulator(
            on_stream_event, reasoning_of=_extract_openai_delta_reasoning
        )

        stream = await self._client.chat.completions.create(**params)
        async for chunk in stream:
            if cancel and cancel.is_cancelled():
                raise LLMCancelledError(
                    message="Request cancelled",
                    provider="openrouter",
                    retryable=False,
                )
            accumulator.add(chunk)

        return accumulator.response()

    def build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build OpenRouter API parameters from request.

        Supports OpenRouter-specific options via request.extra:
        - transforms: List of transforms to apply (e.g., ["middle-out"])
        - route: Routing preference ("fallback" for automatic failover)
        - provider: Provider preferences (order, allow, require_parameters)
        - models: Fallback model list for automatic routing
        """
        params: dict[str, Any] = {
            "model": self._model,
            "messages": _to_openai_messages(request.messages),
        }

        temp = resolve_temperature(
            self._profile,
            request.temperature,
            model=self._model,
            forced_off=self.temperature_unsupported,
        )
        if temp is not None:
            params["temperature"] = temp

        if request.max_tokens is not None:
            params["max_tokens"] = request.max_tokens

        if request.tools:
            params["tools"] = _to_openai_tools(request.tools)

        if request.tool_choice:
            params["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool_choice},
            }

        if request.structured_output:
            params["response_format"] = _to_openai_response_format(
                request.structured_output
            )

        if request.extra:
            extra = dict(request.extra)
            self._normalize_reasoning_controls(extra)
            # Handle OpenRouter-specific options
            openrouter_keys = ["transforms", "route", "provider", "models"]
            for key in openrouter_keys:
                if key in extra:
                    params[key] = extra.pop(key)
            params.update(extra)

        return params

    def map_error(self, exc: Exception) -> LLMError:
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
