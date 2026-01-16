"""Google/Gemini provider implementation.

Uses the google-genai SDK (v1.57+) for direct API access.
Supports Gemini 2.5/3.0 model families with native structured output,
streaming, and function calling.

Reference: https://ai.google.dev/gemini-api/docs
"""

from __future__ import annotations

import asyncio
import json
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
    ImagePart,
    LLMMessage,
    LLMRequest,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from .base import Provider

if TYPE_CHECKING:
    from google.genai import Client as GenaiClient
    from google.genai.types import Content, GenerateContentConfig, GenerateContentResponse

    from ..types import CancelToken, StreamCallback


class GoogleProvider(Provider):
    """Google/Gemini provider using the google-genai SDK (v1.57+).

    Supports:
    - Gemini 2.5/3.0 model families (gemini-3-pro-preview, gemini-2.5-flash, etc.)
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
        timeout: float = 60.0,
    ):
        """Initialize the Google provider.

        Args:
            model: Model identifier (e.g., "gemini-2.5-flash", "gemini-3-pro-preview").
            api_key: Google API key (uses GOOGLE_API_KEY env var if not provided).
            profile: Model profile override.
            timeout: Default timeout in seconds.
        """
        try:
            from google import genai
        except ImportError as e:
            raise ImportError(
                "Google GenAI SDK not installed. Install with: pip install google-genai>=1.57.0"
            ) from e

        self._model = model
        self._profile = profile or get_profile(model)
        self._timeout = timeout

        api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self._client: GenaiClient = genai.Client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "google"

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
            raise LLMCancelledError(message="Request cancelled", provider="google")

        contents = self._to_google_contents(request.messages)
        config = self._build_config(request)
        timeout = timeout_s or self._timeout

        try:
            if stream and on_stream_event:
                return await self._stream_completion(contents, config, on_stream_event, timeout, cancel)

            async with asyncio.timeout(timeout):
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )

            return self._from_google_response(response)

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
            raise self._map_error(e) from e

    async def _stream_completion(
        self,
        contents: list[Content],
        config: GenerateContentConfig,
        on_stream_event: StreamCallback,
        timeout: float,
        cancel: CancelToken | None,
    ) -> CompletionResponse:
        """Handle streaming completion."""
        def _accumulate(prev: str, incoming: str) -> tuple[str, str]:
            """Return (delta, next_full) for cumulative or delta streams."""
            if not incoming:
                return "", prev
            if incoming.startswith(prev):
                return incoming[len(prev) :], incoming
            if prev and incoming in prev:
                return "", prev
            return incoming, prev + incoming

        full_text = ""
        full_reasoning = ""
        usage: Usage | None = None
        finish_reason: str | None = None

        try:
            async with asyncio.timeout(timeout):
                async for chunk in await self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                ):
                    if cancel and cancel.is_cancelled():
                        raise LLMCancelledError(message="Request cancelled", provider="google")

                    emitted_text = False

                    # Prefer parsing candidates/parts so we can separate thought vs normal output.
                    candidates = getattr(chunk, "candidates", None)
                    if candidates:
                        for candidate in candidates:
                            parts = getattr(getattr(candidate, "content", None), "parts", None)
                            if not parts:
                                continue
                            for part in parts:
                                part_text = getattr(part, "text", None)
                                if not part_text:
                                    continue

                                is_thought = bool(getattr(part, "thought", False))
                                if is_thought:
                                    delta, full_reasoning = _accumulate(full_reasoning, str(part_text))
                                    if delta:
                                        on_stream_event(StreamEvent(delta_reasoning=delta))
                                else:
                                    delta, full_text = _accumulate(full_text, str(part_text))
                                    if delta:
                                        emitted_text = True
                                        on_stream_event(StreamEvent(delta_text=delta))

                    # Fallback: some SDK/server combos provide streaming text only via chunk.text.
                    chunk_text = getattr(chunk, "text", None)
                    if chunk_text and not emitted_text:
                        delta, full_text = _accumulate(full_text, str(chunk_text))
                        if delta:
                            on_stream_event(StreamEvent(delta_text=delta))

                    # Handle usage metadata if present
                    if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                        usage = Usage(
                            input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                            output_tokens=chunk.usage_metadata.candidates_token_count or 0,
                            total_tokens=chunk.usage_metadata.total_token_count or 0,
                        )

                    # Handle finish reason
                    if hasattr(chunk, "candidates") and chunk.candidates:
                        finish_reason = chunk.candidates[0].finish_reason

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Stream timed out after {timeout}s",
                provider="google",
                raw=e,
            ) from e

        # Build final message
        parts: list[TextPart | ToolCallPart | ToolResultPart | ImagePart] = (
            [TextPart(text=full_text)] if full_text else []
        )

        on_stream_event(StreamEvent(done=True, usage=usage, finish_reason=finish_reason))

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage or Usage.zero(),
            raw_response=None,
            reasoning_content=full_reasoning or None,
            finish_reason=finish_reason,
        )


    def _to_google_contents(self, messages: tuple[LLMMessage, ...] | list[LLMMessage]) -> list[Content]:
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
                            args=json.loads(part.arguments_json) if part.arguments_json else {},
                        )
                    )
                elif isinstance(part, ToolResultPart):
                    parts.append(
                        genai_types.Part.from_function_response(
                            name=part.name,
                            response=json.loads(part.result_json) if part.result_json else {},
                        )
                    )

            if parts:
                role = "user" if msg.role in ("user", "system", "tool") else "model"
                contents.append(genai_types.Content(role=role, parts=parts))

        return contents

    def _build_config(self, request: LLMRequest) -> GenerateContentConfig:
        """Build Google API GenerateContentConfig from request."""
        from google.genai import types as genai_types

        config: dict[str, Any] = {
            "temperature": request.temperature,
        }

        if request.max_tokens:
            config["max_output_tokens"] = request.max_tokens

        # Handle structured output via response_schema
        if request.structured_output:
            from ..schema.google import GoogleJsonSchemaTransformer

            transformer = GoogleJsonSchemaTransformer(
                request.structured_output.json_schema,
                strict=request.structured_output.strict,
            )
            config["response_mime_type"] = "application/json"
            # IMPORTANT: Use response_json_schema, not response_schema.
            #
            # google-genai's GenerateContentConfig supports both:
            # - response_schema: a google.genai.types.Schema (OpenAPI-ish) which the SDK converts
            # - response_json_schema: raw JSON schema, passed through as-is
            #
            # We intentionally use response_json_schema here because:
            # - Our transformer produces JSON Schema (not a google.genai.types.Schema tree)
            # - Some SDK/server combinations reject snake_case schema fields (e.g., additional_properties),
            #   which can be introduced during Schema coercion/serialization.
            config["response_json_schema"] = transformer.transform()

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
                effort = str(reasoning_effort).strip().lower()
                budget = None

                effort_to_budget = {
                    "minimal": 1024,
                    "low": 4096,
                    "medium": 16384,
                    "high": 32768,
                }

                budget = effort_to_budget.get(effort)
                if not isinstance(budget, int) or budget < 0:
                    budget = None

                # Configure thinking with proper parameters for streaming
                thinking_kwargs: dict[str, Any] = {"include_thoughts": True}
                if budget is not None:
                    thinking_kwargs["thinking_budget"] = budget
                # Note: Gemini API rejects setting both thinking_budget and thinking_level.
                # Prefer budget when available because it reliably yields thought parts in practice.

                try:
                    config["thinking_config"] = genai_types.ThinkingConfig(**thinking_kwargs)
                except Exception:
                    config["thinking_config"] = thinking_kwargs

        # Ensure proper streaming configuration for both text and reasoning
        # Add safety settings if not already present
        if "safety_settings" not in config:
            config["safety_settings"] = None  # Use default safety settings

        # Validate and filter config keys to only include valid ones
        allowed_keys = getattr(getattr(genai_types, "GenerateContentConfig", None), "model_fields", None)
        if isinstance(allowed_keys, dict):
            for key in list(config.keys()):
                if key not in allowed_keys:
                    config.pop(key, None)

        return genai_types.GenerateContentConfig(**config)

    def _to_google_tools(self, tools: tuple[Any, ...] | list[Any] | None) -> list[Any]:
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

    def _from_google_response(self, response: GenerateContentResponse) -> CompletionResponse:
        """Convert Google GenerateContentResponse to CompletionResponse."""
        parts: list[TextPart | ToolCallPart | ToolResultPart | ImagePart] = []
        reasoning_acc: list[str] = []

        if response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                    for part in candidate.content.parts:
                        # Handle text vs thought content: google-genai marks thought parts with `part.thought=True`.
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
                                    arguments_json=json.dumps(dict(fc.args) if fc.args else {}),
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

    def _map_error(self, exc: Exception) -> LLMError:
        """Map Google SDK exceptions to LLMError."""
        error_str = str(exc).lower()

        if "api key" in error_str or "authentication" in error_str or "unauthorized" in error_str:
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
