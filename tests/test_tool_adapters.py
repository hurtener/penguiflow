"""Tests for penguiflow/tools/adapters.py."""

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

# ─── adapt_mcp_error tests ───────────────────────────────────────────────────


def test_adapt_mcp_error_mcp_error_type():
    """McpError type should return ToolClientError."""

    class FakeMcpError(Exception):
        pass

    FakeMcpError.__name__ = "McpError"
    exc = FakeMcpError("tool call failed")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolClientError)
    assert "MCP tool error" in str(result)


def test_adapt_mcp_error_tool_error_type():
    """ToolError type should return ToolClientError."""

    class FakeToolError(Exception):
        pass

    FakeToolError.__name__ = "ToolError"
    exc = FakeToolError("tool error")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolClientError)
    assert "MCP tool error" in str(result)


def test_adapt_mcp_error_connection_error_type():
    """ConnectionError in type name should return ToolConnectionError."""

    class FakeConnectionError(Exception):
        pass

    FakeConnectionError.__name__ = "SomeConnectionError"
    exc = FakeConnectionError("failed to connect")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolConnectionError)
    assert "MCP connection failed" in str(result)


def test_adapt_mcp_error_connection_in_message():
    """'connection' in message should return ToolConnectionError."""
    exc = Exception("Connection refused")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolConnectionError)


def test_adapt_mcp_error_with_status_code():
    """Exception with status_code should map via _from_status."""
    exc = Exception("server error")
    exc.status_code = 500  # type: ignore[attr-defined]
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolServerError)


def test_adapt_mcp_error_with_code_attr():
    """Exception with code attribute should map via _from_status."""
    exc = Exception("rate limited")
    exc.code = 429  # type: ignore[attr-defined]
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolRateLimitError)


def test_adapt_mcp_error_generic():
    """Generic exception should return ToolNodeError."""
    exc = Exception("something went wrong")
    result = adapt_mcp_error(exc)
    assert isinstance(result, ToolNodeError)
    assert "MCP error" in str(result)


# ─── adapt_utcp_error tests ──────────────────────────────────────────────────


def test_adapt_utcp_error_http_error_type():
    """UtcpHttpError type should use status mapping."""

    class FakeUtcpHttpError(Exception):
        pass

    FakeUtcpHttpError.__name__ = "UtcpHttpError"
    exc = FakeUtcpHttpError("HTTP 401")
    exc.status_code = 401  # type: ignore[attr-defined]
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolAuthError)


def test_adapt_utcp_error_connection_error_type():
    """UtcpConnectionError type should return ToolConnectionError."""

    class FakeUtcpConnectionError(Exception):
        pass

    FakeUtcpConnectionError.__name__ = "UtcpConnectionError"
    exc = FakeUtcpConnectionError("failed")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolConnectionError)
    assert "UTCP connection failed" in str(result)


def test_adapt_utcp_error_timeout_error_type():
    """UtcpTimeoutError type should return ToolTimeoutError."""

    class FakeUtcpTimeoutError(Exception):
        pass

    FakeUtcpTimeoutError.__name__ = "UtcpTimeoutError"
    exc = FakeUtcpTimeoutError("timed out")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolTimeoutError)
    assert "UTCP timeout" in str(result)


def test_adapt_utcp_error_timeout_in_message():
    """'timeout' in message should return ToolTimeoutError."""
    exc = Exception("request timeout reached")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolTimeoutError)


def test_adapt_utcp_error_connection_in_message():
    """'connection' in message should return ToolConnectionError."""
    exc = Exception("Connection closed by peer")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolConnectionError)


def test_adapt_utcp_error_with_status_attr():
    """Exception with status attribute should map via _from_status."""
    exc = Exception("error")
    exc.status = 503  # type: ignore[attr-defined]
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolServerError)


def test_adapt_utcp_error_status_in_message():
    """Status code in message should be extracted via regex."""
    exc = Exception("HTTP: 403 Forbidden")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolAuthError)


def test_adapt_utcp_error_status_lowercase_in_message():
    """Status in message with different case should work."""
    exc = Exception("status: 429 Too Many Requests")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolRateLimitError)


