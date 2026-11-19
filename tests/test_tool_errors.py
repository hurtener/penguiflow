"""Tests for penguiflow/tools/errors.py."""

from __future__ import annotations

from penguiflow.tools.errors import (
    ErrorCategory,
    ToolAuthError,
    ToolClientError,
    ToolConnectionError,
    ToolNodeError,
    ToolRateLimitError,
    ToolServerError,
    ToolTimeoutError,
)

# ─── ErrorCategory tests ─────────────────────────────────────────────────────


def test_error_category_values() -> None:
    """ErrorCategory should have expected values."""
    assert ErrorCategory.RETRYABLE_SERVER.value == "retryable_server"
    assert ErrorCategory.RETRYABLE_RATE_LIMIT.value == "retryable_rate"
    assert ErrorCategory.NON_RETRYABLE_CLIENT.value == "non_retryable"
    assert ErrorCategory.AUTH_REQUIRED.value == "auth_required"
    assert ErrorCategory.NETWORK.value == "network"
    assert ErrorCategory.CANCELLED.value == "cancelled"
    assert ErrorCategory.UNKNOWN.value == "unknown"


# ─── ToolNodeError tests ─────────────────────────────────────────────────────


def test_tool_node_error_basic() -> None:
    """ToolNodeError should store message."""
    exc = ToolNodeError("test error")
    assert str(exc) == "test error"
    assert exc.status_code is None
    assert exc.category == ErrorCategory.UNKNOWN


def test_tool_node_error_with_status_code() -> None:
    """ToolNodeError should infer category from status code."""
    exc = ToolNodeError("server error", status_code=500)
    assert exc.status_code == 500
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_with_explicit_category() -> None:
    """ToolNodeError should accept explicit category."""
    exc = ToolNodeError("custom", category=ErrorCategory.NETWORK)
    assert exc.category == ErrorCategory.NETWORK


def test_tool_node_error_with_cause() -> None:
    """ToolNodeError should store cause."""
    cause = ValueError("original")
    exc = ToolNodeError("wrapped", cause=cause)
    assert exc.__cause__ is cause


def test_tool_node_error_infer_category_429() -> None:
    """Status 429 should infer RETRYABLE_RATE_LIMIT."""
    exc = ToolNodeError("rate limited", status_code=429)
    assert exc.category == ErrorCategory.RETRYABLE_RATE_LIMIT


def test_tool_node_error_infer_category_500() -> None:
    """Status 500 should infer RETRYABLE_SERVER."""
    exc = ToolNodeError("server error", status_code=500)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_infer_category_502() -> None:
    """Status 502 should infer RETRYABLE_SERVER."""
    exc = ToolNodeError("bad gateway", status_code=502)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_infer_category_504() -> None:
    """Status 504 should infer RETRYABLE_SERVER."""
    exc = ToolNodeError("timeout", status_code=504)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_infer_category_401() -> None:
    """Status 401 should infer AUTH_REQUIRED."""
    exc = ToolNodeError("unauthorized", status_code=401)
    assert exc.category == ErrorCategory.AUTH_REQUIRED


def test_tool_node_error_infer_category_403() -> None:
    """Status 403 should infer AUTH_REQUIRED."""
    exc = ToolNodeError("forbidden", status_code=403)
    assert exc.category == ErrorCategory.AUTH_REQUIRED


def test_tool_node_error_infer_category_400() -> None:
    """Status 400 should infer NON_RETRYABLE_CLIENT."""
    exc = ToolNodeError("bad request", status_code=400)
    assert exc.category == ErrorCategory.NON_RETRYABLE_CLIENT


def test_tool_node_error_infer_category_404() -> None:
    """Status 404 should infer NON_RETRYABLE_CLIENT."""
    exc = ToolNodeError("not found", status_code=404)
    assert exc.category == ErrorCategory.NON_RETRYABLE_CLIENT


def test_tool_node_error_infer_category_other() -> None:
    """Other status codes should infer UNKNOWN."""
    exc = ToolNodeError("other", status_code=600)
    assert exc.category == ErrorCategory.UNKNOWN


def test_tool_node_error_is_retryable_server() -> None:
    """RETRYABLE_SERVER should be retryable."""
    exc = ToolNodeError("server", category=ErrorCategory.RETRYABLE_SERVER)
    assert exc.is_retryable is True


def test_tool_node_error_is_retryable_rate_limit() -> None:
    """RETRYABLE_RATE_LIMIT should be retryable."""
    exc = ToolNodeError("rate", category=ErrorCategory.RETRYABLE_RATE_LIMIT)
    assert exc.is_retryable is True


