"""Tests for penguiflow/tools/adapters.py."""

from __future__ import annotations

import asyncio

import pytest

from penguiflow.tools.adapters import (
    _from_status,
    adapt_exception,
    adapt_mcp_error,
    adapt_utcp_error,
)
from penguiflow.tools.errors import (
    ToolAuthError,
    ToolClientError,
    ToolConnectionError,
    ToolNodeError,
    ToolRateLimitError,
    ToolServerError,
    ToolTimeoutError,
)


class _StatusCodeError(Exception):
    """Error carrying an HTTP ``status_code``."""

    def __init__(self, message: str, *, status_code: int) -> None:
        """Keep ``status_code`` next to the message."""
        super().__init__(message)
        self.status_code = status_code


class _CodeError(Exception):
    """Error carrying a status in a ``code`` attribute."""

    def __init__(self, message: str, *, code: int) -> None:
        """Keep ``code`` next to the message."""
        super().__init__(message)
        self.code = code


class _StatusError(Exception):
    """Error carrying a status in a ``status`` attribute."""

    def __init__(self, message: str, *, status: int) -> None:
        """Keep ``status`` next to the message."""
        super().__init__(message)
        self.status = status


# ─── adapt_mcp_error tests ───────────────────────────────────────────────────


def test_adapt_mcp_error_mcp_error_type() -> None:
    """McpError type should return ToolClientError."""

    class FakeMcpError(Exception):
        pass

    FakeMcpError.__name__ = "McpError"
    exc = FakeMcpError("tool call failed")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolClientError)
    assert "MCP tool error" in str(result)


def test_adapt_mcp_error_tool_error_type() -> None:
    """ToolError type should return ToolClientError."""

    class FakeToolError(Exception):
        pass

    FakeToolError.__name__ = "ToolError"
    exc = FakeToolError("tool error")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolClientError)
    assert "MCP tool error" in str(result)


def test_adapt_mcp_error_connection_error_type() -> None:
    """ConnectionError in type name should return ToolConnectionError."""

    class FakeConnectionError(Exception):
        pass

    FakeConnectionError.__name__ = "SomeConnectionError"
    exc = FakeConnectionError("failed to connect")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolConnectionError)
    assert "MCP connection failed" in str(result)


def test_adapt_mcp_error_connection_in_message() -> None:
    """'connection' in message should return ToolConnectionError."""
    exc = Exception("Connection refused")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolConnectionError)


def test_adapt_mcp_error_with_status_code() -> None:
    """Exception with status_code should map via _from_status."""
    exc = _StatusCodeError("server error", status_code=500)
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolServerError)


def test_adapt_mcp_error_with_code_attr() -> None:
    """Exception with code attribute should map via _from_status."""
    exc = _CodeError("rate limited", code=429)
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolRateLimitError)


def test_adapt_mcp_error_generic() -> None:
    """Generic exception should return ToolNodeError."""
    exc = Exception("something went wrong")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolNodeError)
    assert "MCP error" in str(result)


# ─── adapt_utcp_error tests ──────────────────────────────────────────────────


def test_adapt_utcp_error_http_error_type() -> None:
    """UtcpHttpError type should use status mapping."""

    class FakeUtcpHttpError(_StatusCodeError):
        pass

    FakeUtcpHttpError.__name__ = "UtcpHttpError"
    exc = FakeUtcpHttpError("HTTP 401", status_code=401)
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolAuthError)


def test_adapt_utcp_error_connection_error_type() -> None:
    """UtcpConnectionError type should return ToolConnectionError."""

    class FakeUtcpConnectionError(Exception):
        pass

    FakeUtcpConnectionError.__name__ = "UtcpConnectionError"
    exc = FakeUtcpConnectionError("failed")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolConnectionError)
    assert "UTCP connection failed" in str(result)


def test_adapt_utcp_error_timeout_error_type() -> None:
    """UtcpTimeoutError type should return ToolTimeoutError."""

    class FakeUtcpTimeoutError(Exception):
        pass

    FakeUtcpTimeoutError.__name__ = "UtcpTimeoutError"
    exc = FakeUtcpTimeoutError("timed out")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolTimeoutError)
    assert "UTCP timeout" in str(result)


def test_adapt_utcp_error_timeout_in_message() -> None:
    """'timeout' in message should return ToolTimeoutError."""
    exc = Exception("request timeout reached")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolTimeoutError)


def test_adapt_utcp_error_connection_in_message() -> None:
    """'connection' in message should return ToolConnectionError."""
    exc = Exception("Connection closed by peer")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolConnectionError)


def test_adapt_utcp_error_with_status_attr() -> None:
    """Exception with status attribute should map via _from_status."""
    exc = _StatusError("error", status=503)
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolServerError)


