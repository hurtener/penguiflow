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
        text_acc: list[str] = []
        usage: Usage | None = None
        finish_reason: str | None = None

        try:
            async with asyncio.timeout(timeout):
                async for chunk in self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=config,
                ):
                    if cancel and cancel.is_cancelled():
                        raise LLMCancelledError(message="Request cancelled", provider="google")

                    if chunk.text:
                        text_acc.append(chunk.text)
                        on_stream_event(StreamEvent(delta_text=chunk.text))

                    if chunk.usage_metadata:
                        usage = Usage(
                            input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                            output_tokens=chunk.usage_metadata.candidates_token_count or 0,
                            total_tokens=chunk.usage_metadata.total_token_count or 0,
                        )

                    if hasattr(chunk, "candidates") and chunk.candidates:
                        finish_reason = chunk.candidates[0].finish_reason

        except TimeoutError as e:
            raise LLMTimeoutError(
                message=f"Stream timed out after {timeout}s",
                provider="google",
                raw=e,
            ) from e

        # Build final message
        full_text = "".join(text_acc)
        parts: list[TextPart | ToolCallPart | ToolResultPart | ImagePart] = (
            [TextPart(text=full_text)] if full_text else []
        )

        on_stream_event(StreamEvent(done=True, usage=usage, finish_reason=finish_reason))

        return CompletionResponse(
            message=LLMMessage(role="assistant", parts=parts),
            usage=usage or Usage.zero(),
            raw_response=None,
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
                    parts.append(genai_types.Part.from_text(part.text))
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
            config["response_schema"] = transformer.transform()

        # Handle function calling
        if request.tools:
            config["tools"] = self._to_google_tools(request.tools)

        if request.tool_choice:
            config["tool_config"] = genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=[request.tool_choice],
                )
            )

        if request.extra:
            config.update(request.extra)

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

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    parts.append(TextPart(text=part.text))
                elif hasattr(part, "function_call") and part.function_call:
                    parts.append(
                        ToolCallPart(
                            name=part.function_call.name,
                            arguments_json=json.dumps(dict(part.function_call.args)),
                            call_id=None,  # Google doesn't use call IDs
                        )
                    )

        usage = Usage.zero()
        if response.usage_metadata:
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
