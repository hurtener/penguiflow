"""Tests for penguiflow/tools/errors.py."""


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


def test_error_category_values():
    """ErrorCategory should have expected values."""
    assert ErrorCategory.RETRYABLE_SERVER == "retryable_server"
    assert ErrorCategory.RETRYABLE_RATE_LIMIT == "retryable_rate"
    assert ErrorCategory.NON_RETRYABLE_CLIENT == "non_retryable"
    assert ErrorCategory.AUTH_REQUIRED == "auth_required"
    assert ErrorCategory.NETWORK == "network"
    assert ErrorCategory.CANCELLED == "cancelled"
    assert ErrorCategory.UNKNOWN == "unknown"


# ─── ToolNodeError tests ─────────────────────────────────────────────────────


def test_tool_node_error_basic():
    """ToolNodeError should store message."""
    exc = ToolNodeError("test error")
    assert str(exc) == "test error"
    assert exc.status_code is None
    assert exc.category == ErrorCategory.UNKNOWN


def test_tool_node_error_with_status_code():
    """ToolNodeError should infer category from status code."""
    exc = ToolNodeError("server error", status_code=500)
    assert exc.status_code == 500
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_with_explicit_category():
    """ToolNodeError should accept explicit category."""
    exc = ToolNodeError("custom", category=ErrorCategory.NETWORK)
    assert exc.category == ErrorCategory.NETWORK


def test_tool_node_error_with_cause():
    """ToolNodeError should store cause."""
    cause = ValueError("original")
    exc = ToolNodeError("wrapped", cause=cause)
    assert exc.__cause__ is cause


def test_tool_node_error_infer_category_429():
    """Status 429 should infer RETRYABLE_RATE_LIMIT."""
    exc = ToolNodeError("rate limited", status_code=429)
    assert exc.category == ErrorCategory.RETRYABLE_RATE_LIMIT


def test_tool_node_error_infer_category_500():
    """Status 500 should infer RETRYABLE_SERVER."""
    exc = ToolNodeError("server error", status_code=500)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_infer_category_502():
    """Status 502 should infer RETRYABLE_SERVER."""
    exc = ToolNodeError("bad gateway", status_code=502)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_infer_category_504():
    """Status 504 should infer RETRYABLE_SERVER."""
    exc = ToolNodeError("timeout", status_code=504)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_tool_node_error_infer_category_401():
    """Status 401 should infer AUTH_REQUIRED."""
    exc = ToolNodeError("unauthorized", status_code=401)
    assert exc.category == ErrorCategory.AUTH_REQUIRED


def test_tool_node_error_infer_category_403():
    """Status 403 should infer AUTH_REQUIRED."""
    exc = ToolNodeError("forbidden", status_code=403)
    assert exc.category == ErrorCategory.AUTH_REQUIRED


def test_tool_node_error_infer_category_400():
    """Status 400 should infer NON_RETRYABLE_CLIENT."""
    exc = ToolNodeError("bad request", status_code=400)
    assert exc.category == ErrorCategory.NON_RETRYABLE_CLIENT


def test_tool_node_error_infer_category_404():
    """Status 404 should infer NON_RETRYABLE_CLIENT."""
    exc = ToolNodeError("not found", status_code=404)
    assert exc.category == ErrorCategory.NON_RETRYABLE_CLIENT


def test_tool_node_error_infer_category_other():
    """Other status codes should infer UNKNOWN."""
    exc = ToolNodeError("other", status_code=600)
    assert exc.category == ErrorCategory.UNKNOWN


def test_tool_node_error_is_retryable_server():
    """RETRYABLE_SERVER should be retryable."""
    exc = ToolNodeError("server", category=ErrorCategory.RETRYABLE_SERVER)
    assert exc.is_retryable is True


def test_tool_node_error_is_retryable_rate_limit():
    """RETRYABLE_RATE_LIMIT should be retryable."""
    exc = ToolNodeError("rate", category=ErrorCategory.RETRYABLE_RATE_LIMIT)
    assert exc.is_retryable is True


def test_tool_node_error_is_retryable_network():
    """NETWORK should be retryable."""
    exc = ToolNodeError("network", category=ErrorCategory.NETWORK)
    assert exc.is_retryable is True