def test_adapt_utcp_error_status_in_message() -> None:
    """Status code in message should be extracted via regex."""
    exc = Exception("HTTP: 403 Forbidden")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolAuthError)


def test_adapt_utcp_error_status_lowercase_in_message() -> None:
    """Status in message with different case should work."""
    exc = Exception("status: 429 Too Many Requests")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolRateLimitError)


def test_adapt_utcp_error_generic() -> None:
    """Generic exception should return ToolNodeError."""
    exc = Exception("unknown error")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolNodeError)
    assert "UTCP error" in str(result)


# ─── adapt_exception tests ───────────────────────────────────────────────────


def test_adapt_exception_cancelled_error() -> None:
    """CancelledError should be re-raised, not wrapped."""
    exc = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        adapt_exception(exc, "mcp")


def test_adapt_exception_timeout_error() -> None:
    """TimeoutError should return ToolTimeoutError."""
    exc = TimeoutError()
    result = adapt_exception(exc, "utcp")
    assert isinstance(result, ToolTimeoutError)
    assert "timed out" in str(result)


def test_adapt_exception_connection_error() -> None:
    """ConnectionError should return ToolConnectionError."""
    exc = ConnectionError("refused")
    result = adapt_exception(exc, "mcp")
    assert isinstance(result, ToolConnectionError)


def test_adapt_exception_os_error() -> None:
    """OSError should return ToolConnectionError."""
    exc = OSError("network unreachable")
    result = adapt_exception(exc, "utcp")
    assert isinstance(result, ToolConnectionError)


def test_adapt_exception_tool_node_error_passthrough() -> None:
    """ToolNodeError should be passed through unchanged."""
    exc = ToolAuthError("already adapted")
    result = adapt_exception(exc, "mcp")
    assert result is exc


def test_adapt_exception_routes_to_mcp() -> None:
    """transport='mcp' should route to adapt_mcp_error."""
    exc = Exception("some error")
    result = adapt_exception(exc, "mcp")
    assert isinstance(result, ToolNodeError)
    assert "MCP error" in str(result)


def test_adapt_exception_routes_to_utcp() -> None:
    """transport='utcp' should route to adapt_utcp_error."""
    exc = Exception("some error")
    result = adapt_exception(exc, "utcp")
    assert isinstance(result, ToolNodeError)
    assert "UTCP error" in str(result)


# ─── _from_status tests ──────────────────────────────────────────────────────


def test_from_status_none() -> None:
    """None status_code should return base ToolNodeError."""
    result = _from_status(None, "error message", Exception("test"))
    assert type(result) is ToolNodeError


def test_from_status_429() -> None:
    """Status 429 should return ToolRateLimitError."""
    result = _from_status(429, "rate limited", Exception("test"))
    assert isinstance(result, ToolRateLimitError)
    assert result.status_code == 429


def test_from_status_500() -> None:
    """Status 500 should return ToolServerError."""
    result = _from_status(500, "internal error", Exception("test"))
    assert isinstance(result, ToolServerError)


def test_from_status_502() -> None:
    """Status 502 should return ToolServerError."""
    result = _from_status(502, "bad gateway", Exception("test"))
    assert isinstance(result, ToolServerError)


def test_from_status_504() -> None:
    """Status 504 should return ToolServerError."""
    result = _from_status(504, "gateway timeout", Exception("test"))
    assert isinstance(result, ToolServerError)


def test_from_status_401() -> None:
    """Status 401 should return ToolAuthError."""
    result = _from_status(401, "unauthorized", Exception("test"))
    assert isinstance(result, ToolAuthError)
    assert result.status_code == 401


def test_from_status_403() -> None:
    """Status 403 should return ToolAuthError."""
    result = _from_status(403, "forbidden", Exception("test"))
    assert isinstance(result, ToolAuthError)


def test_from_status_400() -> None:
    """Status 400 should return ToolClientError."""
    result = _from_status(400, "bad request", Exception("test"))
    assert isinstance(result, ToolClientError)


def test_from_status_404() -> None:
    """Status 404 should return ToolClientError."""
    result = _from_status(404, "not found", Exception("test"))
    assert isinstance(result, ToolClientError)


def test_from_status_422() -> None:
    """Status 422 should return ToolClientError."""
    result = _from_status(422, "unprocessable", Exception("test"))
    assert isinstance(result, ToolClientError)


def test_from_status_other() -> None:
    """Other status codes should return base ToolNodeError."""
    result = _from_status(600, "unknown", Exception("test"))
    assert type(result) is ToolNodeError
    assert result.status_code == 600
