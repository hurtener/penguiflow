"""Tests for the LLM errors module."""

from __future__ import annotations

from penguiflow.llm.errors import (
    LLMAuthError,
    LLMCancelledError,
    LLMContextLengthError,
    LLMError,
    LLMInvalidRequestError,
    LLMParseError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMValidationError,
    extract_clean_error_message,
    is_context_length_error,
    is_retryable,
    map_status_to_error,
)


class TestLLMError:
    def test_basic_error(self) -> None:
        error = LLMError(message="Test error")
        assert "Test error" in str(error)
        assert error.message == "Test error"
        assert error.status_code is None
        assert error.provider is None

    def test_with_all_fields(self) -> None:
        error = LLMError(
            message="Full error",
            status_code=500,
            provider="openai",
            raw={"error": "original"},
        )
        assert error.status_code == 500
        assert error.provider == "openai"
        assert error.raw == {"error": "original"}


class TestSpecificErrors:
    def test_timeout_error(self) -> None:
        error = LLMTimeoutError(message="Request timed out", provider="anthropic")
        assert isinstance(error, LLMError)
        assert "timed out" in error.message
        assert error.provider == "anthropic"

    def test_rate_limit_error(self) -> None:
        error = LLMRateLimitError(message="Rate limited", retry_after=30.0)
        assert error.retry_after == 30.0

    def test_rate_limit_without_retry(self) -> None:
        error = LLMRateLimitError(message="Rate limited")
        assert error.retry_after is None

    def test_server_error(self) -> None:
        error = LLMServerError(message="Server error", status_code=500)
        assert error.status_code == 500

    def test_invalid_request_error(self) -> None:
        error = LLMInvalidRequestError(message="Bad request", provider="bedrock")
        assert isinstance(error, LLMError)

    def test_auth_error(self) -> None:
        error = LLMAuthError(message="Invalid API key")
        assert isinstance(error, LLMError)

    def test_cancelled_error(self) -> None:
        error = LLMCancelledError(message="Operation cancelled")
        assert isinstance(error, LLMError)

    def test_context_length_error(self) -> None:
        error = LLMContextLengthError(
            message="Context too long",
            max_tokens=8192,
            current_tokens=10000,
        )
        assert error.max_tokens == 8192
        assert error.current_tokens == 10000

    def test_validation_error(self) -> None:
        error = LLMValidationError(
            message="Validation failed",
            validation_errors=[{"loc": ["field"], "msg": "invalid"}],
        )
        assert len(error.validation_errors) == 1

    def test_parse_error(self) -> None:
        error = LLMParseError(
            message="Parse failed",
            raw_content="invalid json",
        )
        assert error.raw_content == "invalid json"


class TestIsRetryable:
    def test_rate_limit_is_retryable(self) -> None:
        error = LLMRateLimitError(message="Rate limited")
        assert is_retryable(error) is True

    def test_server_error_is_retryable(self) -> None:
        error = LLMServerError(message="Server error", status_code=503)
        assert is_retryable(error) is True

    def test_timeout_is_retryable(self) -> None:
        error = LLMTimeoutError(message="Timed out")
        assert is_retryable(error) is True

    def test_cancelled_is_retryable_by_default(self) -> None:
        error = LLMCancelledError(message="Request cancelled")
        assert is_retryable(error) is True

    def test_cancelled_can_be_non_retryable(self) -> None:
        error = LLMCancelledError(message="Request cancelled", retryable=False)
        assert is_retryable(error) is False

    def test_auth_not_retryable(self) -> None:
        error = LLMAuthError(message="Invalid key")
        assert is_retryable(error) is False

    def test_invalid_request_not_retryable(self) -> None:
        error = LLMInvalidRequestError(message="Bad request")
        assert is_retryable(error) is False

    def test_context_length_not_retryable(self) -> None:
        error = LLMContextLengthError(message="Too long")
        assert is_retryable(error) is False


class TestMapStatusToError:
    def test_map_401(self) -> None:
        error = map_status_to_error(401, "Unauthorized")
        assert isinstance(error, LLMAuthError)

    def test_map_429(self) -> None:
        error = map_status_to_error(429, "Rate limited")
        assert isinstance(error, LLMRateLimitError)

    def test_map_500(self) -> None:
        error = map_status_to_error(500, "Internal error")
        assert isinstance(error, LLMServerError)
        assert error.status_code == 500

    def test_map_503(self) -> None:
        error = map_status_to_error(503, "Service unavailable")
        assert isinstance(error, LLMServerError)

    def test_map_400(self) -> None:
        error = map_status_to_error(400, "Bad request")
        assert isinstance(error, LLMInvalidRequestError)

    def test_map_unknown(self) -> None:
        error = map_status_to_error(418, "I'm a teapot")
        assert isinstance(error, LLMError)

    def test_map_success_status_returns_generic_llm_error(self) -> None:
        error = map_status_to_error(200, "OK")
        assert isinstance(error, LLMError)
        assert not isinstance(error, (LLMAuthError, LLMRateLimitError, LLMServerError, LLMInvalidRequestError))


