"""Core types for the LLM layer.

This module defines the typed request/response model that all providers adapt to/from,
eliminating raw dict plumbing throughout the stack.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Role and Content Parts
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class TextPart:
    """Text content part."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    """Tool/function call part from assistant."""

    name: str
    arguments_json: str  # Raw JSON string for faithful round-trip
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResultPart:
    """Tool/function result part from tool execution."""

    name: str
    result_json: str
    call_id: str | None = None
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ImagePart:
    """Image content part."""

    data: bytes
    media_type: str  # e.g., "image/png", "image/jpeg"
    detail: Literal["auto", "low", "high"] = "auto"


ContentPart = TextPart | ToolCallPart | ToolResultPart | ImagePart


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """A single message in a conversation.

    Providers adapt SDK-specific payload shapes (Anthropic content blocks,
    Google parts, Bedrock converse formats) to/from this type.
    """

    role: Role
    parts: tuple[ContentPart, ...] | list[ContentPart]

    def __post_init__(self) -> None:
        # Ensure parts is always a tuple for immutability
        if isinstance(self.parts, list):
            object.__setattr__(self, "parts", tuple(self.parts))

    @property
    def text(self) -> str:
        """Extract concatenated text from all TextParts."""
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        """Extract all tool calls from message."""
        return [p for p in self.parts if isinstance(p, ToolCallPart)]


# ---------------------------------------------------------------------------
# Tool and Structured Output Specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Specification for a callable tool/function."""

    name: str
    description: str
    json_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StructuredOutputSpec:
    """Specification for structured output (response schema)."""

    name: str
    json_schema: dict[str, Any]
    strict: bool = True


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """A typed request to an LLM provider.

    This is the normalized request format that all output strategies
    produce and all providers consume.
    """

    model: str
    messages: tuple[LLMMessage, ...] | list[LLMMessage]
    tools: tuple[ToolSpec, ...] | list[ToolSpec] | None = None
    tool_choice: str | None = None  # Tool name or None
    structured_output: StructuredOutputSpec | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    extra: dict[str, Any] | None = None  # Provider-specific passthrough (sanitized)

    def __post_init__(self) -> None:
        # Ensure messages is always a tuple for immutability
        if isinstance(self.messages, list):
            object.__setattr__(self, "messages", tuple(self.messages))
        if isinstance(self.tools, list):
            object.__setattr__(self, "tools", tuple(self.tools) if self.tools else None)


# ---------------------------------------------------------------------------
# Usage and Response
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage statistics."""

    input_tokens: int
    output_tokens: int
    total_tokens: int

    @classmethod
    def zero(cls) -> Usage:
        """Create a zero usage instance."""
        return cls(input_tokens=0, output_tokens=0, total_tokens=0)


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    """Normalized response from a completion call.

    Providers adapt SDK responses into this portable shape so that the rest of the
    system never needs to interpret provider-specific payload formats.
    """

    message: LLMMessage
    usage: Usage
    raw_response: Any = None
    reasoning_content: str | None = None
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StreamEvent:
    """A streaming event emitted during completion.

    This is a minimal common denominator; providers can emit richer events internally.
    """

    delta_text: str | None = None
    delta_reasoning: str | None = None  # For reasoning models (o1, o3, etc.)
    delta_tool_call: ToolCallPart | None = None
    usage: Usage | None = None
    done: bool = False
    finish_reason: str | None = None


StreamCallback = Callable[[StreamEvent], None]


# ---------------------------------------------------------------------------
# Cancellation Protocol
# ---------------------------------------------------------------------------


class CancelToken:
    """Minimal cancellation contract compatible with PenguiFlow cancel propagation.

    This protocol allows callers to signal cancellation to long-running LLM operations.
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cost:
    """Cost information for an LLM call."""

    input_cost: float
    output_cost: float
    total_cost: float
    currency: str = "USD"

    @classmethod
    def zero(cls) -> Cost:
        """Create a zero cost instance."""
        return cls(input_cost=0.0, output_cost=0.0, total_cost=0.0)

    def __add__(self, other: Cost) -> Cost:
        """Add two costs together."""
        if self.currency != other.currency:
            raise ValueError(f"Cannot add costs with different currencies: {self.currency} vs {other.currency}")
        return Cost(
            input_cost=self.input_cost + other.input_cost,
            output_cost=self.output_cost + other.output_cost,
            total_cost=self.total_cost + other.total_cost,
            currency=self.currency,
        )


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def extract_text(message: LLMMessage) -> str:
    """Extract all text content from a message."""
    return message.text


def extract_single_tool_call(message: LLMMessage, expected_name: str | None = None) -> ToolCallPart:
    """Extract a single tool call from a message.

    Args:
        message: The message to extract from.
        expected_name: If provided, verify the tool call has this name.

    Returns:
        The tool call part.

    Raises:
        ValueError: If no tool call found or multiple tool calls present.
    """
    tool_calls = message.tool_calls
    if not tool_calls:
        raise ValueError("No tool calls found in message")
    if len(tool_calls) > 1:
        raise ValueError(f"Expected single tool call, found {len(tool_calls)}")

    call = tool_calls[0]
    if expected_name is not None and call.name != expected_name:
        raise ValueError(f"Expected tool call '{expected_name}', got '{call.name}'")

    return call


def strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from text if present."""
    import re

    text = text.strip()

    # Try fenced code block first (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()

    return text