def test_tool_node_error_is_not_retryable_client():
    """NON_RETRYABLE_CLIENT should not be retryable."""
    exc = ToolNodeError("client", category=ErrorCategory.NON_RETRYABLE_CLIENT)
    assert exc.is_retryable is False


def test_tool_node_error_is_not_retryable_auth():
    """AUTH_REQUIRED should not be retryable."""
    exc = ToolNodeError("auth", category=ErrorCategory.AUTH_REQUIRED)
    assert exc.is_retryable is False


def test_tool_node_error_retry_after_rate_limit():
    """RETRYABLE_RATE_LIMIT should have retry_after_seconds."""
    exc = ToolNodeError("rate limited", category=ErrorCategory.RETRYABLE_RATE_LIMIT)
    assert exc.retry_after_seconds == 1.0


def test_tool_node_error_retry_after_other():
    """Other categories should return None for retry_after_seconds."""
    exc = ToolNodeError("server", category=ErrorCategory.RETRYABLE_SERVER)
    assert exc.retry_after_seconds is None


def test_tool_node_error_to_dict():
    """to_dict should return serializable representation."""
    exc = ToolNodeError("test", status_code=500)
    result = exc.to_dict()

    assert result["type"] == "ToolNodeError"
    assert result["message"] == "test"
    assert result["status_code"] == 500
    assert result["category"] == "retryable_server"
    assert result["is_retryable"] is True


def test_tool_node_error_to_dict_no_status():
    """to_dict should handle None status_code."""
    exc = ToolNodeError("test")
    result = exc.to_dict()

    assert result["status_code"] is None
    assert result["category"] == "unknown"


# ─── Subclass tests ──────────────────────────────────────────────────────────


def test_tool_auth_error_with_status():
    """ToolAuthError with status 401 should infer AUTH_REQUIRED."""
    exc = ToolAuthError("auth failed", status_code=401)
    assert exc.category == ErrorCategory.AUTH_REQUIRED


def test_tool_auth_error_without_status():
    """ToolAuthError without status uses inferred category."""
    exc = ToolAuthError("auth failed")
    # Without status_code, _infer_category returns UNKNOWN
    assert exc.category == ErrorCategory.UNKNOWN


def test_tool_timeout_error_is_instance():
    """ToolTimeoutError should be a ToolNodeError subclass."""
    exc = ToolTimeoutError("timed out")
    assert isinstance(exc, ToolNodeError)
    assert str(exc) == "timed out"


def test_tool_connection_error_is_instance():
    """ToolConnectionError should be a ToolNodeError subclass."""
    exc = ToolConnectionError("connection failed")
    assert isinstance(exc, ToolNodeError)
    assert str(exc) == "connection failed"


def test_tool_rate_limit_error_with_status():
    """ToolRateLimitError with 429 should infer RETRYABLE_RATE_LIMIT."""
    exc = ToolRateLimitError("rate limited", status_code=429)
    assert exc.category == ErrorCategory.RETRYABLE_RATE_LIMIT


def test_tool_client_error_with_status():
    """ToolClientError with 400 should infer NON_RETRYABLE_CLIENT."""
    exc = ToolClientError("client error", status_code=400)
    assert exc.category == ErrorCategory.NON_RETRYABLE_CLIENT


def test_tool_server_error_with_status():
    """ToolServerError with 500 should infer RETRYABLE_SERVER."""
    exc = ToolServerError("server error", status_code=500)
    assert exc.category == ErrorCategory.RETRYABLE_SERVER


def test_subclass_to_dict_type():
    """Subclass to_dict should return correct type."""
    exc = ToolAuthError("auth", status_code=401)
    result = exc.to_dict()
    assert result["type"] == "ToolAuthError"


def test_rate_limit_error_retry_after():
    """ToolRateLimitError should have retry_after_seconds."""
    exc = ToolRateLimitError("too many requests", status_code=429)
    assert exc.retry_after_seconds == 1.0
    assert exc.is_retryable is True


def test_connection_error_is_retryable():
    """ToolConnectionError with NETWORK category is retryable."""
    exc = ToolConnectionError("failed", category=ErrorCategory.NETWORK)
    assert exc.is_retryable is True


def test_server_error_is_retryable():
    """ToolServerError with server status is retryable."""
    exc = ToolServerError("internal error", status_code=503)
    assert exc.is_retryable is True
