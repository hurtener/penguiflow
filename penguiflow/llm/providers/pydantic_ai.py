"""pydantic-ai transport provider.

Implements the ``Provider`` ABC on top of pydantic-ai's direct model layer
(``pydantic_ai.direct.model_request`` / ``model_request_stream``), delegating
the per-provider wire handling (auth, request shaping, streaming-delta
assembly, reasoning normalization) to pydantic-ai while PenguiFlow keeps
profiles, output strategies, fallback, pricing, and tracing.

This module imports ``pydantic_ai`` at import time and therefore must only be
imported lazily (the adapter does so when ``transport="pydantic-ai"``
resolves). Install with ``pip install penguiflow-lite[pydantic-ai]``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai import exceptions as pai_exceptions
from pydantic_ai import messages as pai_messages
from pydantic_ai.direct import model_request, model_request_stream
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.tools import ToolDefinition
from typing_extensions import override

from ..errors import (
    LLMCancelledError,
    LLMError,
    LLMTimeoutError,
    map_status_to_error,
)
from ..profiles import ModelProfile, get_profile
from ..types import (
    AudioPart,
    CancelToken,
    CompletionResponse,
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamCallback,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from ._params import resolve_temperature
from .base import Provider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.settings import ModelSettings

logger = logging.getLogger("penguiflow.llm.providers.pydantic_ai")

PROVIDER_NAME = "pydantic-ai"


def _normalize_databricks_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base.endswith("/serving-endpoints"):
        base = f"{base}/serving-endpoints"
    return base


_PAI_PREFIXES = (
    ("openai/", "openai"),
    ("anthropic/", "anthropic"),
    ("google/", "google-gla"),
    ("bedrock/", "bedrock"),
)
_PAI_BARE_PREFIXES = (("claude", "anthropic"), ("gemini", "google-gla"))


def _openrouter_model(route: str, *, api_key: str | None) -> Model | str:
    """Return an OpenRouter model, keyed when ``api_key`` is given."""
    if api_key is None:
        return f"openrouter:{route}"
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import (
        OpenRouterProvider as PaiOpenRouterProvider,
    )

    return OpenRouterModel(
        route, provider=PaiOpenRouterProvider(api_key=api_key)
    )


def _openai_chat_model(
    route: str, *, base_url: str, api_key: str | None
) -> Model:
    """Return an OpenAI-compatible chat model served at ``base_url``."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import (
        OpenAIProvider as PaiOpenAIProvider,
    )

    return OpenAIChatModel(
        route, provider=PaiOpenAIProvider(base_url=base_url, api_key=api_key)
    )


def _databricks_model(
    endpoint: str, *, api_key: str | None, base_url: str | None
) -> Model:
    """Return a Databricks serving endpoint as an OpenAI chat model."""
    resolved_base = (
        base_url
        or os.environ.get("DATABRICKS_API_BASE")
        or os.environ.get("DATABRICKS_HOST")
    )
    resolved_key = (
        api_key
        or os.environ.get("DATABRICKS_TOKEN")
        or os.environ.get("DATABRICKS_API_KEY")
    )
    if not resolved_base or not resolved_key:
        msg = (
            "Databricks via the pydantic-ai transport "
            "requires a base_url/api_key "
            "(or DATABRICKS_API_BASE / DATABRICKS_TOKEN env vars)."
        )
        raise ValueError(msg)
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.profiles.openai import (
        OpenAIModelProfile,
        openai_model_profile,
    )
    from pydantic_ai.providers.openai import (
        OpenAIProvider as PaiOpenAIProvider,
    )

    # Merge onto the default profile (a bare OpenAIModelProfile would wipe
    # defaults such as supports_json_schema_output). Databricks rejects the
    # OpenAI `strict` tool field (Phase 0 spike).
    profile = openai_model_profile(endpoint).update(
        OpenAIModelProfile(openai_supports_strict_tool_definition=False)
    )
    return OpenAIChatModel(
        endpoint,
        provider=PaiOpenAIProvider(
            base_url=_normalize_databricks_base_url(resolved_base),
            api_key=resolved_key,
        ),
        profile=profile,
    )


