"""Retry mechanism for the LLM layer.

Provides exception-based retry with LLM feedback, adapted from PydanticAI patterns.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import LLMError, LLMRateLimitError, is_retryable
from .types import LLMMessage, LLMRequest, TextPart

if TYPE_CHECKING:
    from .providers.base import Provider
    from .types import CancelToken, CompletionResponse, StreamCallback

logger = logging.getLogger("penguiflow.llm.retry")

T = TypeVar("T", bound=BaseModel)


class ModelRetry(Exception):
    """Raise to retry the LLM call with feedback message.

    Use this when you want to explicitly request a retry with
    a custom message to the LLM.
    """

    def __init__(self, message: str, validation_errors: list[dict[str, Any]] | None = None):
        self.message = message
        self.validation_errors = validation_errors
        super().__init__(message)


class ValidationRetry(Exception):
    """Raise when Pydantic validation fails and retry is desired."""

    def __init__(self, errors: list[dict[str, Any]], raw_content: str):
        self.errors = errors
        self.raw_content = raw_content
        super().__init__(f"Validation failed: {errors}")


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    retry_on_validation: bool = True
    retry_on_parse: bool = True
    retry_on_provider_errors: bool = True
    initial_backoff_s: float = 1.0
    max_backoff_s: float = 30.0
    backoff_multiplier: float = 2.0


@dataclass
class RetryState:
    """State tracking for retry loop."""

    attempt: int = 0
    total_cost: float = 0.0
    errors: list[Exception] = field(default_factory=list)


def format_validation_retry_message(error: ValidationError | ValidationRetry) -> LLMMessage:
    """Format validation error as a user message for the LLM.

    Args:
        error: The validation error to format.

    Returns:
        LLMMessage with error details for retry.
    """
    errors: list[Any]
    if isinstance(error, ValidationError):
        errors = error.errors()
    else:
        errors = error.errors

    error_details = []
    for err in errors:
        loc = " -> ".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "Unknown error")
        error_details.append(f"- {loc}: {msg}")

    content = (
        "The previous response failed validation. Please fix these errors:\n"
        + "\n".join(error_details)
        + "\n\nProvide a corrected response."
    )

    return LLMMessage(role="user", parts=[TextPart(text=content)])


def format_parse_retry_message(error: json.JSONDecodeError) -> LLMMessage:
    """Format JSON parse error as a user message for the LLM.

    Args:
        error: The JSON decode error to format.

    Returns:
        LLMMessage with error details for retry.
    """
    content = (
        f"Invalid JSON in your response: {error}\n\n"
        "Please provide a valid JSON response."
    )
    return LLMMessage(role="user", parts=[TextPart(text=content)])


async def call_with_retry(
    provider: Provider,
    base_messages: list[LLMMessage],
    response_model: type[T],
    output_strategy: Any,
    *,
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    timeout_s: float | None = None,
    cancel: CancelToken | None = None,
    stream: bool = False,
    on_stream_event: StreamCallback | None = None,
    pricing_fn: Callable[[str, int, int], float] | None = None,
    build_request: Callable[[list[LLMMessage]], LLMRequest] | None = None,
    profile: Any = None,
    plan: Any = None,
) -> tuple[T, float]:
    """Execute LLM call with automatic retry and cost accounting.

    Args:
        provider: The LLM provider to use.
        base_messages: Initial conversation messages.
        response_model: Pydantic model for structured output.
        output_strategy: Strategy for building requests and parsing responses.
        config: Retry configuration.
        on_retry: Callback called on each retry (attempt_num, error).
        timeout_s: Request timeout in seconds.
        cancel: Cancellation token.
        stream: Whether to enable streaming.
        on_stream_event: Streaming callback.
        pricing_fn: Function to calculate cost (model, input_tokens, output_tokens).
        build_request: Custom request builder function.
        profile: Model profile for building requests.
        plan: Schema plan for building requests.

    Returns:
        Tuple of (parsed_response, total_cost).

    Raises:
        LLMError: If all retries fail.
        ValidationError: If final retry fails validation.
    """
    if config is None:
        config = RetryConfig()

    state = RetryState()
    working_messages = list(base_messages)

    for attempt in range(config.max_retries + 1):
        state.attempt = attempt
        response: CompletionResponse | None = None

        try:
            # Build request
            if build_request:
                request = build_request(working_messages)
            else:
                request = output_strategy.build_request(
                    model=provider.model,
                    messages=working_messages,
                    response_model=response_model,
                    profile=profile or provider.profile,
                    plan=plan,
                )

            # Execute request
            response = await provider.complete(
                request,
                timeout_s=timeout_s,
                cancel=cancel,
                stream=stream,
                on_stream_event=on_stream_event,
            )

            # Calculate cost
            if pricing_fn and response.usage:
                attempt_cost = pricing_fn(
                    provider.model,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                state.total_cost += attempt_cost

            # Parse response
            parsed = output_strategy.parse_response(response, response_model)
            return parsed, state.total_cost

        except ValidationError as e:
            state.errors.append(e)
            logger.warning(f"Validation error on attempt {attempt + 1}: {e}")

            if not config.retry_on_validation or attempt >= config.max_retries:
                raise

            if on_retry:
                on_retry(attempt + 1, e)

            # Add the assistant's failed response to context before error feedback
            if response is not None:
                working_messages.append(response.message)

            working_messages.append(format_validation_retry_message(e))

        except json.JSONDecodeError as e:
            state.errors.append(e)
            logger.warning(f"JSON parse error on attempt {attempt + 1}: {e}")

            if not config.retry_on_parse or attempt >= config.max_retries:
                raise

            if on_retry:
                on_retry(attempt + 1, e)

            # Add the assistant's failed response to context before error feedback
            if response is not None:
                working_messages.append(response.message)

            working_messages.append(format_parse_retry_message(e))

        except ModelRetry as e:
            state.errors.append(e)
            logger.debug(f"Model retry requested on attempt {attempt + 1}: {e.message}")

            if attempt >= config.max_retries:
                raise

            if on_retry:
                on_retry(attempt + 1, e)

            # Add the assistant's failed response to context before error feedback
            if response is not None:
                working_messages.append(response.message)

            working_messages.append(
                LLMMessage(role="user", parts=[TextPart(text=e.message)])
            )

        except LLMError as e:
            state.errors.append(e)
            logger.warning(f"LLM error on attempt {attempt + 1}: {e}")

            if not config.retry_on_provider_errors or not is_retryable(e) or attempt >= config.max_retries:
                raise

            if on_retry:
                on_retry(attempt + 1, e)

            # Use retry_after from rate limit errors if available, otherwise apply backoff
            if isinstance(e, LLMRateLimitError) and e.retry_after is not None:
                backoff = e.retry_after
            else:
                backoff = min(
                    config.initial_backoff_s * (config.backoff_multiplier ** attempt),
                    config.max_backoff_s,
                )
            await asyncio.sleep(backoff)

    # Should not reach here
    raise RuntimeError("Retry loop exited unexpectedly")
