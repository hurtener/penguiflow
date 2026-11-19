"""Retry mechanism for the LLM layer.

Provides exception-based retry with LLM feedback, adapted from PydanticAI
patterns.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import (
    LLMError,
    LLMRateLimitError,
    is_retryable,
    is_temperature_error,
)
from .types import LLMMessage, LLMRequest, TextPart

if TYPE_CHECKING:
    from collections.abc import Callable

    from .output.base import OutputStrategy
    from .profiles import ModelProfile
    from .providers.base import Provider
    from .schema.plan import SchemaPlan
    from .types import CancelToken, CompletionResponse, StreamCallback

logger = logging.getLogger("penguiflow.llm.retry")

T = TypeVar("T", bound=BaseModel)


class ModelRetry(Exception):
    """Raise to retry the LLM call with feedback message.

    Use this when you want to explicitly request a retry with a custom message
    to the LLM.
    """

    def __init__(
        self,
        message: str,
        validation_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        """Store the feedback message and optional validation errors."""
        self.message = message
        self.validation_errors = validation_errors
        super().__init__(message)


class ValidationRetry(Exception):
    """Raise when Pydantic validation fails and retry is desired."""

    def __init__(self, errors: list[dict[str, Any]], raw_content: str) -> None:
        """Store the validation errors and the raw model output."""
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


def format_validation_retry_message(
    error: ValidationError | ValidationRetry,
) -> LLMMessage:
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


def _queue_feedback_retry(
    error: ValidationError | json.JSONDecodeError | ModelRetry,
    *,
    attempt: int,
    config: RetryConfig,
    on_retry: Callable[[int, Exception], None] | None,
    response: CompletionResponse | None,
    working_messages: list[LLMMessage],
) -> bool:
    """Append retry feedback for a parse or validation failure.

    Returns:
        ``True`` when the call should be retried, ``False`` to re-raise.
    """
    if isinstance(error, ValidationError):
        logger.warning(f"Validation error on attempt {attempt + 1}: {error}")
        allowed = config.retry_on_validation
    elif isinstance(error, json.JSONDecodeError):
        logger.warning(f"JSON parse error on attempt {attempt + 1}: {error}")
        allowed = config.retry_on_parse
    else:
        logger.debug(
            f"Model retry requested on attempt {attempt + 1}: {error.message}"
        )
        allowed = True
    if not allowed or attempt >= config.max_retries:
        return False
    if on_retry:
        on_retry(attempt + 1, error)
    # Add the assistant's failed response to context before the feedback.
    if response is not None:
        working_messages.append(response.message)
    if isinstance(error, ValidationError):
        working_messages.append(format_validation_retry_message(error))
    elif isinstance(error, json.JSONDecodeError):
        working_messages.append(format_parse_retry_message(error))
    else:
        working_messages.append(
            LLMMessage(role="user", parts=[TextPart(text=error.message)])
        )
    return True


def _provider_error_backoff(
    error: LLMError,
    *,
    attempt: int,
    config: RetryConfig,
    provider: Provider,
    on_retry: Callable[[int, Exception], None] | None,
) -> float | None:
    """Decide how to retry after a provider error.

    Returns:
        Seconds to wait before retrying (``0`` to retry at once), or ``None``
        to re-raise the error.
    """
    # Temperature compatibility recovery: a model that rejects the
    # temperature parameter returns a non-retryable 400. Drop temperature
    # for this model and retry; the provider's next request omits it.
    if (
        is_temperature_error(error)
        and not provider.temperature_unsupported
        and attempt < config.max_retries
    ):
        provider.mark_temperature_unsupported()
        logger.warning(
            "llm_temperature_unsupported_recovered | provider=%s model=%s",
            provider.provider_name,
            provider.model,
        )
        if on_retry:
            on_retry(attempt + 1, error)
        return 0.0

    logger.warning(f"LLM error on attempt {attempt + 1}: {error}")
    if (
        not config.retry_on_provider_errors
        or not is_retryable(error)
        or attempt >= config.max_retries
    ):
        return None
    if on_retry:
        on_retry(attempt + 1, error)
    # Use retry_after from rate limit errors if available, otherwise apply
    # exponential backoff.
    if isinstance(error, LLMRateLimitError) and error.retry_after is not None:
        return error.retry_after
    return min(
        config.initial_backoff_s * (config.backoff_multiplier**attempt),
        config.max_backoff_s,
    )


def _attempt_cost(
    pricing_fn: Callable[[str, int, int], float] | None,
    provider: Provider,
    response: CompletionResponse,
) -> float:
    """Return the cost of one attempt, or ``0`` without pricing or usage."""
    if pricing_fn is None or response.usage is None:
        return 0.0
    return pricing_fn(
        provider.model,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )


def _strategy_request_builder(
    output_strategy: OutputStrategy,
    provider: Provider,
    response_model: type[BaseModel],
    *,
    profile: ModelProfile | None,
    plan: SchemaPlan | None,
    temperature: float | None,
) -> Callable[[list[LLMMessage]], LLMRequest]:
    """Return a request builder backed by ``output_strategy``.

    The provider's model and profile are read on every call, so a fallback
    provider that switched models builds requests for the active model.
    """

    def build(messages: list[LLMMessage]) -> LLMRequest:
        return output_strategy.build_request(
            model=provider.model,
            messages=messages,
            response_model=response_model,
            profile=profile or provider.profile,
            plan=plan,
            temperature=temperature,
        )

    return build


async def call_with_retry(
    provider: Provider,
    base_messages: list[LLMMessage],
    response_model: type[T],
    output_strategy: OutputStrategy,
    *,
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    timeout_s: float | None = None,
    cancel: CancelToken | None = None,
    stream: bool = False,
    on_stream_event: StreamCallback | None = None,
    pricing_fn: Callable[[str, int, int], float] | None = None,
    build_request: Callable[[list[LLMMessage]], LLMRequest] | None = None,
    profile: ModelProfile | None = None,
    plan: SchemaPlan | None = None,
    temperature: float | None = None,
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
        pricing_fn: Function to calculate cost (model, input_tokens,
            output_tokens).
        build_request: Custom request builder function.
        profile: Model profile for building requests.
        plan: Schema plan for building requests.
        temperature: Optional sampling temperature (``None`` omits it).

    Returns:
        Tuple of (parsed_response, total_cost).

    Raises:
        LLMError: If all retries fail.
        ValidationError: If final retry fails validation.
    """
    if config is None:
        config = RetryConfig()
    if build_request is None:
        build_request = _strategy_request_builder(
            output_strategy,
            provider,
            response_model,
            profile=profile,
            plan=plan,
            temperature=temperature,
        )

    state = RetryState()
    working_messages = list(base_messages)

    for attempt in range(config.max_retries + 1):
        state.attempt = attempt
        response: CompletionResponse | None = None

        try:
            response = await provider.complete(
                build_request(working_messages),
                timeout_s=timeout_s,
                cancel=cancel,
                stream=stream,
                on_stream_event=on_stream_event,
            )
            state.total_cost += _attempt_cost(pricing_fn, provider, response)
            parsed = output_strategy.parse_response(response, response_model)
        except (ValidationError, json.JSONDecodeError, ModelRetry) as e:
            state.errors.append(e)
            if not _queue_feedback_retry(
                e,
                attempt=attempt,
                config=config,
                on_retry=on_retry,
                response=response,
                working_messages=working_messages,
            ):
                raise
        except LLMError as e:
            state.errors.append(e)
            backoff = _provider_error_backoff(
                e,
                attempt=attempt,
                config=config,
                provider=provider,
                on_retry=on_retry,
            )
            if backoff is None:
                raise
            if backoff > 0:
                await asyncio.sleep(backoff)
        else:
            return parsed, state.total_cost

    # Should not reach here
    msg = "Retry loop exited unexpectedly"
    raise RuntimeError(msg)
