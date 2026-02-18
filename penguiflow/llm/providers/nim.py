"""NVIDIA NIM provider implementation.

Uses the OpenAI SDK against NVIDIA's OpenAI-compatible chat completions API.

Supported model formats:
- "nim/qwen/qwen3.5-397b-a17b"
- "nvidia/qwen/qwen3.5-397b-a17b"
- "qwen/qwen3.5-397b-a17b"
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
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

logger = logging.getLogger("penguiflow.llm.providers.nim")


class NIMProvider(OpenAICompatibleProvider):
    """NVIDIA NIM provider using OpenAI-compatible chat completions."""

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
    _REASONING_OFF_VALUES = {"off", "none", "disabled", "false", "0"}
    _REASONING_ON_VALUES = {"minimal", "low", "medium", "high", "max", "default"}
    _UNSUPPORTED_BUDGET_KEYS = {
        "reasoning_budget_tokens",
        "thinking_budget",
        "thinking_budget_tokens",
    }

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
    ) -> None:
        """Initialize the NIM provider.

        Args:
            model: Model identifier (e.g., "nim/qwen/qwen3.5-397b-a17b").
            api_key: NIM API key (env fallback: NIM_API_KEY, then NVIDIA_API_KEY).
            base_url: Base URL override for OpenAI-compatible NIM endpoint.
            profile: Model profile override.
            timeout: Default timeout in seconds.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("OpenAI SDK not installed. Install with: pip install openai>=2.0.0") from e

        self._original_model = model
        self._model = self._normalize_model(model)
        self._profile = profile or get_profile(self._model)
        self._timeout = timeout

        api_key = api_key or os.environ.get("NIM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise ValueError(
                "NIM API key required. Set NIM_API_KEY (or NVIDIA_API_KEY) environment variable or pass api_key."
            )

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            timeout=timeout,
        )

    @property
    def provider_name(self) -> str:
        return "nim"

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def model(self) -> str:
        return self._model

    def _normalize_model(self, model: str) -> str:
        if model.startswith("nim/"):
            return model.removeprefix("nim/")
        if model.startswith("nvidia/"):
            return model.removeprefix("nvidia/")
        return model

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
            raise LLMCancelledError(message="Request cancelled", provider="nim", retryable=False)

        params = self._build_params(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                return await self._stream_completion(params, on_stream_event, timeout, cancel)

            async with asyncio.timeout(timeout):
                response = await self._client.chat.completions.create(**params)

            message, usage = self._from_openai_response(response)
            reasoning_content = self._extract_openai_reasoning_content(response.choices[0].message)
            message, reasoning_content = self._normalize_thinking_tags(message, reasoning_content)

            return CompletionResponse(
                message=message,
                usage=usage,
                raw_response=response,
                reasoning_content=reasoning_content,
                finish_reason=response.choices[0].finish_reason,
            )

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="nim",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(message="Request cancelled", provider="nim") from None
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
                        raise LLMCancelledError(message="Request cancelled", provider="nim", retryable=False)

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
                provider="nim",
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

        message = LLMMessage(role="assistant", parts=parts)
        reasoning_content = "".join(reasoning_acc) or None
        message, reasoning_content = self._normalize_thinking_tags(message, reasoning_content)

        on_stream_event(StreamEvent(done=True, usage=usage, finish_reason=finish_reason))

        return CompletionResponse(
            message=message,
            usage=usage or Usage.zero(),
            raw_response=None,
            reasoning_content=reasoning_content,
            finish_reason=finish_reason,
        )

    def _build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build NIM OpenAI-compatible parameters from request."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_openai_messages(request.messages),
        }

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

        if request.extra:
            extra = dict(request.extra)
            self._normalize_nim_reasoning_controls(extra)
            if self._profile.reasoning_effort_param and self._profile.reasoning_effort_param in extra:
                params[self._profile.reasoning_effort_param] = extra.pop(self._profile.reasoning_effort_param)
            params.update(extra)

        return params

    def _normalize_nim_reasoning_controls(self, extra: dict[str, Any]) -> None:
        """Normalize generic reasoning controls into NIM's extra_body shape."""
        reasoning_effort = extra.pop("reasoning_effort", None)

        # Databricks-style thinking budgets are not supported by NIM; ignore with warning.
        for key in self._UNSUPPORTED_BUDGET_KEYS:
            if key in extra:
                extra.pop(key, None)
                logger.warning(
                    "nim_unsupported_reasoning_budget",
                    extra={"provider": "nim", "model": self._model, "control": key},
                )

        thinking_obj = extra.get("thinking")
        has_budget_in_thinking = isinstance(thinking_obj, dict) and any(
            k in thinking_obj for k in self._UNSUPPORTED_BUDGET_KEYS | {"budget_tokens"}
        )
        if has_budget_in_thinking:
            extra.pop("thinking", None)
            logger.warning(
                "nim_unsupported_reasoning_budget",
                extra={"provider": "nim", "model": self._model, "control": "thinking.budget_tokens"},
            )

        alias_chat_kwargs = extra.pop("chat_template_kwargs", None)
        if alias_chat_kwargs is not None:
            logger.warning(
                "nim_chat_template_kwargs_alias_normalized",
                extra={"provider": "nim", "model": self._model},
            )

        raw_extra_body = extra.get("extra_body")
        if isinstance(raw_extra_body, dict):
            extra_body = dict(raw_extra_body)
        else:
            extra_body = {}
            if raw_extra_body is not None:
                logger.warning(
                    "nim_invalid_extra_body_ignored",
                    extra={"provider": "nim", "model": self._model, "type": type(raw_extra_body).__name__},
                )

        raw_chat_kwargs = extra_body.get("chat_template_kwargs")
        explicit_extra_body_thinking = isinstance(raw_chat_kwargs, dict) and "thinking" in raw_chat_kwargs
        chat_kwargs: dict[str, Any] = dict(raw_chat_kwargs) if isinstance(raw_chat_kwargs, dict) else {}

        if raw_chat_kwargs is not None and not isinstance(raw_chat_kwargs, dict):
            logger.warning(
                "nim_invalid_chat_template_kwargs_ignored",
                extra={"provider": "nim", "model": self._model, "type": type(raw_chat_kwargs).__name__},
            )

        if isinstance(alias_chat_kwargs, dict):
            # Backward-compatible alias merge: only fill missing keys.
            for k, v in alias_chat_kwargs.items():
                chat_kwargs.setdefault(k, v)
        elif alias_chat_kwargs is not None:
            logger.warning(
                "nim_invalid_chat_template_kwargs_alias_ignored",
                extra={"provider": "nim", "model": self._model, "type": type(alias_chat_kwargs).__name__},
            )

        for key in self._UNSUPPORTED_BUDGET_KEYS | {"budget_tokens"}:
            if key in chat_kwargs:
                chat_kwargs.pop(key, None)
                logger.warning(
                    "nim_unsupported_reasoning_budget",
                    extra={"provider": "nim", "model": self._model, "control": f"chat_template_kwargs.{key}"},
                )

        derived_thinking = self._map_reasoning_effort_to_thinking(reasoning_effort)
        if derived_thinking is not None and not explicit_extra_body_thinking:
            chat_kwargs["thinking"] = derived_thinking

        if chat_kwargs:
            extra_body["chat_template_kwargs"] = chat_kwargs
        elif "chat_template_kwargs" in extra_body:
            extra_body.pop("chat_template_kwargs", None)

        if extra_body:
            extra["extra_body"] = extra_body
        else:
            extra.pop("extra_body", None)

    def _map_reasoning_effort_to_thinking(self, reasoning_effort: Any) -> bool | None:
        if reasoning_effort is None:
            return None

        effort = str(reasoning_effort).strip().lower()
        if effort in self._REASONING_OFF_VALUES:
            return False
        if effort in self._REASONING_ON_VALUES:
            return True

        logger.warning(
            "nim_unknown_reasoning_effort_defaulting_to_thinking",
            extra={
                "provider": "nim",
                "model": self._model,
                "reasoning_effort": str(reasoning_effort),
            },
        )
        return True

    def _normalize_thinking_tags(
        self,
        message: LLMMessage,
        reasoning_content: str | None,
    ) -> tuple[LLMMessage, str | None]:
        """Move <think>...</think> blocks to reasoning_content when needed."""
        if reasoning_content:
            return message, reasoning_content

        tags = getattr(self._profile, "thinking_tags", None)
        if not isinstance(tags, tuple) or len(tags) != 2:
            return message, reasoning_content

        open_tag, close_tag = tags
        if not open_tag or not close_tag:
            return message, reasoning_content

        pattern = re.compile(
            re.escape(open_tag) + r"(.*?)" + re.escape(close_tag),
            re.IGNORECASE | re.DOTALL,
        )

        extracted_reasoning: list[str] = []
        normalized_parts: list[Any] = []

        for part in message.parts:
            if not isinstance(part, TextPart):
                normalized_parts.append(part)
                continue

            text = part.text
            matches = [m.group(1) for m in pattern.finditer(text) if m.group(1).strip()]
            if matches:
                extracted_reasoning.extend(matches)
                text = pattern.sub("", text)

            if text:
                normalized_parts.append(TextPart(text=text))

        if not extracted_reasoning:
            return message, reasoning_content

        normalized_message = LLMMessage(role=message.role, parts=normalized_parts)
        return normalized_message, "".join(extracted_reasoning)

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
                    provider="nim",
                    status_code=exc.status_code if hasattr(exc, "status_code") else 401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=str(exc),
                    provider="nim",
                    status_code=exc.status_code if hasattr(exc, "status_code") else 429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc):
                    return LLMContextLengthError(
                        message=str(exc),
                        provider="nim",
                        status_code=exc.status_code if hasattr(exc, "status_code") else 400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="nim",
                    status_code=exc.status_code if hasattr(exc, "status_code") else 400,
                    raw=exc,
                )

            if isinstance(exc, APIStatusError):
                status = exc.status_code if hasattr(exc, "status_code") else 500
                if status >= 500:
                    return LLMServerError(
                        message=str(exc),
                        provider="nim",
                        status_code=status,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="nim",
                    status_code=status,
                    raw=exc,
                )

            if isinstance(exc, APIConnectionError):
                return LLMServerError(
                    message=str(exc),
                    provider="nim",
                    raw=exc,
                )

        except ImportError:
            pass

        return LLMError(
            message=str(exc),
            provider="nim",
            raw=exc,
        )