class TestIsContextLengthError:
    def test_context_length_error_type(self) -> None:
        error = LLMContextLengthError(message="Too long")
        assert is_context_length_error(error) is True

    def test_message_pattern_input_too_long(self) -> None:
        error = LLMError(message="input is too long for this model")
        assert is_context_length_error(error) is True

    def test_message_pattern_context_length(self) -> None:
        error = LLMError(message="maximum context length exceeded")
        assert is_context_length_error(error) is True

    def test_message_pattern_token_limit(self) -> None:
        error = LLMError(message="exceeds token limit")
        assert is_context_length_error(error) is True

    def test_unrelated_error(self) -> None:
        error = LLMError(message="Something else went wrong")
        assert is_context_length_error(error) is False


class TestIsRetryableNonLLMError:
    """Test is_retryable with non-LLMError exceptions."""

    def test_non_llm_error_not_retryable(self) -> None:
        """Test that non-LLMError exceptions return False."""
        error = ValueError("Some error")
        assert is_retryable(error) is False

    def test_generic_exception_not_retryable(self) -> None:
        """Test that generic exceptions return False."""
        error = Exception("Generic error")
        assert is_retryable(error) is False


class TestMapStatusToErrorAdditional:
    """Additional tests for map_status_to_error covering more status codes."""

    def test_map_403(self) -> None:
        """Test mapping 403 to auth error."""
        error = map_status_to_error(403, "Forbidden")
        assert isinstance(error, LLMAuthError)
        assert error.status_code == 403

    def test_map_408_timeout(self) -> None:
        """Test mapping 408 to timeout error."""
        error = map_status_to_error(408, "Request Timeout")
        assert isinstance(error, LLMTimeoutError)
        assert error.status_code == 408

    def test_map_504_timeout(self) -> None:
        """Test mapping 504 to timeout error."""
        error = map_status_to_error(504, "Gateway Timeout")
        assert isinstance(error, LLMTimeoutError)
        assert error.status_code == 504

    def test_map_502(self) -> None:
        """Test mapping 502 to server error."""
        error = map_status_to_error(502, "Bad Gateway")
        assert isinstance(error, LLMServerError)
        assert error.status_code == 502


class TestExtractCleanErrorMessage:
    """Test the extract_clean_error_message function."""

    def test_simple_message(self) -> None:
        """Test extracting message from simple exception."""
        error = ValueError("Simple error message")
        result = extract_clean_error_message(error)
        assert result == "Simple error message"

    def test_nested_json_message(self) -> None:
        """Test extracting message from nested JSON error."""
        # Simulate Databricks-style nested JSON error
        error = ValueError('{"message":"outer","details":{"message":"inner error"}}')
        result = extract_clean_error_message(error)
        assert result == "inner error"

    def test_colon_separated_message(self) -> None:
        """Test extracting message after colon."""
        error = Exception("ErrorType: The actual error message")
        result = extract_clean_error_message(error)
        assert "actual error message" in result

    def test_json_message_pattern(self) -> None:
        """Test extracting message from JSON with message key."""
        error = ValueError('{"error_code":"BAD_REQUEST","message":"Invalid input"}')
        result = extract_clean_error_message(error)
        assert result == "Invalid input"

    def test_empty_message(self) -> None:
        """Test with empty exception message."""
        error = ValueError("")
        result = extract_clean_error_message(error)
        assert result == ""

    def test_no_patterns_match(self) -> None:
        """Test when no extraction patterns match."""
        error = ValueError("plain error")
        result = extract_clean_error_message(error)
        assert result == "plain error"

    def test_nested_extraction_handles_regex_failure(self, monkeypatch) -> None:
        import re

        def _boom(*_args, **_kwargs):
            raise RuntimeError("regex broken")

        monkeypatch.setattr(re, "findall", _boom)
        error = ValueError('{"message":"outer","details":{"message":"inner"}}')
        assert extract_clean_error_message(error) == error.__str__()
