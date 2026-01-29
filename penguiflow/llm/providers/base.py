"""Provider base class for the LLM layer.

Defines the abstract interface that all LLM providers must implement.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..profiles import ModelProfile
    from ..types import (
        CancelToken,
        CompletionResponse,
        LLMRequest,
        StreamCallback,
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
        - Respect cancellation (raise `asyncio.CancelledError` or `LLMCancelledError`)
        - Enforce `timeout_s` (raise `LLMTimeoutError`)
        - Emit `StreamEvent` via `on_stream_event` if streaming is enabled
        - Normalize provider-specific responses into `CompletionResponse.message`
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

    def validate_request(self, request: LLMRequest) -> None:  # noqa: B027
        """Validate a request before sending.

        Default implementation does nothing. Providers can override to add
        provider-specific validation (e.g., tool count limits, schema complexity).

        Args:
            request: The request to validate.

        Raises:
            LLMInvalidRequestError: If validation fails.
        """


class OpenAICompatibleProvider(Provider, ABC):
    """Base class for OpenAI-compatible providers.

    Provides shared utilities for providers that use OpenAI-shaped APIs
    (OpenAI, Databricks, OpenRouter, Azure OpenAI).
    """

    def _to_openai_messages(self, messages: tuple[Any, ...] | list[Any]) -> list[dict[str, Any]]:
        """Convert typed messages to OpenAI-format messages."""
        from ..types import ImagePart, TextPart, ToolCallPart, ToolResultPart

        result: list[dict[str, Any]] = []
        for msg in messages:
            content: str | list[dict[str, Any]] | None
            tool_calls: list[dict[str, Any]]
            if len(msg.parts) == 1 and isinstance(msg.parts[0], TextPart):
                # Simple text message
                content = msg.parts[0].text
                tool_calls = []
            else:
                # Multi-part message
                content = []
                tool_calls = []
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        content.append({"type": "text", "text": part.text})
                    elif isinstance(part, ImagePart):
                        import base64

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

                if not content:
                    content = None

                if tool_calls:
                    result.append(
                        {
                            "role": msg.role,
                            "content": content if content else None,
                            "tool_calls": tool_calls,
                        }
                    )
                    continue

            # Handle tool result messages
            if msg.role == "tool" and msg.parts:
                for part in msg.parts:
                    if isinstance(part, ToolResultPart):
                        result.append(
                            {
                                "role": "tool",
                                "tool_call_id": part.call_id or "",
                                "content": part.result_json,
                            }
                        )
                continue

            result.append({"role": msg.role, "content": content})

        return result

    def _to_openai_tools(self, tools: tuple[Any, ...] | list[Any] | None) -> list[dict[str, Any]] | None:
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
        self,
        structured_output: Any | None,
    ) -> dict[str, Any] | None:
        """Convert structured output spec to OpenAI response_format."""
        if not structured_output:
            return None

        return {
            "type": "json_schema",
            "json_schema": {
                "name": structured_output.name,
                "schema": structured_output.json_schema,
                "strict": structured_output.strict,
            },
        }

    def _from_openai_response(
        self,
        response: Any,
    ) -> tuple[Any, Any]:
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
                    if item_type in ("text", "output_text") and isinstance(item.get("text"), str) and item["text"]:
                        parts.append(TextPart(text=item["text"]))

        for tc in getattr(msg, "tool_calls", []) or []:
            parts.append(
                ToolCallPart(
                    name=tc.function.name,
                    arguments_json=tc.function.arguments,
                    call_id=getattr(tc, "id", None),
                )
            )

        usage = Usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

        message = LLMMessage(role="assistant", parts=parts)
        return message, usage

    def _extract_openai_reasoning_content(self, message: Any) -> str | None:
        """Best-effort extraction of reasoning/thinking content from OpenAI-shaped messages.

        Different OpenAI-compatible backends may expose this under different fields.
        """
        if message is None:
            return None

        # SDK objects
        for attr in ("reasoning_content", "reasoning", "thinking"):
            val = getattr(message, attr, None)
            if isinstance(val, str) and val:
                return val

        # Dict-shaped messages (defensive)
        if isinstance(message, dict):
            for key in ("reasoning_content", "reasoning", "thinking"):
                val = message.get(key)
                if isinstance(val, str) and val:
                    return val
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type in ("reasoning", "thinking", "thought"):
                        summary = item.get("summary")
                        if isinstance(summary, list):
                            texts: list[str] = []
                            for s in summary:
                                if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]:
                                    texts.append(s["text"])
                            if texts:
                                return "".join(texts)
                        if isinstance(item.get("text"), str) and item["text"]:
                            return item["text"]

        # Some OpenAI-shaped backends (e.g. Databricks hybrid reasoning) return
        # a list of content blocks (reasoning + text).
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type in ("reasoning", "thinking", "thought"):
                    # Databricks Claude: {"type":"reasoning","summary":[{"text":"..."}]}
                    summary = item.get("summary")
                    if isinstance(summary, list):
                        texts = []
                        for s in summary:
                            if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]:
                                texts.append(s["text"])
                        if texts:
                            return "".join(texts)
                    if isinstance(item.get("text"), str) and item["text"]:
                        return item["text"]

        return None

    def _extract_openai_delta_reasoning(self, delta: Any) -> str | None:
        """Best-effort extraction of reasoning/thinking deltas from OpenAI-shaped streaming deltas."""
        if delta is None:
            return None

        for attr in ("reasoning_content", "reasoning", "thinking"):
            val = getattr(delta, attr, None)
            if isinstance(val, str) and val:
                return val

        if isinstance(delta, dict):
            for key in ("reasoning_content", "reasoning", "thinking"):
                val = delta.get(key)
                if isinstance(val, str) and val:
                    return val

            content = delta.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type in ("reasoning", "thinking", "thought"):
                        summary = item.get("summary")
                        if isinstance(summary, list):
                            for s in summary:
                                if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]:
                                    return s["text"]
                        if isinstance(item.get("text"), str) and item["text"]:
                            return item["text"]

        content = getattr(delta, "content", None)
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type in ("reasoning", "thinking", "thought"):
                    summary = item.get("summary")
                    if isinstance(summary, list):
                        for s in summary:
                            if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]:
                                return s["text"]
                    if isinstance(item.get("text"), str) and item["text"]:
                        return item["text"]

        return None
