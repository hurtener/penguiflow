"""Provider base class for the LLM layer.

Defines the abstract interface that all LLM providers must implement.
"""

from __future__ import annotations

import base64
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..types import (
    CompletionResponse,
    LLMMessage,
    StreamEvent,
    TextPart,
    ToolCallPart,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openai.types.chat import ChatCompletion, ChatCompletionChunk
    from openai.types.chat.chat_completion_chunk import (
        ChoiceDelta,
        ChoiceDeltaToolCall,
    )

    from ..profiles import ModelProfile
    from ..types import (
        CancelToken,
        ContentPart,
        LLMRequest,
        StreamCallback,
        StructuredOutputSpec,
    )


def _openai_content_and_tool_calls(
    parts: Sequence[ContentPart],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Split multi-part content into OpenAI content blocks and tool calls.

    Returns ``None`` content when no text or image part is present.
    """
    from ..types import ImagePart, TextPart, ToolCallPart

    content: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            b64 = base64.b64encode(part.data).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{part.media_type};base64,{b64}",
                        "detail": part.detail,
                    },
                }
            )
        elif isinstance(part, ToolCallPart):
            tool_calls.append(
                {
                    "id": part.call_id or f"call_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {
                        "name": part.name,
                        "arguments": part.arguments_json,
                    },
                }
            )
    return content or None, tool_calls


_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")
_REASONING_BLOCK_TYPES = ("reasoning", "thinking", "thought")


def _text_of(value: object) -> str | None:
    """Return ``value`` if it is a non-empty string."""
    return value if isinstance(value, str) and value else None


def _summary_texts(summary: object) -> list[str]:
    """Return the non-empty ``text`` entries of a reasoning summary list."""
    if not isinstance(summary, list):
        return []
    texts: list[str] = []
    for entry in summary:
        if isinstance(entry, dict) and (text := _text_of(entry.get("text"))):
            texts.append(text)
    return texts


def _block_reasoning(blocks: object, *, join_summaries: bool) -> str | None:
    """Return reasoning text from a list of content blocks.

    Databricks Claude, for example, sends
    ``{"type": "reasoning", "summary": [{"text": "..."}]}``.
    """
    if not isinstance(blocks, list):
        return None
    for item in blocks:
        if (
            not isinstance(item, dict)
            or item.get("type") not in _REASONING_BLOCK_TYPES
        ):
            continue
        texts = _summary_texts(item.get("summary"))
        if texts:
            return "".join(texts) if join_summaries else texts[0]
        if text := _text_of(item.get("text")):
            return text
    return None


def _find_reasoning(source: object, *, join_summaries: bool) -> str | None:
    """Find reasoning text on an SDK object or a dict-shaped payload."""
    if source is None:
        return None
    for key in _REASONING_KEYS:
        if text := _text_of(getattr(source, key, None)):
            return text
    if isinstance(source, dict):
        for key in _REASONING_KEYS:
            if text := _text_of(source.get(key)):
                return text
        if text := _block_reasoning(
            source.get("content"), join_summaries=join_summaries
        ):
            return text
    return _block_reasoning(
        getattr(source, "content", None), join_summaries=join_summaries
    )


class OpenAIStreamAccumulator:
    """Accumulate OpenAI-shaped chat completion chunks into one response.

    Text and reasoning deltas are forwarded to ``on_stream_event`` as they
    arrive; tool-call fragments are merged by index.
    """

    def __init__(
        self,
        on_stream_event: StreamCallback,
        *,
        reasoning_of: Callable[[object], str | None],
    ) -> None:
        """Create an accumulator.

        Args:
            on_stream_event: Callback receiving each text/reasoning delta.
            reasoning_of: Extracts reasoning text from a chunk delta.
        """
        self._emit = on_stream_event
        self._reasoning_of = reasoning_of
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tool_calls: dict[int, dict[str, str]] = {}
        self.usage: Usage | None = None
        self.finish_reason: str | None = None

    def add(self, chunk: ChatCompletionChunk) -> None:
        """Fold one streamed chunk into the accumulated response."""
        if not chunk.choices:
            # Usage chunk at the end (if supported)
            usage = getattr(chunk, "usage", None)
            if usage:
                self.usage = Usage(
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                )
            return
        choice = chunk.choices[0]
        self.finish_reason = choice.finish_reason
        self._add_delta(choice.delta)
        for tool_call in choice.delta.tool_calls or []:
            self._add_tool_call(tool_call)

    def add_text(self, text: str) -> None:
        """Record and forward a text delta."""
        self._text.append(text)
        self._emit(StreamEvent(delta_text=text))

    def add_reasoning(self, text: str) -> None:
        """Record and forward a reasoning delta."""
        self._reasoning.append(text)
        self._emit(StreamEvent(delta_reasoning=text))

    def _add_delta(self, delta: ChoiceDelta) -> None:
        """Handle a delta's text and reasoning."""
        if delta.content:
            self.add_text(delta.content)
        reasoning = self._reasoning_of(delta)
        if reasoning:
            self.add_reasoning(reasoning)

    def _add_tool_call(self, tool_call: ChoiceDeltaToolCall) -> None:
        entry = self._tool_calls.setdefault(
            tool_call.index,
            {"id": tool_call.id or "", "name": "", "arguments": ""},
        )
        if tool_call.function:
            if tool_call.function.name:
                entry["name"] = tool_call.function.name
            if tool_call.function.arguments:
                entry["arguments"] += tool_call.function.arguments

    @property
    def reasoning_content(self) -> str | None:
        """The accumulated reasoning text, or ``None`` if there was none."""
        return "".join(self._reasoning) or None

    def message(self) -> LLMMessage:
        """Build the assistant message from the accumulated deltas."""
        parts: list[ContentPart] = []
        full_text = "".join(self._text)
        if full_text:
            parts.append(TextPart(text=full_text))
        for index in sorted(self._tool_calls):
            entry = self._tool_calls[index]
            parts.append(
                ToolCallPart(
                    name=entry["name"],
                    arguments_json=entry["arguments"],
                    call_id=entry["id"],
                )
            )
        return LLMMessage(role="assistant", parts=parts)

    def emit_done(self) -> None:
        """Emit the final ``done`` event with usage and finish reason."""
        self._emit(
            StreamEvent(
                done=True, usage=self.usage, finish_reason=self.finish_reason
            )
        )

    def response(self) -> CompletionResponse:
        """Emit the ``done`` event and build the completion response."""
        self.emit_done()
        return CompletionResponse(
            message=self.message(),
            usage=self.usage or Usage.zero(),
            raw_response=None,
            reasoning_content=self.reasoning_content,
            finish_reason=self.finish_reason,
        )


class Provider(ABC):
    """Abstract base class for LLM providers.

    Each provider implementation:
    - Uses native SDK directly (openai, anthropic, google-genai, boto3)
    - Handles SDK-specific payload shapes (content blocks, parts, etc.)
    - Normalizes responses into typed CompletionResponse
    - Implements streaming via StreamCallback
    - Respects cancellation tokens and timeouts
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the provider name (e.g., 'openai', 'anthropic')."""
        ...

    @property
    @abstractmethod
    def profile(self) -> ModelProfile:
        """Get the model profile for capability information."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Get the model identifier."""
        ...

    @abstractmethod
    async def complete(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        """Execute a completion request.

        Requirements:
        - Respect cancellation (raise `asyncio.CancelledError` or
          `LLMCancelledError`)
        - Enforce `timeout_s` (raise `LLMTimeoutError`)
        - Emit `StreamEvent` via `on_stream_event` if streaming is enabled
        - Normalize provider-specific responses into
          `CompletionResponse.message`
        - Map SDK exceptions to LLMError subclasses

        Args:
            request: The typed request to execute.
            timeout_s: Optional timeout in seconds.
            cancel: Optional cancellation token.
            stream: Whether to enable streaming.
            on_stream_event: Callback for streaming events.

        Returns:
            Normalized completion response.

        Raises:
            LLMError: For any provider errors (mapped to appropriate subclass).
            asyncio.CancelledError: If cancelled via cancel token.
        """
        ...

    def validate_request(self, request: LLMRequest) -> None:
        """Validate a request before sending.

        The default checks the tool count against the profile's
        ``max_tools``. Providers can override to add provider-specific
        validation (e.g., schema complexity).

        Args:
            request: The request to validate.

        Raises:
            LLMInvalidRequestError: If validation fails.
        """
        limit = self.profile.max_tools
        if request.tools and limit is not None and len(request.tools) > limit:
            from ..errors import LLMInvalidRequestError

            raise LLMInvalidRequestError(
                message=(
                    f"{self.provider_name} supports at most {limit} tools, "
                    f"got {len(request.tools)}"
                ),
                provider=self.provider_name,
            )

    @property
    def temperature_unsupported(self) -> bool:
        """Whether temperature has been disabled for this model at runtime.

        Set by :meth:`mark_temperature_unsupported` after a provider returns a
        temperature-related 400, so subsequent requests omit the parameter.
        """
        return getattr(self, "_temperature_unsupported", False)

    def mark_temperature_unsupported(self) -> None:
        """Record that this model rejects the temperature parameter.

        Subsequent ``build_params`` calls drop ``temperature`` so the same
        request can be retried successfully without a static profile change.
        """
        self._temperature_unsupported = True


def _to_openai_messages(
    messages: Sequence[LLMMessage],
) -> list[dict[str, Any]]:
    """Convert typed messages to OpenAI-format messages."""
    from ..types import TextPart, ToolResultPart

    result: list[dict[str, Any]] = []
    for msg in messages:
        content: str | list[dict[str, Any]] | None
        if len(msg.parts) == 1 and isinstance(msg.parts[0], TextPart):
            # Simple text message
            content = msg.parts[0].text
        else:
            content, tool_calls = _openai_content_and_tool_calls(msg.parts)
            if tool_calls:
                result.append(
                    {
                        "role": msg.role,
                        "content": content,
                        "tool_calls": tool_calls,
                    }
                )
                continue

        # Handle tool result messages
        if msg.role == "tool" and msg.parts:
            result.extend(
                {
                    "role": "tool",
                    "tool_call_id": part.call_id or "",
                    "content": part.result_json,
                }
                for part in msg.parts
                if isinstance(part, ToolResultPart)
            )
            continue

        result.append({"role": msg.role, "content": content})

    return result


def _to_openai_tools(
    tools: tuple[Any, ...] | list[Any] | None,
) -> list[dict[str, Any]] | None:
    """Convert typed tools to OpenAI-format tools."""
    if not tools:
        return None

    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.json_schema,
            },
        }
        for tool in tools
    ]


