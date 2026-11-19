"""Google/Gemini provider implementation.

Uses the google-genai SDK (v1.57+) for direct API access. Supports Gemini
2.5/3.0 model families with native structured output, streaming, and function
calling.

Reference: https://ai.google.dev/gemini-api/docs
"""

from __future__ import annotations

import asyncio
import json
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
    ContentPart,
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
    from google.genai import Client as GenaiClient
    from google.genai.types import (
        Content,
        ContentUnion,
        GenerateContentConfig,
        GenerateContentResponse,
        ThinkingConfig,
    )

    from ..types import (
        CancelToken,
        ContentPart,
        StreamCallback,
        StructuredOutputSpec,
    )


_EFFORT_TO_THINKING_BUDGET = {
    "minimal": 1024,
    "low": 4096,
    "medium": 16384,
    "high": 32768,
}


def _response_json_schema(spec: StructuredOutputSpec) -> dict[str, Any]:
    """Return the Gemini-compatible JSON schema for structured output.

    The raw JSON schema goes in ``response_json_schema`` rather than
    ``response_schema`` (a ``google.genai.types.Schema`` the SDK converts):
    the transformer produces JSON Schema, and some SDK/server combinations
    reject snake_case schema fields (e.g. ``additional_properties``) that
    Schema coercion can introduce.
    """
    from ..schema.google import GoogleJsonSchemaTransformer

    transformer = GoogleJsonSchemaTransformer(
        spec.json_schema, strict=spec.strict
    )
    return transformer.transform()


def _thinking_config(
    reasoning_effort: object,
) -> ThinkingConfig | dict[str, Any]:
    """Build the thinking config for a reasoning effort level.

    Gemini rejects setting both ``thinking_budget`` and ``thinking_level``;
    a budget is used when the effort maps to one because it reliably yields
    thought parts.
    """
    from google.genai import types as genai_types

    effort = str(reasoning_effort).strip().lower()
    thinking_kwargs: dict[str, Any] = {"include_thoughts": True}
    budget = _EFFORT_TO_THINKING_BUDGET.get(effort)
    if budget is not None:
        thinking_kwargs["thinking_budget"] = budget
    try:
        return genai_types.ThinkingConfig(**thinking_kwargs)
    except Exception:
        return thinking_kwargs


def _known_config_keys(config: dict[str, Any]) -> dict[str, Any]:
    """Drop keys that ``GenerateContentConfig`` does not define."""
    from google.genai import types as genai_types

    allowed_keys = getattr(
        getattr(genai_types, "GenerateContentConfig", None),
        "model_fields",
        None,
    )
    if not isinstance(allowed_keys, dict):
        return config
    return {key: value for key, value in config.items() if key in allowed_keys}


def _accumulate(prev: str, incoming: str) -> tuple[str, str]:
    """Return ``(delta, next_full)`` for cumulative or delta streams."""
    if not incoming:
        return "", prev
    if incoming.startswith(prev):
        return incoming[len(prev) :], incoming
    if prev and incoming in prev:
        return "", prev
    return incoming, prev + incoming


class _GoogleStreamState:
    """Accumulates Gemini stream chunks, separating thoughts from text."""

    def __init__(self, on_stream_event: StreamCallback) -> None:
        self._emit = on_stream_event
        self._text = ""
        self._reasoning = ""
        self._usage: Usage | None = None
        self._finish_reason: str | None = None

    def add_chunk(self, chunk: object) -> None:
        """Fold one streamed chunk into the accumulated response."""
        # Prefer parsing candidates/parts so thoughts and normal output can be
        # told apart.
        emitted_text = False
        candidates = getattr(chunk, "candidates", None)
        for candidate in candidates or []:
            parts = getattr(getattr(candidate, "content", None), "parts", None)
            for part in parts or []:
                emitted_text = self._add_part(part) or emitted_text

        # Fallback: some SDK/server combos stream text only via chunk.text.
        chunk_text = getattr(chunk, "text", None)
        if chunk_text and not emitted_text:
            self._add_text(str(chunk_text))

        usage = getattr(chunk, "usage_metadata", None)
        if usage:
            self._usage = Usage(
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=usage.candidates_token_count or 0,
                total_tokens=usage.total_token_count or 0,
            )
        if candidates:
            self._finish_reason = candidates[0].finish_reason

    def _add_part(self, part: object) -> bool:
        """Add one candidate part; return whether new text was emitted."""
        part_text = getattr(part, "text", None)
        if not part_text:
            return False
        if getattr(part, "thought", False):
            delta, self._reasoning = _accumulate(
                self._reasoning, str(part_text)
            )
            if delta:
                self._emit(StreamEvent(delta_reasoning=delta))
            return False
        return self._add_text(str(part_text))

    def _add_text(self, incoming: str) -> bool:
        delta, self._text = _accumulate(self._text, incoming)
        if delta:
            self._emit(StreamEvent(delta_text=delta))
        return bool(delta)

    def response(self) -> CompletionResponse:
        """Emit the ``done`` event and build the completion response."""
        parts: list[ContentPart] = (
            [TextPart(text=self._text)] if self._text else []
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
            reasoning_content=self._reasoning or None,
            finish_reason=self._finish_reason,
        )


