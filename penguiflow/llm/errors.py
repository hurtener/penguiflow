"""Error taxonomy for the LLM layer.

Provides a stable error hierarchy with explicit retryability so that:
- The retry loop can make correct decisions without string matching
- The planner/runtime can log consistent failure reasons across providers
- User-visible errors can be extracted cleanly while retaining raw payloads for debugging
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMError(Exception):
    """Base class for all LLM errors.

    All LLM errors carry:
    - message: A clean, user-readable error message
    - provider: The provider that raised the error (optional)
    - status_code: HTTP status code if applicable (optional)
    - retryable: Whether this error should be retried
    - raw: The original exception or response for debugging
    """

    message: str
    provider: str | None = None
    status_code: int | None = None
    retryable: bool = False
    raw: Any = None

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.status_code:
            parts.append(f"status={self.status_code}")
        return " | ".join(parts)


@dataclass
class LLMTimeoutError(LLMError):
    """Request timed out."""

    retryable: bool = True


@dataclass
class LLMRateLimitError(LLMError):
    """Rate limit exceeded."""

    retryable: bool = True
    retry_after: float | None = None  # Seconds to wait before retrying


@dataclass
class LLMServerError(LLMError):
    """Server-side error (5xx)."""

    retryable: bool = True


@dataclass
class LLMInvalidRequestError(LLMError):
    """Invalid request (4xx, non-rate-limit)."""

    retryable: bool = False


@dataclass
class LLMAuthError(LLMError):
    """Authentication or authorization error."""

    retryable: bool = False


@dataclass
class LLMCancelledError(LLMError):
    """Request was cancelled."""

    retryable: bool = False


@dataclass
class LLMContextLengthError(LLMError):
    """Context length exceeded."""

    retryable: bool = False  # Not automatically retryable; requires context reduction
    max_tokens: int | None = None
    current_tokens: int | None = None


@dataclass
class LLMValidationError(LLMError):
    """Response validation failed (e.g., Pydantic validation)."""

    retryable: bool = True  # Can be retried with feedback
    validation_errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LLMParseError(LLMError):
    """Failed to parse response (e.g., invalid JSON)."""

    retryable: bool = True  # Can be retried with feedback
    raw_content: str = ""


# ---------------------------------------------------------------------------
# Error Mapping Utilities
# ---------------------------------------------------------------------------


def is_retryable(error: Exception) -> bool:
    """Check if an error is retryable."""
    if isinstance(error, LLMError):
        return error.retryable
    return False


def map_status_to_error(
    status_code: int,
    message: str,
    provider: str | None = None,
    raw: Any = None,
) -> LLMError:
    """Map an HTTP status code to the appropriate LLMError subclass."""
    if status_code == 401 or status_code == 403:
        return LLMAuthError(
            message=message,
            provider=provider,
            status_code=status_code,
            raw=raw,
        )
    elif status_code == 429:
        return LLMRateLimitError(
            message=message,
            provider=provider,
            status_code=status_code,
            raw=raw,
        )
    elif status_code == 408 or status_code == 504:
        return LLMTimeoutError(
            message=message,
            provider=provider,
            status_code=status_code,
            raw=raw,
        )
    elif status_code >= 500:
        return LLMServerError(
            message=message,
            provider=provider,
            status_code=status_code,
            raw=raw,
        )
    elif status_code >= 400:
        return LLMInvalidRequestError(
            message=message,
            provider=provider,
            status_code=status_code,
            raw=raw,
        )
    else:
        return LLMError(
            message=message,
            provider=provider,
            status_code=status_code,
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Context Length Detection
# ---------------------------------------------------------------------------

_CONTEXT_LENGTH_PATTERNS = (
    "input is too long",
    "context length",
    "maximum context",
    "token limit",
    "context_length_exceeded",
    "max_tokens",
    "too many tokens",
    "exceeds the model",
    "maximum tokens",
    "context window",
)


def is_context_length_error(error: Exception | str) -> bool:
    """Check if an error is related to context length."""
    if isinstance(error, LLMContextLengthError):
        return True

    error_str = str(error).lower()
    return any(pattern in error_str for pattern in _CONTEXT_LENGTH_PATTERNS)


def extract_clean_error_message(exc: Exception) -> str:
    """Extract a user-friendly error message from an LLM exception.

    Handles nested JSON error messages from providers like Databricks.
    """
    import re

    error_str = str(exc)

    # Try to extract nested JSON message (common in Databricks errors)
    # Pattern: {"error_code":"BAD_REQUEST","message":"{"message":"..."}"}
    try:
        # Look for nested message patterns
        matches = re.findall(r'"message"\s*:\s*"([^"]+)"', error_str)
        if matches:
            # Return the last (most nested) message
            return matches[-1]
    except Exception:
        pass

    # Fallback: extract after the last colon for standard exceptions
    if ": " in error_str:
        return error_str.split(": ", 1)[-1].strip('"{}')

    return error_str