def _to_openai_response_format(
    structured_output: StructuredOutputSpec | None,
) -> dict[str, Any] | None:
    """Convert structured output spec to OpenAI response_format."""
    if not structured_output:
        return None

    # Represent permissive generic-object mode as json_object so
    # OpenAI-compatible providers can avoid strict schema validation.
    if (
        getattr(structured_output, "strict", None) is False
        and getattr(structured_output, "name", None) == "json_response"
        and getattr(structured_output, "json_schema", None)
        == {"type": "object"}
    ):
        return {"type": "json_object"}

    return {
        "type": "json_schema",
        "json_schema": {
            "name": structured_output.name,
            "schema": structured_output.json_schema,
            "strict": structured_output.strict,
        },
    }


def _from_openai_response(
    response: ChatCompletion,
) -> tuple[LLMMessage, Usage]:
    """Extract message and usage from OpenAI-format response.

    Returns:
        Tuple of (LLMMessage, Usage)
    """
    from ..types import LLMMessage, TextPart, ToolCallPart, Usage

    msg = response.choices[0].message
    parts: list[Any] = []

    content = getattr(msg, "content", None)
    if isinstance(content, str) and content:
        parts.append(TextPart(text=content))
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str) and item:
                parts.append(TextPart(text=item))
                continue
            if isinstance(item, dict):
                item_type = item.get("type")
                if (
                    item_type in ("text", "output_text")
                    and isinstance(item.get("text"), str)
                    and item["text"]
                ):
                    parts.append(TextPart(text=item["text"]))

    for tc in getattr(msg, "tool_calls", []) or []:
        parts.append(
            ToolCallPart(
                name=tc.function.name,
                arguments_json=tc.function.arguments,
                call_id=getattr(tc, "id", None),
            )
        )

    raw_usage = response.usage
    usage = (
        Usage(
            input_tokens=raw_usage.prompt_tokens,
            output_tokens=raw_usage.completion_tokens,
            total_tokens=raw_usage.total_tokens,
        )
        if raw_usage is not None
        else Usage.zero()
    )

    message = LLMMessage(role="assistant", parts=parts)
    return message, usage


def _extract_openai_reasoning_content(message: object) -> str | None:
    """Best-effort reasoning extraction from an OpenAI-shaped message.

    Different OpenAI-compatible backends expose reasoning under different
    fields, or as reasoning blocks in a list ``content`` (e.g. Databricks
    hybrid reasoning).
    """
    return _find_reasoning(message, join_summaries=True)


def _extract_openai_delta_reasoning(delta: object) -> str | None:
    """Best-effort reasoning extraction from an OpenAI-shaped delta."""
    return _find_reasoning(delta, join_summaries=False)


class OpenAICompatibleProvider(Provider, ABC):
    """Base class for OpenAI-compatible providers.

    Provides shared utilities for providers that use OpenAI-shaped APIs
    (OpenAI, Databricks, OpenRouter, Azure OpenAI).
    """
