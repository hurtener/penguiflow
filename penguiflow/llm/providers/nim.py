"""NVIDIA NIM provider implementation.

Uses the OpenAI SDK against NVIDIA's OpenAI-compatible chat completions API.

Supported model formats:
- "nim/qwen/qwen3.5-397b-a17b"
- "nvidia/qwen/qwen3.5-397b-a17b"
- "nvidia/nemotron-3-nano-30b-a3b"
- "qwen/qwen3.5-397b-a17b"
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

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
    LLMMessage,
    LLMRequest,
    TextPart,
    Usage,
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

    from ..types import CancelToken, StreamCallback

logger = logging.getLogger("penguiflow.llm.providers.nim")


_ControlT = TypeVar("_ControlT")


class NIMProvider(OpenAICompatibleProvider):
    """NVIDIA NIM provider using OpenAI-compatible chat completions."""

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
    _REASONING_OFF_VALUES: ClassVar[frozenset[str]] = frozenset(
        {"off", "none", "disabled", "false", "0"}
    )
    _REASONING_ON_VALUES: ClassVar[frozenset[str]] = frozenset(
        {
            "minimal",
            "low",
            "medium",
            "high",
            "max",
            "default",
        }
    )
    _UNSUPPORTED_BUDGET_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "reasoning_budget_tokens",
            "thinking_budget",
            "thinking_budget_tokens",
        }
    )
    _MODEL_REASONING_CONTROL_MAP: tuple[tuple[str, str, Any, Any], ...] = (
        (
            "qwen/qwen3.5-397b-a17b",
            "thinking_mode",
            "thinking",
            "non-thinking",
        ),
        ("stepfun-ai/step-3.5-flash", "reasoning_mode", "thinking", "none"),
        ("z-ai/glm5", "reasoning_mode", "thinking", "none"),
        ("moonshotai/kimi-k2.5", "reasoning_mode", "thinking", "none"),
        (
            "deepseek-ai/deepseek-v3.1-terminus",
            "reasoning_mode",
            "thinking",
            "none",
        ),
        ("minimaxai/minimax-m2.1", "enable_thinking", True, False),
    )

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize the NIM provider.

        Args:
            model: Model identifier (e.g., "nim/qwen/qwen3.5-397b-a17b").
            api_key: NIM API key (env fallback: NIM_API_KEY, then
                NVIDIA_API_KEY).
            base_url: Base URL override for OpenAI-compatible NIM endpoint.
            profile: Model profile override.
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

        self._original_model = model
        self._model = self._normalize_model(model)
        self._profile = profile or get_profile(self._model)
        self._timeout = timeout

        api_key = (
            api_key
            or os.environ.get("NIM_API_KEY")
            or os.environ.get("NVIDIA_API_KEY")
        )
        if not api_key:
            msg = (
                "NIM API key required. Set NIM_API_KEY (or "
                "NVIDIA_API_KEY) environment variable or pass api_key."
            )
            raise ValueError(msg)

        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                api_key=api_key,
                base_url=base_url or self.DEFAULT_BASE_URL,
                timeout=timeout,
            )
        )

    @property
    @override
    def provider_name(self) -> str:
        return "nim"

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

    def _normalize_model(self, model: str) -> str:
        if model.startswith("nim/"):
            return model.removeprefix("nim/")
        if model.startswith("nvidia/"):
            remainder = model.removeprefix("nvidia/")
            # Treat nvidia/<vendor>/<model> as provider alias (legacy style),
            # but keep nvidia/<model> intact because NVIDIA's API expects it.
            if "/" in remainder:
                return remainder
            return model
        return model

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
                message="Request cancelled", provider="nim", retryable=False
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
                        provider="nim",
                        raw=e,
                    ) from e

            async with asyncio.timeout(timeout):
                response = await self._client.chat.completions.create(**params)

            message, usage = _from_openai_response(response)
            reasoning_content = _extract_openai_reasoning_content(
                response.choices[0].message
            )
            message, reasoning_content = self._normalize_thinking_tags(
                message, reasoning_content
            )

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
            raise LLMCancelledError(
                message="Request cancelled", provider="nim"
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
                    provider="nim",
                    retryable=False,
                )
            accumulator.add(chunk)

        message, reasoning_content = self._normalize_thinking_tags(
            accumulator.message(), accumulator.reasoning_content
        )
        accumulator.emit_done()
        return CompletionResponse(
            message=message,
            usage=accumulator.usage or Usage.zero(),
            raw_response=None,
            reasoning_content=reasoning_content,
            finish_reason=accumulator.finish_reason,
        )

    def build_params(self, request: LLMRequest) -> dict[str, Any]:
        """Build NIM OpenAI-compatible parameters from request."""
        messages = _to_openai_messages(request.messages)
        messages = self._reorder_system_messages(messages)
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
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
            self._normalize_nim_reasoning_controls(extra)
            if (
                self._profile.reasoning_effort_param
                and self._profile.reasoning_effort_param in extra
            ):
                params[self._profile.reasoning_effort_param] = extra.pop(
                    self._profile.reasoning_effort_param
                )
            params.update(extra)

        return params

    def _reorder_system_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Move all system/developer messages first, collapsed into one."""
        system_like_roles = {"system", "developer"}
        seen_non_system = False
        needs_reorder = False
        for msg in messages:
            role = str(msg.get("role", "")).lower()
            if role in system_like_roles:
                if seen_non_system:
                    needs_reorder = True
                    break
            else:
                seen_non_system = True

        systems = [
            msg
            for msg in messages
            if str(msg.get("role", "")).lower() in system_like_roles
        ]
        others = [
            msg
            for msg in messages
            if str(msg.get("role", "")).lower() not in system_like_roles
        ]

        normalized = messages
        if needs_reorder:
            logger.warning(
                "nim_reordered_system_messages",
                extra={
                    "provider": "nim",
                    "model": self._model,
                    "system_count": len(systems),
                    "non_system_count": len(others),
                },
            )
            normalized = [*systems, *others]

        leading_system_count = 0
        merged_parts: list[str] = []
        for msg in normalized:
            if str(msg.get("role", "")).lower() not in system_like_roles:
                break
            leading_system_count += 1
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                merged_parts.append(content.strip())

        if leading_system_count > 1:
            merged_content = "\n\n".join(merged_parts)
            collapsed = [
                {"role": "system", "content": merged_content},
                *normalized[leading_system_count:],
            ]
            logger.warning(
                "nim_collapsed_system_messages",
                extra={
                    "provider": "nim",
                    "model": self._model,
                    "collapsed_count": leading_system_count,
                },
            )
            return collapsed

        return normalized

    def _normalize_nim_reasoning_controls(self, extra: dict[str, Any]) -> None:
        """Normalize generic reasoning controls into NIM's extra_body shape."""
        reasoning_effort = extra.pop("reasoning_effort", None)
        control_key, on_value, off_value = (
            self._resolve_reasoning_control_spec()
        )
        self._drop_unsupported_budgets(extra)

        alias_chat_kwargs = extra.pop("chat_template_kwargs", None)
        if alias_chat_kwargs is not None:
            logger.warning(
                "nim_chat_template_kwargs_alias_normalized",
                extra={"provider": "nim", "model": self._model},
            )

        extra_body = self._extra_body(extra.get("extra_body"))
        raw_chat_kwargs = extra_body.get("chat_template_kwargs")
        explicit_extra_body_control = isinstance(raw_chat_kwargs, dict) and (
            control_key in raw_chat_kwargs or "thinking" in raw_chat_kwargs
        )
        chat_kwargs = self._chat_template_kwargs(
            raw_chat_kwargs, alias_chat_kwargs
        )

        # Backward compatibility for legacy key: map explicit "thinking" to
        # model key.
        if (
            "thinking" in chat_kwargs
            and control_key != "thinking"
            and control_key not in chat_kwargs
        ):
            thinking_value = bool(chat_kwargs.pop("thinking"))
            chat_kwargs[control_key] = (
                on_value if thinking_value else off_value
            )

        derived_control = self._map_reasoning_effort_to_control_value(
            reasoning_effort,
            on_value=on_value,
            off_value=off_value,
        )
        if derived_control is not None and not explicit_extra_body_control:
            chat_kwargs[control_key] = derived_control

        if control_key != "thinking":
            # Avoid sending both legacy and model-native keys in the same
            # request.
            chat_kwargs.pop("thinking", None)

        if chat_kwargs:
            extra_body["chat_template_kwargs"] = chat_kwargs
        else:
            extra_body.pop("chat_template_kwargs", None)

        if extra_body:
            extra["extra_body"] = extra_body
        else:
            extra.pop("extra_body", None)

    def _warn_unsupported_budget(self, control: str) -> None:
        logger.warning(
            "nim_unsupported_reasoning_budget",
            extra={
                "provider": "nim",
                "model": self._model,
                "control": control,
            },
        )

    def _warn_ignored_type(self, event: str, value: object) -> None:
        logger.warning(
            event,
            extra={
                "provider": "nim",
                "model": self._model,
                "type": type(value).__name__,
            },
        )

    def _drop_unsupported_budgets(self, extra: dict[str, Any]) -> None:
        """Drop Databricks-style thinking budgets, which NIM ignores."""
        for key in self._UNSUPPORTED_BUDGET_KEYS:
            if key in extra:
                extra.pop(key, None)
                self._warn_unsupported_budget(key)

        thinking_obj = extra.get("thinking")
        if isinstance(thinking_obj, dict) and any(
            k in thinking_obj
            for k in self._UNSUPPORTED_BUDGET_KEYS | {"budget_tokens"}
        ):
            extra.pop("thinking", None)
            self._warn_unsupported_budget("thinking.budget_tokens")

    def _extra_body(self, raw_extra_body: object) -> dict[str, Any]:
        """Copy a dict ``extra_body``; anything else is ignored."""
        if isinstance(raw_extra_body, dict):
            return dict(raw_extra_body)
        if raw_extra_body is not None:
            self._warn_ignored_type(
                "nim_invalid_extra_body_ignored", raw_extra_body
            )
        return {}

    def _chat_template_kwargs(
        self, raw_chat_kwargs: object, alias_chat_kwargs: object
    ) -> dict[str, Any]:
        """Merge ``chat_template_kwargs`` with its legacy top-level alias."""
        chat_kwargs: dict[str, Any] = (
            dict(raw_chat_kwargs) if isinstance(raw_chat_kwargs, dict) else {}
        )
        if raw_chat_kwargs is not None and not isinstance(
            raw_chat_kwargs, dict
        ):
            self._warn_ignored_type(
                "nim_invalid_chat_template_kwargs_ignored", raw_chat_kwargs
            )

        if isinstance(alias_chat_kwargs, dict):
            # Backward-compatible alias merge: only fill missing keys.
            for k, v in alias_chat_kwargs.items():
                chat_kwargs.setdefault(k, v)
        elif alias_chat_kwargs is not None:
            self._warn_ignored_type(
                "nim_invalid_chat_template_kwargs_alias_ignored",
                alias_chat_kwargs,
            )

        for key in self._UNSUPPORTED_BUDGET_KEYS | {"budget_tokens"}:
            if key in chat_kwargs:
                chat_kwargs.pop(key, None)
                self._warn_unsupported_budget(f"chat_template_kwargs.{key}")
        return chat_kwargs

    def _resolve_reasoning_control_spec(self) -> tuple[str, Any, Any]:
        model = self._model.lower()
        for (
            prefix,
            key,
            on_value,
            off_value,
        ) in self._MODEL_REASONING_CONTROL_MAP:
            if model.startswith(prefix):
                return key, on_value, off_value
        return "thinking", True, False

    def _map_reasoning_effort_to_control_value(
        self,
        reasoning_effort: object,
        *,
        on_value: _ControlT,
        off_value: _ControlT,
    ) -> _ControlT | None:
        if reasoning_effort is None:
            return None

        effort = str(reasoning_effort).strip().lower()
        if effort in self._REASONING_OFF_VALUES:
            return off_value
        if effort in self._REASONING_ON_VALUES:
            return on_value

        logger.warning(
            "nim_unknown_reasoning_effort_defaulting_to_thinking",
            extra={
                "provider": "nim",
                "model": self._model,
                "reasoning_effort": str(reasoning_effort),
            },
        )
        return on_value

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
            matches = [
                m.group(1)
                for m in pattern.finditer(text)
                if m.group(1).strip()
            ]
            if matches:
                extracted_reasoning.extend(matches)
                text = pattern.sub("", text)

            if text:
                normalized_parts.append(TextPart(text=text))

        if not extracted_reasoning:
            return message, reasoning_content

        normalized_message = LLMMessage(
            role=message.role, parts=normalized_parts
        )
        return normalized_message, "".join(extracted_reasoning)

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
                    provider="nim",
                    status_code=exc.status_code
                    if hasattr(exc, "status_code")
                    else 401,
                    raw=exc,
                )

            if isinstance(exc, RateLimitError):
                return LLMRateLimitError(
                    message=str(exc),
                    provider="nim",
                    status_code=exc.status_code
                    if hasattr(exc, "status_code")
                    else 429,
                    raw=exc,
                )

            if isinstance(exc, BadRequestError):
                if is_context_length_error(exc):
                    return LLMContextLengthError(
                        message=str(exc),
                        provider="nim",
                        status_code=exc.status_code
                        if hasattr(exc, "status_code")
                        else 400,
                        raw=exc,
                    )
                return LLMInvalidRequestError(
                    message=str(exc),
                    provider="nim",
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
