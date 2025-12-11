"""Error types for ToolNode."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    """Classification for retry decisions."""

    RETRYABLE_SERVER = "retryable_server"  # 500-504, transient
    RETRYABLE_RATE_LIMIT = "retryable_rate"  # 429, backoff required
    NON_RETRYABLE_CLIENT = "non_retryable"  # 400-428 (except 429)
    AUTH_REQUIRED = "auth_required"  # 401, 403
    NETWORK = "network"  # Connection errors
    CANCELLED = "cancelled"  # Task cancelled
    UNKNOWN = "unknown"


class ToolNodeError(Exception):
    """Base exception for ToolNode errors."""

    category: ErrorCategory = ErrorCategory.UNKNOWN
    status_code: int | None = None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        category: ErrorCategory | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.category = category or self._infer_category(status_code)
        self.__cause__ = cause

    def _infer_category(self, status_code: int | None) -> ErrorCategory:
        if status_code is None:
            return ErrorCategory.UNKNOWN
        if status_code == 429:
            return ErrorCategory.RETRYABLE_RATE_LIMIT
        if 500 <= status_code <= 504:
            return ErrorCategory.RETRYABLE_SERVER
        if status_code in (401, 403):
            return ErrorCategory.AUTH_REQUIRED
        if 400 <= status_code < 500:
            return ErrorCategory.NON_RETRYABLE_CLIENT
        return ErrorCategory.UNKNOWN

    @property
    def is_retryable(self) -> bool:
        return self.category in (
            ErrorCategory.RETRYABLE_SERVER,
            ErrorCategory.RETRYABLE_RATE_LIMIT,
            ErrorCategory.NETWORK,
        )

    @property
    def retry_after_seconds(self) -> float | None:
        """Hint for backoff, especially for 429s."""
        if self.category == ErrorCategory.RETRYABLE_RATE_LIMIT:
            return 1.0
        return None

    def to_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "status_code": self.status_code,
            "category": self.category.value,
            "is_retryable": self.is_retryable,
        }


class ToolAuthError(ToolNodeError):
    """Authentication required or failed."""

    category = ErrorCategory.AUTH_REQUIRED


class ToolTimeoutError(ToolNodeError):
    """Tool execution exceeded timeout."""

    category = ErrorCategory.RETRYABLE_SERVER


class ToolConnectionError(ToolNodeError):
    """Failed to connect to tool source."""

    category = ErrorCategory.NETWORK


class ToolRateLimitError(ToolNodeError):
    """Rate limited by external service."""

    category = ErrorCategory.RETRYABLE_RATE_LIMIT


class ToolClientError(ToolNodeError):
    """Client error (4xx) - don't retry."""

    category = ErrorCategory.NON_RETRYABLE_CLIENT


class ToolServerError(ToolNodeError):
    """Server error (5xx) - retry."""

    category = ErrorCategory.RETRYABLE_SERVER


__all__ = [
    "ErrorCategory",
    "ToolAuthError",
    "ToolClientError",
    "ToolConnectionError",
    "ToolNodeError",
    "ToolRateLimitError",
    "ToolServerError",
    "ToolTimeoutError",
]
