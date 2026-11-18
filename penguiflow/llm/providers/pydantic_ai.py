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
from pydantic_ai.models import Model, ModelRequestParameters, OutputObjectDefinition
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

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

logger = logging.getLogger("penguiflow.llm.providers.pydantic_ai")

PROVIDER_NAME = "pydantic-ai"


def _normalize_databricks_base_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base.endswith("/serving-endpoints"):
        base = f"{base}/serving-endpoints"
    return base


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
        **_ignored: Any,
    ) -> None:
        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout
        self._pai_model = self._build_model(model, api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------
    # Provider ABC surface
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

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
        if cancel and cancel.is_cancelled():
            raise LLMCancelledError(message="Request cancelled", provider=PROVIDER_NAME, retryable=False)

        messages = self._to_pai_messages(request.messages)
        settings = self._build_settings(request)
        parameters = self._build_parameters(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event is not None:
                return await self._stream_completion(
                    messages,
                    settings,
                    parameters,
                    on_stream_event,
                    timeout,
                    cancel,
                )
            async with asyncio.timeout(timeout):
                response = await model_request(
                    self._pai_model,
                    messages,
                    model_settings=settings,
                    model_request_parameters=parameters,
                )
            return self._to_completion_response(response, raw_response=response)
        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider=PROVIDER_NAME,
                raw=e,
            ) from e
        except asyncio.CancelledError as e:
            raise LLMCancelledError(message="Request cancelled", provider=PROVIDER_NAME, raw=e) from e
        except LLMError:
            raise
        except Exception as e:
            raise self._map_error(e) from e

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    def _build_model(self, model: str, *, api_key: str | None, base_url: str | None) -> Model | str:
        """Resolve a PenguiFlow model string to a pydantic-ai model.

        Mirrors ``create_provider``'s prefix routing. Returns a string form
        (pydantic-ai resolves keys from env vars) unless explicit credentials
        or a base_url require constructing a model object.
        """
        if model.startswith("openrouter/"):
            route = model.removeprefix("openrouter/")
            if api_key is not None:
                from pydantic_ai.models.openrouter import OpenRouterModel
                from pydantic_ai.providers.openrouter import OpenRouterProvider as PaiOpenRouterProvider

                return OpenRouterModel(route, provider=PaiOpenRouterProvider(api_key=api_key))
            return f"openrouter:{route}"

        if model.startswith(("databricks/", "databricks-")):
            endpoint = model.removeprefix("databricks/")
            resolved_base = base_url or os.environ.get("DATABRICKS_API_BASE") or os.environ.get("DATABRICKS_HOST")
            resolved_key = (
                api_key or os.environ.get("DATABRICKS_TOKEN") or os.environ.get("DATABRICKS_API_KEY")
            )
            if not resolved_base or not resolved_key:
                raise ValueError(
                    "Databricks via the pydantic-ai transport requires a base_url/api_key "
                    "(or DATABRICKS_API_BASE / DATABRICKS_TOKEN env vars)."
                )
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.profiles.openai import OpenAIModelProfile, openai_model_profile
            from pydantic_ai.providers.openai import OpenAIProvider as PaiOpenAIProvider

            # Merge onto the default profile (a bare OpenAIModelProfile would
            # wipe defaults such as supports_json_schema_output). Databricks
            # rejects the OpenAI `strict` tool field (Phase 0 spike).
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

        if model.startswith("nim/") or (base_url and not model.startswith(("openai/", "anthropic/", "google/"))):
            route = model.removeprefix("nim/")
            if base_url:
                from pydantic_ai.models.openai import OpenAIChatModel
                from pydantic_ai.providers.openai import OpenAIProvider as PaiOpenAIProvider

                return OpenAIChatModel(route, provider=PaiOpenAIProvider(base_url=base_url, api_key=api_key))
            # NIM without base_url falls through to the OpenAI-compatible default.

        prefix_map = (
            ("openai/", "openai"),
            ("anthropic/", "anthropic"),
            ("google/", "google-gla"),
            ("bedrock/", "bedrock"),
        )
        for prefix, pai_provider in prefix_map:
            if model.startswith(prefix):
                return self._keyed_or_string(pai_provider, model.removeprefix(prefix), api_key)
        if model.startswith("claude"):
            return self._keyed_or_string("anthropic", model, api_key)
        if model.startswith("gemini"):
            return self._keyed_or_string("google-gla", model, api_key)
        if model.startswith(("anthropic.", "amazon.", "meta.")):
            return f"bedrock:{model}"
        # Default: OpenAI-compatible (matches create_provider's fallback).
        return self._keyed_or_string("openai", model, api_key)

    def _keyed_or_string(self, pai_provider: str, route: str, api_key: str | None) -> Model | str:
        if api_key is None:
            return f"{pai_provider}:{route}"
        if pai_provider == "openai":
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider as PaiOpenAIProvider

            return OpenAIChatModel(route, provider=PaiOpenAIProvider(api_key=api_key))
        if pai_provider == "anthropic":
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider as PaiAnthropicProvider

            return AnthropicModel(route, provider=PaiAnthropicProvider(api_key=api_key))
        if pai_provider == "google-gla":
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google import GoogleProvider as PaiGoogleProvider

            return GoogleModel(route, provider=PaiGoogleProvider(api_key=api_key))
        return f"{pai_provider}:{route}"

    # ------------------------------------------------------------------
    # Request mapping
    # ------------------------------------------------------------------

    def _to_pai_messages(self, messages: Sequence[LLMMessage]) -> list[pai_messages.ModelMessage]:
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
            if role in ("system", "developer"):
                pending_request.append(pai_messages.SystemPromptPart(content=message.text))
            elif role == "user":
                pending_request.append(pai_messages.UserPromptPart(content=self._to_user_content(message)))
            elif role == "tool":
                for part in message.parts:
                    if isinstance(part, ToolResultPart):
                        pending_request.append(
                            pai_messages.ToolReturnPart(
                                tool_name=part.name,
                                content=part.result_json,
                                tool_call_id=part.call_id or "",
                            )
                        )
            elif role == "assistant":
                flush_request()
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
                if response_parts:
                    result.append(pai_messages.ModelResponse(parts=response_parts))
            else:
                logger.warning("pydantic_ai_unknown_role_dropped", extra={"role": role})

        flush_request()
        return result

    def _to_user_content(self, message: LLMMessage) -> str | list[Any]:
        parts = list(message.parts)
        if all(isinstance(p, TextPart) for p in parts):
            return message.text
        content: list[Any] = []
        for part in parts:
            if isinstance(part, TextPart):
                if part.text:
                    content.append(part.text)
            elif isinstance(part, ImagePart):
                content.append(pai_messages.BinaryContent(data=part.data, media_type=part.media_type))
            elif isinstance(part, AudioPart):
                content.append(pai_messages.BinaryContent(data=part.data, media_type=part.media_type))
        return content

    def _build_settings(self, request: LLMRequest) -> ModelSettings | None:
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
            extra.pop("reasoning_enabled", None)  # openrouter-native control; not portable
            extra_body: dict[str, Any] = dict(extra.pop("extra_body", {}) or {})
            if isinstance(reasoning_effort, str) and reasoning_effort:
                extra_body["reasoning_effort"] = reasoning_effort
            extra_body.update(extra)
            if extra_body:
                settings["extra_body"] = extra_body
        # ModelSettings is a TypedDict; the keys above are all members of it.
        return cast("ModelSettings", settings) if settings else None

    def _build_parameters(self, request: LLMRequest) -> ModelRequestParameters | None:
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
            return ModelRequestParameters(function_tools=function_tools, allow_text_output=True)
        return None

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    def _to_completion_response(
        self,
        response: pai_messages.ModelResponse,
        *,
        raw_response: Any,
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
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    @staticmethod
    def _to_usage(usage: Any) -> Usage:
        if usage is None:
            return Usage.zero()
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total = getattr(usage, "total_tokens", None)
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(total) if total is not None else input_tokens + output_tokens,
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _stream_completion(
        self,
        messages: list[pai_messages.ModelMessage],
        settings: ModelSettings | None,
        parameters: ModelRequestParameters | None,
        on_stream_event: StreamCallback,
        timeout: float,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        async with asyncio.timeout(timeout):
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
            StreamEvent(done=True, usage=completion.usage, finish_reason=completion.finish_reason)
        )
        return completion

    @staticmethod
    def _emit_stream_event(event: Any, on_stream_event: StreamCallback) -> None:
        if isinstance(event, pai_messages.PartStartEvent):
            part = event.part
            if isinstance(part, pai_messages.TextPart) and part.content:
                on_stream_event(StreamEvent(delta_text=part.content))
            elif isinstance(part, pai_messages.ThinkingPart) and part.content:
                on_stream_event(StreamEvent(delta_reasoning=part.content))
        elif isinstance(event, pai_messages.PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, pai_messages.TextPartDelta) and delta.content_delta:
                on_stream_event(StreamEvent(delta_text=delta.content_delta))
            elif isinstance(delta, pai_messages.ThinkingPartDelta) and delta.content_delta:
                on_stream_event(StreamEvent(delta_reasoning=delta.content_delta))

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    def _map_error(self, exc: Exception) -> LLMError:
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