def _from_google_response(
    response: GenerateContentResponse,
) -> CompletionResponse:
    """Convert Google GenerateContentResponse to CompletionResponse."""
    parts: list[ContentPart] = []
    reasoning_acc: list[str] = []

    if response.candidates:
        for candidate in response.candidates:
            content = getattr(candidate, "content", None)
            candidate_parts = (
                getattr(content, "parts", None)
                if content is not None
                else None
            )
            if not candidate_parts:
                continue
            for part in candidate_parts:
                # Handle text vs thought content: google-genai marks
                # thought parts with `part.thought=True`.
                part_text = getattr(part, "text", None)
                if part_text:
                    if bool(getattr(part, "thought", False)):
                        reasoning_acc.append(str(part_text))
                    else:
                        parts.append(TextPart(text=str(part_text)))

                # Handle function calls
                fc = getattr(part, "function_call", None)
                if fc:
                    parts.append(
                        ToolCallPart(
                            name=fc.name or "",
                            arguments_json=json.dumps(
                                dict(fc.args) if fc.args else {}
                            ),
                            call_id=None,  # Google doesn't use call IDs
                        )
                    )

    usage = Usage.zero()
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = Usage(
            input_tokens=response.usage_metadata.prompt_token_count or 0,
            output_tokens=response.usage_metadata.candidates_token_count or 0,
            total_tokens=response.usage_metadata.total_token_count or 0,
        )

    finish_reason = None
    if response.candidates:
        finish_reason = response.candidates[0].finish_reason

    return CompletionResponse(
        message=LLMMessage(role="assistant", parts=parts),
        usage=usage,
        raw_response=response,
        reasoning_content="".join(reasoning_acc) or None,
        finish_reason=finish_reason,
    )