def _to_response_parts(
    message: LLMMessage,
) -> list[pai_messages.ModelResponsePart]:
    """Map an assistant message's text and tool calls to response parts."""
    response_parts: list[pai_messages.ModelResponsePart] = []
    for part in message.parts:
        if isinstance(part, TextPart) and part.text:
            response_parts.append(pai_messages.TextPart(content=part.text))
        elif isinstance(part, ToolCallPart):
            response_parts.append(
                pai_messages.ToolCallPart(
                    tool_name=part.name,
                    args=part.arguments_json,
                    tool_call_id=part.call_id or "",
                )
            )
    return response_parts


def _to_user_content(message: LLMMessage) -> str | list[Any]:
    parts = list(message.parts)
    if all(isinstance(p, TextPart) for p in parts):
        return message.text
    content: list[Any] = []
    for part in parts:
        if isinstance(part, TextPart):
            if part.text:
                content.append(part.text)
        elif isinstance(part, (ImagePart, AudioPart)):
            content.append(
                pai_messages.BinaryContent(
                    data=part.data, media_type=part.media_type
                )
            )
    return content


def _build_parameters(request: LLMRequest) -> ModelRequestParameters | None:
    function_tools = [
        ToolDefinition(
            name=tool.name,
            parameters_json_schema=tool.json_schema,
            description=tool.description,
        )
        for tool in (request.tools or ())
    ]
    if request.structured_output is not None:
        spec = request.structured_output
        return ModelRequestParameters(
            output_mode="native",
            output_object=OutputObjectDefinition(
                json_schema=spec.json_schema,
                name=spec.name,
                strict=spec.strict,
            ),
            function_tools=function_tools,
            allow_text_output=False,
        )
    if function_tools:
        return ModelRequestParameters(
            function_tools=function_tools, allow_text_output=True
        )
    return None


