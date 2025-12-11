"""Transport-aware error adapters for ToolNode."""

from __future__ import annotations

import asyncio
import re

from .errors import (
    ToolAuthError,
    ToolClientError,
    ToolConnectionError,
    ToolNodeError,
    ToolRateLimitError,
    ToolServerError,
    ToolTimeoutError,
)


def adapt_mcp_error(exc: Exception) -> ToolNodeError:
    """Convert FastMCP exceptions to ToolNodeError."""
    exc_type = type(exc).__name__
    exc_msg = str(exc)

    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)

    if "McpError" in exc_type or "ToolError" in exc_type:
        return ToolClientError(f"MCP tool error: {exc_msg}", cause=exc)

    if "ConnectionError" in exc_type or "connection" in exc_msg.lower():
        return ToolConnectionError(f"MCP connection failed: {exc_msg}", cause=exc)

    if status_code:
        return _from_status(status_code, exc_msg, exc)

    return ToolNodeError(f"MCP error: {exc_msg}", cause=exc)


def adapt_utcp_error(exc: Exception) -> ToolNodeError:
    """Convert UTCP exceptions to ToolNodeError."""
    exc_type = type(exc).__name__
    exc_msg = str(exc)

    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)

    if status_code is None:
        match = re.search(r"(?:HTTP|status)[:\s]*(\d{3})", exc_msg, re.IGNORECASE)
        if match:
            status_code = int(match.group(1))

    if "UtcpHttpError" in exc_type or "HttpError" in exc_type:
        return _from_status(status_code, exc_msg, exc)

    if "UtcpConnectionError" in exc_type or "connection" in exc_msg.lower():
        return ToolConnectionError(f"UTCP connection failed: {exc_msg}", cause=exc)

    if "UtcpTimeoutError" in exc_type or "timeout" in exc_msg.lower():
        return ToolTimeoutError(f"UTCP timeout: {exc_msg}", cause=exc)

    if status_code:
        return _from_status(status_code, exc_msg, exc)

    return ToolNodeError(f"UTCP error: {exc_msg}", cause=exc)


def adapt_exception(exc: Exception, transport: str) -> ToolNodeError:
    """Route to appropriate adapter based on transport."""
    if isinstance(exc, asyncio.CancelledError):
        raise exc

    if isinstance(exc, asyncio.TimeoutError):
        return ToolTimeoutError(f"Operation timed out: {exc}", cause=exc)

    if isinstance(exc, (ConnectionError, OSError)):
        return ToolConnectionError(f"Connection failed: {exc}", cause=exc)

    if isinstance(exc, ToolNodeError):
        return exc

    if transport == "mcp":
        return adapt_mcp_error(exc)
    return adapt_utcp_error(exc)


def _from_status(status_code: int | None, exc_msg: str, exc: Exception) -> ToolNodeError:
    """Map HTTP-like status codes into concrete ToolNodeError subclasses."""
    if status_code is None:
        return ToolNodeError(exc_msg, cause=exc)
    if status_code == 429:
        return ToolRateLimitError(exc_msg, status_code=status_code, cause=exc)
    if 500 <= status_code <= 504:
        return ToolServerError(exc_msg, status_code=status_code, cause=exc)
    if status_code in (401, 403):
        return ToolAuthError(exc_msg, status_code=status_code, cause=exc)
    if 400 <= status_code < 500:
        return ToolClientError(exc_msg, status_code=status_code, cause=exc)
    return ToolNodeError(exc_msg, status_code=status_code, cause=exc)


__all__ = ["adapt_exception", "adapt_mcp_error", "adapt_utcp_error"]