def test_adapt_utcp_error_generic():
    """Generic exception should return ToolNodeError."""
    exc = Exception("unknown error")
    result = adapt_utcp_error(exc)
    assert isinstance(result, ToolNodeError)
    assert "UTCP error" in str(result)


# ─── adapt_exception tests ───────────────────────────────────────────────────


def test_adapt_exception_cancelled_error():
    """CancelledError should be re-raised, not wrapped."""
    exc = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        adapt_exception(exc, "mcp")


def test_adapt_exception_timeout_error():
    """TimeoutError should return ToolTimeoutError."""
    exc = TimeoutError()
    result = adapt_exception(exc, "utcp")
    assert isinstance(result, ToolTimeoutError)
    assert "timed out" in str(result)


def test_adapt_exception_connection_error():
    """ConnectionError should return ToolConnectionError."""
    exc = ConnectionError("refused")
    result = adapt_exception(exc, "mcp")
    assert isinstance(result, ToolConnectionError)


def test_adapt_exception_os_error():
    """OSError should return ToolConnectionError."""
    exc = OSError("network unreachable")
    result = adapt_exception(exc, "utcp")
    assert isinstance(result, ToolConnectionError)


def test_adapt_exception_tool_node_error_passthrough():
    """ToolNodeError should be passed through unchanged."""
    exc = ToolAuthError("already adapted")
    result = adapt_exception(exc, "mcp")
    assert result is exc


def test_adapt_exception_routes_to_mcp():
    """transport='mcp' should route to adapt_mcp_error."""
    exc = Exception("some error")
    result = adapt_exception(exc, "mcp")
    assert isinstance(result, ToolNodeError)
    assert "MCP error" in str(result)


def test_adapt_exception_routes_to_utcp():
    """transport='utcp' should route to adapt_utcp_error."""
    exc = Exception("some error")
    result = adapt_exception(exc, "utcp")
    assert isinstance(result, ToolNodeError)
    assert "UTCP error" in str(result)


# ─── _from_status tests ──────────────────────────────────────────────────────


def test_from_status_none():
    """None status_code should return base ToolNodeError."""
    result = _from_status(None, "error message", Exception("test"))
    assert type(result) is ToolNodeError


def test_from_status_429():
    """Status 429 should return ToolRateLimitError."""
    result = _from_status(429, "rate limited", Exception("test"))
    assert isinstance(result, ToolRateLimitError)
    assert result.status_code == 429


def test_from_status_500():
    """Status 500 should return ToolServerError."""
    result = _from_status(500, "internal error", Exception("test"))
    assert isinstance(result, ToolServerError)


def test_from_status_502():
    """Status 502 should return ToolServerError."""
    result = _from_status(502, "bad gateway", Exception("test"))
    assert isinstance(result, ToolServerError)


def test_from_status_504():
    """Status 504 should return ToolServerError."""
    result = _from_status(504, "gateway timeout", Exception("test"))
    assert isinstance(result, ToolServerError)


def test_from_status_401():
    """Status 401 should return ToolAuthError."""
    result = _from_status(401, "unauthorized", Exception("test"))
    assert isinstance(result, ToolAuthError)
    assert result.status_code == 401


def test_from_status_403():
    """Status 403 should return ToolAuthError."""
    result = _from_status(403, "forbidden", Exception("test"))
    assert isinstance(result, ToolAuthError)


def test_from_status_400():
    """Status 400 should return ToolClientError."""
    result = _from_status(400, "bad request", Exception("test"))
    assert isinstance(result, ToolClientError)


def test_from_status_404():
    """Status 404 should return ToolClientError."""
    result = _from_status(404, "not found", Exception("test"))
    assert isinstance(result, ToolClientError)


def test_from_status_422():
    """Status 422 should return ToolClientError."""
    result = _from_status(422, "unprocessable", Exception("test"))
    assert isinstance(result, ToolClientError)


def test_from_status_other():
    """Other status codes should return base ToolNodeError."""
    result = _from_status(600, "unknown", Exception("test"))
    assert type(result) is ToolNodeError
    assert result.status_code == 600