class PydanticAIProvider(Provider):
    """Provider that routes completions through pydantic-ai's model layer.

    Model strings use PenguiFlow's existing naming (``gpt-4o``,
    ``anthropic/claude-...``, ``openrouter/...``, ``databricks-...``) and are
    resolved to pydantic-ai models internally, so callers never deal with two
    naming schemes. ``self.model`` keeps the original string — pricing and
    profile lookups stay keyed exactly as on the native transport.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
        **_ignored: object,
    ) -> None:
        """Resolve ``model`` to a pydantic-ai model; ignore other options."""
        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout
        self._pai_model = self._build_model(
            model, api_key=api_key, base_url=base_url
        )

    @property
    def pai_model(self) -> Model | str:
        """The pydantic-ai model (or model name) requests run on."""
        return self._pai_model

    # ------------------------------------------------------------------
    # Provider ABC surface
    # ------------------------------------------------------------------

    @property
    @override
    def provider_name(self) -> str:
        return PROVIDER_NAME

    @property
    @override
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    @override
    def model(self) -> str:
        return self._model

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
        if cancel and cancel.is_cancelled():
            raise LLMCancelledError(
                message="Request cancelled",
                provider=PROVIDER_NAME,
                retryable=False,
            )

        messages = self.to_pai_messages(request.messages)
        settings = self.build_settings(request)
        parameters = _build_parameters(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event is not None:
                async with asyncio.timeout(timeout):
                    return await self.stream_completion(
                        messages,
                        settings,
                        parameters,
                        on_stream_event,
                        cancel,
                    )
            async with asyncio.timeout(timeout):
                response = await model_request(
                    self._pai_model,
                    messages,
                    model_settings=settings,
                    model_request_parameters=parameters,
                )
            return self._to_completion_response(
                response, raw_response=response
            )
        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider=PROVIDER_NAME,
                raw=e,
            ) from e
        except asyncio.CancelledError as e:
            raise LLMCancelledError(
                message="Request cancelled", provider=PROVIDER_NAME, raw=e
            ) from e
        except LLMError:
            raise
        except Exception as e:
            raise self.map_error(e) from e

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    def _build_model(
        self, model: str, *, api_key: str | None, base_url: str | None
    ) -> Model | str:
        """Resolve a PenguiFlow model string to a pydantic-ai model.

        Mirrors ``create_provider``'s prefix routing. Returns a string form
        (pydantic-ai resolves keys from env vars) unless explicit credentials
        or a base_url require constructing a model object.
        """
        if model.startswith("openrouter/"):
            return _openrouter_model(
                model.removeprefix("openrouter/"), api_key=api_key
            )
        if model.startswith(("databricks/", "databricks-")):
            return _databricks_model(
                model.removeprefix("databricks/"),
                api_key=api_key,
                base_url=base_url,
            )
        if base_url and (
            model.startswith("nim/")
            or not model.startswith(("openai/", "anthropic/", "google/"))
        ):
            # NIM (or any model with a base_url) speaks the OpenAI chat API.
            # NIM without base_url falls through to the OpenAI default.
            return _openai_chat_model(
                model.removeprefix("nim/"), base_url=base_url, api_key=api_key
            )

        for prefix, pai_provider in _PAI_PREFIXES:
            if model.startswith(prefix):
                return self._keyed_or_string(
                    pai_provider, model.removeprefix(prefix), api_key
                )
        for bare_prefix, pai_provider in _PAI_BARE_PREFIXES:
            if model.startswith(bare_prefix):
                return self._keyed_or_string(pai_provider, model, api_key)
        if model.startswith(("anthropic.", "amazon.", "meta.")):
            return f"bedrock:{model}"
        # Default: OpenAI-compatible (matches create_provider's fallback).
        return self._keyed_or_string("openai", model, api_key)

    def _keyed_or_string(
        self, pai_provider: str, route: str, api_key: str | None
    ) -> Model | str:
        if api_key is None:
            return f"{pai_provider}:{route}"
        if pai_provider == "openai":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import (
                OpenAIProvider as PaiOpenAIProvider,
            )

            return OpenAIChatModel(
                route, provider=PaiOpenAIProvider(api_key=api_key)
            )
        if pai_provider == "anthropic":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import (
                AnthropicProvider as PaiAnthropicProvider,
            )

            return AnthropicModel(
                route, provider=PaiAnthropicProvider(api_key=api_key)
            )
        if pai_provider == "google-gla":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import (
                GoogleProvider as PaiGoogleProvider,
            )

            return GoogleModel(
                route, provider=PaiGoogleProvider(api_key=api_key)
            )
        return f"{pai_provider}:{route}"

    # ------------------------------------------------------------------
    # Request mapping
    # ------------------------------------------------------------------

    def to_pai_messages(
        self, messages: Sequence[LLMMessage]
    ) -> list[pai_messages.ModelMessage]:
        """Convert PenguiFlow messages to pydantic-ai's request/response turns.

        Consecutive system/user/tool messages merge into one ``ModelRequest``;
        assistant messages become ``ModelResponse`` turns so providers see a
        coherent conversation history.
        """
        result: list[pai_messages.ModelMessage] = []
        pending_request: list[pai_messages.ModelRequestPart] = []

        def flush_request() -> None:
            nonlocal pending_request
            if pending_request:
                result.append(pai_messages.ModelRequest(parts=pending_request))
                pending_request = []

        for message in messages:
            role = str(message.role).lower()
            if role == "assistant":
                flush_request()
                response_parts = _to_response_parts(message)
                if response_parts:
                    result.append(
                        pai_messages.ModelResponse(parts=response_parts)
                    )
                continue
            request_parts = self._to_request_parts(message, role)
            if request_parts is None:
                logger.warning(
                    "pydantic_ai_unknown_role_dropped", extra={"role": role}
                )
            else:
                pending_request.extend(request_parts)

        flush_request()
        return result

    def _to_request_parts(
        self, message: LLMMessage, role: str
    ) -> list[pai_messages.ModelRequestPart] | None:
        """Map a system/user/tool message; ``None`` for an unknown role."""
        if role in ("system", "developer"):
            return [pai_messages.SystemPromptPart(content=message.text)]
        if role == "user":
            return [
                pai_messages.UserPromptPart(content=_to_user_content(message))
            ]
        if role == "tool":
            return [
                pai_messages.ToolReturnPart(
                    tool_name=part.name,
                    content=part.result_json,
                    tool_call_id=part.call_id or "",
                )
                for part in message.parts
                if isinstance(part, ToolResultPart)
            ]
        return None

    def build_settings(self, request: LLMRequest) -> ModelSettings | None:
        """Translate ``request`` options into pydantic-ai model settings."""
        settings: dict[str, Any] = {}
        temperature = resolve_temperature(
            self._profile,
            request.temperature,
            model=self._model,
            forced_off=self.temperature_unsupported,
        )
        if temperature is not None:
            settings["temperature"] = temperature
        if request.max_tokens is not None:
            settings["max_tokens"] = request.max_tokens
        if request.extra:
            extra = dict(request.extra)
            reasoning_effort = extra.pop("reasoning_effort", None)
            extra.pop(
                "reasoning_enabled", None
            )  # openrouter-native control; not portable
            extra_body: dict[str, Any] = dict(
                extra.pop("extra_body", {}) or {}
            )
            if isinstance(reasoning_effort, str) and reasoning_effort:
                extra_body["reasoning_effort"] = reasoning_effort
            extra_body.update(extra)
            if extra_body:
                settings["extra_body"] = extra_body
        # ModelSettings is a TypedDict; the keys above are all members of it.
        return cast("ModelSettings", settings) if settings else None

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    def _to_completion_response(
        self,
        response: pai_messages.ModelResponse,
        *,
        raw_response: object,
    ) -> CompletionResponse:
        parts: list[TextPart | ToolCallPart] = []
        reasoning: list[str] = []
        for part in response.parts:
            if isinstance(part, pai_messages.TextPart):
                parts.append(TextPart(text=part.content))
            elif isinstance(part, pai_messages.ThinkingPart):
                if part.content:
                    reasoning.append(part.content)
            elif isinstance(part, pai_messages.ToolCallPart):
                parts.append(
                    ToolCallPart(
                        name=part.tool_name,
                        arguments_json=part.args_as_json_str(),
                        call_id=part.tool_call_id or None,
                    )
                )
        usage = self._to_usage(response.usage)
        finish_reason = response.finish_reason
        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=tuple(parts)),
            usage=usage,
            raw_response=raw_response,
            reasoning_content="".join(reasoning) or None,
            finish_reason=str(finish_reason)
            if finish_reason is not None
            else None,
        )

    @staticmethod
    def _to_usage(usage: object) -> Usage:
        if usage is None:
            return Usage.zero()
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total = getattr(usage, "total_tokens", None)
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(total)
            if total is not None
            else input_tokens + output_tokens,
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_completion(
        self,
        messages: list[pai_messages.ModelMessage],
        settings: ModelSettings | None,
        parameters: ModelRequestParameters | None,
        on_stream_event: StreamCallback,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Stream a pydantic-ai request, forwarding events as they arrive."""
        async with model_request_stream(
            self._pai_model,
            messages,
            model_settings=settings,
            model_request_parameters=parameters,
        ) as stream:
            async for event in stream:
                if cancel and cancel.is_cancelled():
                    raise LLMCancelledError(
                        message="Request cancelled during streaming",
                        provider=PROVIDER_NAME,
                        retryable=False,
                    )
                self._emit_stream_event(event, on_stream_event)
            response = stream.get()

        completion = self._to_completion_response(response, raw_response=None)
        on_stream_event(
            StreamEvent(
                done=True,
                usage=completion.usage,
                finish_reason=completion.finish_reason,
            )
        )
        return completion

    @staticmethod
    def _emit_stream_event(
        event: object, on_stream_event: StreamCallback
    ) -> None:
        if isinstance(event, pai_messages.PartStartEvent):
            part = event.part
            if isinstance(part, pai_messages.TextPart) and part.content:
                on_stream_event(StreamEvent(delta_text=part.content))
            elif isinstance(part, pai_messages.ThinkingPart) and part.content:
                on_stream_event(StreamEvent(delta_reasoning=part.content))
        elif isinstance(event, pai_messages.PartDeltaEvent):
            delta = event.delta
            if (
                isinstance(delta, pai_messages.TextPartDelta)
                and delta.content_delta
            ):
                on_stream_event(StreamEvent(delta_text=delta.content_delta))
            elif (
                isinstance(delta, pai_messages.ThinkingPartDelta)
                and delta.content_delta
            ):
                on_stream_event(
                    StreamEvent(delta_reasoning=delta.content_delta)
                )

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    def map_error(self, exc: Exception) -> LLMError:
        """Map a pydantic-ai or SDK exception to the matching LLMError."""
        if isinstance(exc, pai_exceptions.ModelHTTPError):
            return map_status_to_error(
                exc.status_code,
                str(exc),
                provider=PROVIDER_NAME,
                raw=exc,
            )
        if isinstance(exc, pai_exceptions.UnexpectedModelBehavior):
            return LLMError(
                message=str(exc),
                provider=PROVIDER_NAME,
                retryable=False,
                raw=exc,
            )
        return LLMError(message=str(exc), provider=PROVIDER_NAME, raw=exc)


__all__ = ["PydanticAIProvider"]