def test_tool_node_error_is_retryable_network() -> None:
    """NETWORK should be retryable."""
    exc = ToolNodeError("network", category=ErrorCategory.NETWORK)
    assert exc.is_retryable is True


def test_tool_node_error_is_not_retryable_client() -> None:
    """NON_RETRYABLE_CLIENT should not be retryable."""
    exc = ToolNodeError("client", category=ErrorCategory.NON_RETRYABLE_CLIENT)
    assert exc.is_retryable is False


def test_tool_node_error_is_not_retryable_auth() -> None:
    """AUTH_REQUIRED should not be retryable."""
    exc = ToolNodeError("auth", category=ErrorCategory.AUTH_REQUIRED)
    assert exc.is_retryable is False


def test_tool_node_error_retry_after_rate_limit() -> None:
    """RETRYABLE_RATE_LIMIT should have retry_after_seconds."""
    exc = ToolNodeError(
        "rate limited", category=ErrorCategory.RETRYABLE_RATE_LIMIT
    )
    assert exc.retry_after_seconds == 1.0


def test_tool_node_error_retry_after_other() -> None:
    """Other categories should return None for retry_after_seconds."""
    exc = ToolNodeError("server", category=ErrorCategory.RETRYABLE_SERVER)
    assert exc.retry_after_seconds is None


def test_tool_node_error_to_dict() -> None:
    """to_dict should return serializable representation."""
    exc = ToolNodeError("test", status_code=500)
    result = exc.to_dict()

    assert result["type"] == "ToolNodeError"
    assert result["message"] == "test"
    assert result["status_code"] == 500
    assert result["category"] == "retryable_server"
    assert result["is_retryable"] is True


def test_tool_node_error_to_dict_no_status() -> None:
    """to_dict should handle None status_code."""
    exc = ToolNodeError("test")
    result = exc.to_dict()

    assert result["status_code"] is None
    assert result["category"] == "unknown"


# ─── Subclass tests ──────────────────────────────────────────────────────────


def test_tool_auth_error_with_status() -> None:
    """ToolAuthError with status 401 should infer AUTH_REQUIRED."""
    exc = ToolAuthError("auth failed", status_code=401)
    assert exc.category == ErrorCategory.AUTH_REQUIRED


def test_tool_auth_error_without_status() -> None:
    """ToolAuthError without status uses inferred category."""
    exc = ToolAuthError("auth failed")
    # Without status_code, _infer_category returns UNKNOWN
    assert exc.category == ErrorCategory.UNKNOWN


def test_tool_timeout_error_is_instance() -> None:
    """ToolTimeoutError should be a ToolNodeError subclass."""
    exc = ToolTimeoutError("timed out")
    assert isinstance(exc, ToolNodeError)
    assert str(exc) == "timed out"


def test_tool_connection_error_is_instance() -> None:
    """ToolConnectionError should be a ToolNodeError subclass."""
    exc = ToolConnectionError("connection failed")
    assert isinstance(exc, ToolNodeError)
    assert str(exc) == "connection failed"


def test_tool_rate_limit_error_with_status() -> None:
    """ToolRateLimitError with 429 should infer RETRYABLE_RATE_LIMIT."""
    exc = ToolRateLimitError("rate limited", status_code=429)
    assert exc.category == ErrorCategory.RETRYABLE_RATE_LIMIT


def test_tool_client_error_with_status() -> None:
    """ToolClientError with 400 should infer NON_RETRYABLE_CLIENT."""
    exc = ToolClientError("client error", status_code=400)
    assert exc.category == ErrorCategory.NON_RETRYABLE_CLIENT


def test_tool_server_error_with_status() -> None:
    """ToolServerError with 500 should infer RETRYABLE_SERVER."""
    exc = ToolServerError("server error", status_code=500)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_subclass_to_dict_type() -> None:
    """Subclass to_dict should return correct type."""
    exc = ToolAuthError("auth", status_code=401)
    result = exc.to_dict()
    assert result["type"] == "ToolAuthError"


def test_rate_limit_error_retry_after() -> None:
    """ToolRateLimitError should have retry_after_seconds."""
    exc = ToolRateLimitError("too many requests", status_code=429)
    assert exc.retry_after_seconds == 1.0
    assert exc.is_retryable is True


def test_connection_error_is_retryable() -> None:
    """ToolConnectionError with NETWORK category is retryable."""
    exc = ToolConnectionError("failed", category=ErrorCategory.NETWORK)
    assert exc.is_retryable is True


def test_server_error_is_retryable() -> None:
    """ToolServerError with server status is retryable."""
    exc = ToolServerError("internal error", status_code=503)
    assert exc.is_retryable is True