class GoogleProvider(Provider):
    """Google/Gemini provider using the google-genai SDK (v1.57+).

    Supports:
    - Gemini 2.5/3.0 model families (gemini-3-pro-preview, gemini-2.5-flash,
      etc.)
    - Native structured output via response_schema
    - Streaming with usage tracking
    - Function calling with FunctionDeclaration
    - Thinking/reasoning modes (Gemini 2.5+ models)

    Reference: https://ai.google.dev/gemini-api/docs/structured-output
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        profile: ModelProfile | None = None,
        timeout: float = 360.0,
        client: GenaiClient | None = None,
    ) -> None:
        """Initialize the Google provider.

        Args:
            model: Model identifier (e.g., "gemini-2.5-flash",
                "gemini-3-pro-preview").
            api_key: Google API key (uses GOOGLE_API_KEY env var if not
                provided).
            profile: Model profile override.
            timeout: Default timeout in seconds.
            client: Pre-built SDK client to use instead of creating one.
        """
        try:
            from google import genai
        except ImportError as e:
            msg = (
                "Google GenAI SDK not installed. Install with: pip "
                "install google-genai>=1.57.0"
            )
            raise ImportError(msg) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout

        if client is not None:
            self._client: GenaiClient = client
        else:
            api_key = api_key or os.environ.get("GOOGLE_API_KEY")
            self._client = genai.Client(api_key=api_key)

    @property
    @override
    def provider_name(self) -> str:
        return "google"

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

    @property
    def client(self) -> GenaiClient:
        """The google-genai client the provider sends requests with."""
        return self._client

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
                message="Request cancelled", provider="google", retryable=False
            )

        contents = self._to_google_contents(request.messages)
        config = self.build_config(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                try:
                    async with asyncio.timeout(timeout):
                        return await self.stream_completion(
                            contents, config, on_stream_event, cancel
                        )
                except TimeoutError as e:
                    raise LLMTimeoutError(
                        message=f"Stream timed out after {timeout}s",
                        provider="google",
                        raw=e,
                    ) from e

            request_contents: list[ContentUnion] = list(contents)
            async with asyncio.timeout(timeout):
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=request_contents,
                    config=config,
                )

            return _from_google_response(response)

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Request timed out after {timeout}s",
                provider="google",
                raw=e,
            ) from e
        except asyncio.CancelledError:
            raise LLMCancelledError(
                message="Request cancelled", provider="google"
            ) from None
        except Exception as e:
            raise self.map_error(e) from e

    async def stream_completion(
        self,
        contents: list[Content],
        config: GenerateContentConfig,
        on_stream_event: StreamCallback,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        request_contents: list[ContentUnion] = list(contents)
        state = _GoogleStreamState(on_stream_event)
        async for (
            chunk
        ) in await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=request_contents,
            config=config,
        ):
            if cancel and cancel.is_cancelled():
                raise LLMCancelledError(
                    message="Request cancelled",
                    provider="google",
                    retryable=False,
                )
            state.add_chunk(chunk)
        return state.response()

    def _to_google_contents(
        self, messages: tuple[LLMMessage, ...] | list[LLMMessage]
    ) -> list[Content]:
        """Convert typed messages to Google Content format."""
        from google.genai import types as genai_types

        contents: list[Content] = []

        for msg in messages:
            parts: list[Any] = []

            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(genai_types.Part.from_text(text=part.text))
                elif isinstance(part, ImagePart):
                    parts.append(
                        genai_types.Part.from_bytes(
                            data=part.data,
                            mime_type=part.media_type,
                        )
                    )
                elif isinstance(part, ToolCallPart):
                    parts.append(
                        genai_types.Part.from_function_call(
                            name=part.name,
                            args=json.loads(part.arguments_json)
                            if part.arguments_json
                            else {},
                        )
                    )
                elif isinstance(part, ToolResultPart):
                    parts.append(
                        genai_types.Part.from_function_response(
                            name=part.name,
                            response=json.loads(part.result_json)
                            if part.result_json
                            else {},
                        )
                    )

            if parts:
                role = (
                    "user"
                    if msg.role in ("user", "system", "tool")
                    else "model"
                )
                contents.append(genai_types.Content(role=role, parts=parts))

        return contents

    def build_config(self, request: LLMRequest) -> GenerateContentConfig:
        """Build Google API GenerateContentConfig from request."""
        from google.genai import types as genai_types

        config: dict[str, Any] = {}

        temp = resolve_temperature(
            self._profile,
            request.temperature,
            model=self._model,
            forced_off=self.temperature_unsupported,
        )
        if temp is not None:
            config["temperature"] = temp

        if request.max_tokens:
            config["max_output_tokens"] = request.max_tokens

        # Handle structured output via response_schema
        if request.structured_output:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = _response_json_schema(
                request.structured_output
            )

        # Handle function calling
        if request.tools:
            config["tools"] = self._to_google_tools(request.tools)

        if request.tool_choice:
            config["tool_config"] = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode=genai_types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=[request.tool_choice],
                )
            )

        if request.extra:
            extra = dict(request.extra)
            reasoning_effort = extra.pop("reasoning_effort", None)
            config.update(extra)
            if reasoning_effort and "thinking_config" not in config:
                config["thinking_config"] = _thinking_config(reasoning_effort)

        # Use the default safety settings unless some were given.
        config.setdefault("safety_settings", None)
        return genai_types.GenerateContentConfig(**_known_config_keys(config))

    def _to_google_tools(
        self, tools: tuple[Any, ...] | list[Any] | None
    ) -> list[Any]:
        """Convert typed tools to Google format."""
        from google.genai import types as genai_types

        if not tools:
            return []

        functions = []
        for tool in tools:
            functions.append(
                genai_types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.json_schema,
                )
            )

        return [genai_types.Tool(function_declarations=functions)]

    def map_error(self, exc: Exception) -> LLMError:
        """Map Google SDK exceptions to LLMError."""
        error_str = str(exc).lower()

        if (
            "api key" in error_str
            or "authentication" in error_str
            or "unauthorized" in error_str
        ):
            return LLMAuthError(
                message=str(exc),
                provider="google",
                raw=exc,
            )

        if "rate limit" in error_str or "quota" in error_str:
            return LLMRateLimitError(
                message=str(exc),
                provider="google",
                raw=exc,
            )

        if is_context_length_error(exc):
            return LLMContextLengthError(
                message=str(exc),
                provider="google",
                raw=exc,
            )

        if "invalid" in error_str or "bad request" in error_str:
            return LLMInvalidRequestError(
                message=str(exc),
                provider="google",
                raw=exc,
            )

        if "server" in error_str or "internal" in error_str:
            return LLMServerError(
                message=str(exc),
                provider="google",
                raw=exc,
            )

        return LLMError(
            message=str(exc),
            provider="google",
            raw=exc,
        )
