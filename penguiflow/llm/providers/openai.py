"""OpenAI provider implementation.

Uses the official openai SDK (v2.x) for direct API access. Supports both the
Chat Completions API and newer Responses API patterns.

SDK Reference: https://github.com/openai/openai-python
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
from ..profiles import ModelProfile, get_profile
from ..types import (
    CompletionResponse,
    LLMRequest,
)
from ._params import resolve_temperature
from .base import (
    OpenAICompatibleProvider,
    OpenAIStreamAccumulator,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_response_format,
    _to_openai_tools,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from ..types import CancelToken, StreamCallback


def _delta_reasoning_content(delta: object) -> str | None:
    """Return a delta's non-empty ``reasoning_content`` attribute."""
    value = getattr(delta, "reasoning_content", None)
    return value if isinstance(value, str) and value else None


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

    Reference: https://platform.openai.com/docs/api-reference/chat SDK:
    https://github.com/openai/openai-python (v2.15.0+)
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: ModelProfile | None = None,
        organization: str | None = None,
        timeout: float = 360.0,
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize the OpenAI provider.

        Args:
            model: Model identifier (e.g., "gpt-4o").
            api_key: OpenAI API key (uses OPENAI_API_KEY env var if not
                provided).
            base_url: Base URL override for OpenAI-compatible APIs.
            profile: Model profile override.
            organization: OpenAI organization ID.
            timeout: Default timeout in seconds.
            client: Pre-built SDK client to use instead of creating one.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            msg = (
                "OpenAI SDK not installed. Install with: pip install "
                "openai>=2.0.0"
            )
            raise ImportError(msg) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout

        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                organization=organization,
                timeout=timeout,
            )
        )

    @property
    @override
    def provider_name(self) -> str:
        return "openai"

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
                message="Request cancelled", provider="openai", retryable=False
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
                        provider="openai",
                        raw=e,
                    ) from e

            async with asyncio.timeout(timeout):
                response = await self._client.chat.completions.create(**params)

            message, usage = _from_openai_response(response)

            return CompletionResponse(
                message=message,
                usage=usage,
                raw_response=response,
                reasoning_content=getattr(
                    response.choices[0].message, "reasoning_content", None
                ),
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
            raise self.map_error(e) from e

    async def stream_completion(
        self,
        params: dict[str, Any],
        on_stream_event: StreamCallback,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}
        accumulator = OpenAIStreamAccumulator(
            on_stream_event, reasoning_of=_delta_reasoning_content
        )

        stream = await self._client.chat.completions.create(**params)
        async for chunk in stream:
            if cancel and cancel.is_cancelled():
                raise LLMCancelledError(
                    message="Request cancelled",
                    provider="openai",
                    retryable=False,
                )
            accumulator.add(chunk)

        return accumulator.response()

    def build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build OpenAI API parameters from request."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": _to_openai_messages(request.messages),
        }

        # Temperature is opt-in and dropped for models that reject it.
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

        # Handle extra parameters
        if request.extra:
            extra = dict(request.extra)
            # Handle reasoning_effort for reasoning models
            if (
                self._profile.reasoning_effort_param
                and self._profile.reasoning_effort_param in extra
            ):
                params[self._profile.reasoning_effort_param] = extra.pop(
                    self._profile.reasoning_effort_param
                )
            params.update(extra)

        return params

    def map_error(self, exc: Exception) -> LLMError:
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
                    status_code=exc.status_code
                    if hasattr(exc, "status_code")
                    else 401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=str(exc),
                    provider="openai",
                    status_code=exc.status_code
                    if hasattr(exc, "status_code")
                    else 429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc):
                    return LLMContextLengthError(
                        message=str(exc),
                        provider="openai",
                        status_code=exc.status_code
                        if hasattr(exc, "status_code")
                        else 400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="openai",
                    status_code=exc.status_code
                    if hasattr(exc, "status_code")
                    else 400,
                    raw=exc,
                )

            if isinstance(exc, APIStatusError):
                status = (
                    exc.status_code if hasattr(exc, "status_code") else 500
                )
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
