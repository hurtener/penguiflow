"""High-level LLM client for the penguiflow LLM layer.

Provides a unified interface for structured output generation with automatic
mode selection, retry, and cost tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from .output.native import NativeOutputStrategy
from .output.prompted import PromptedOutputStrategy
from .output.tool import ToolsOutputStrategy
from .pricing import calculate_cost
from .profiles import ModelProfile, get_profile
from .providers import Provider, create_provider
from .retry import RetryConfig, call_with_retry
from .schema.plan import OutputMode, choose_output_mode, plan_schema
from .telemetry import LLMEvent, get_telemetry_hooks
from .types import (
    CancelToken,
    CompletionResponse,
    Cost,
    LLMMessage,
    LLMRequest,
    StreamCallback,
    Usage,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("penguiflow.llm.client")

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMClientConfig:
    """Configuration for LLMClient."""

    max_retries: int = 3
    retry_on_validation: bool = True
    retry_on_parse: bool = True
    retry_on_provider_errors: bool = True
    timeout_s: float = 120.0
    temperature: float = 0.0
    force_mode: OutputMode | None = None
    enable_telemetry: bool = True
    enable_cost_tracking: bool = True


@dataclass
class LLMResult:
    """Result from an LLM structured output call."""

    data: BaseModel
    usage: Usage
    cost: Cost
    mode_used: OutputMode
    attempts: int
    raw_response: Any = None


class LLMClient:
    """High-level client for structured LLM interactions.

    Provides automatic mode selection, retry with feedback, and cost tracking.

    Example:
        from penguiflow.llm import LLMClient
        from pydantic import BaseModel

        class Answer(BaseModel):
            text: str
            confidence: float

        client = LLMClient("openai/gpt-4o")
        result = await client.generate(
            messages=[LLMMessage(role="user", parts=[TextPart(text="What is 2+2?")])],
            response_model=Answer,
        )
        print(result.data.text)  # "4"
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        config: LLMClientConfig | None = None,
        provider: Provider | None = None,
        profile: ModelProfile | None = None,
        **provider_kwargs: Any,
    ) -> None:
        """Initialize the LLM client.

        Args:
            model: Model identifier (e.g., "gpt-4o", "claude-3-5-sonnet").
            api_key: API key (uses environment variable if not provided).
            base_url: Base URL override for OpenAI-compatible endpoints.
            config: Client configuration.
            provider: Pre-configured provider instance (overrides model-based creation).
            profile: Pre-configured model profile (overrides automatic lookup).
            **provider_kwargs: Additional provider-specific configuration.
        """
        self.model = model
        self.config = config or LLMClientConfig()

        # Create or use provided provider
        if provider is not None:
            self._provider = provider
        else:
            self._provider = create_provider(
                model,
                api_key=api_key,
                base_url=base_url,
                **provider_kwargs,
            )

        # Get or use provided profile
        if profile is not None:
            self._profile = profile
        else:
            self._profile = get_profile(self._provider.model)

        # Output strategies
        self._native_strategy = NativeOutputStrategy()
        self._tools_strategy = ToolsOutputStrategy()
        self._prompted_strategy = PromptedOutputStrategy()

        # Cost accumulator
        self._total_cost = Cost.zero()

    @property
    def provider(self) -> Provider:
        """Get the underlying provider."""
        return self._provider

    @property
    def profile(self) -> ModelProfile:
        """Get the model profile."""
        return self._profile

    @property
    def total_cost(self) -> Cost:
        """Get the total accumulated cost."""
        return self._total_cost

    def reset_cost(self) -> None:
        """Reset the cost accumulator."""
        self._total_cost = Cost.zero()

    async def generate(
        self,
        messages: list[LLMMessage],
        response_model: type[T],
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
        max_retries: int | None = None,
        temperature: float | None = None,
        force_mode: OutputMode | None = None,
    ) -> LLMResult:
        """Generate structured output from an LLM.

        Args:
            messages: Conversation messages.
            response_model: Pydantic model for structured output.
            timeout_s: Request timeout (uses config default if not specified).
            cancel: Cancellation token.
            stream: Enable streaming.
            on_stream_event: Streaming callback.
            max_retries: Override max retries.
            temperature: Override temperature.
            force_mode: Force specific output mode.

        Returns:
            LLMResult with parsed data, usage, and cost.

        Raises:
            LLMError: If the request fails after all retries.
            ValidationError: If the response cannot be validated.
        """
        # Get JSON schema from Pydantic model
        schema = response_model.model_json_schema()

        # Choose output mode
        mode = force_mode or self.config.force_mode
        if mode is None:
            mode, plan = choose_output_mode(self._profile, schema)
        else:
            plan = plan_schema(self._profile, schema, mode=mode)

        # Get strategy for the chosen mode
        strategy = self._get_strategy(mode)

        # Build retry config
        retry_config = RetryConfig(
            max_retries=max_retries if max_retries is not None else self.config.max_retries,
            retry_on_validation=self.config.retry_on_validation,
            retry_on_parse=self.config.retry_on_parse,
            retry_on_provider_errors=self.config.retry_on_provider_errors,
        )

        # Telemetry
        hooks = get_telemetry_hooks()
        trace_id = None  # Could be passed in for distributed tracing

        if self.config.enable_telemetry:
            hooks.emit(
                LLMEvent.create(
                    "request_start",
                    self._provider.provider_name,
                    self._provider.model,
                    trace_id=trace_id,
                    mode=mode.value,
                    response_model=response_model.__name__,
                )
            )

        attempts = 0
        try:
            # Use call_with_retry for the complete retry loop
            def on_retry(attempt: int, error: Exception) -> None:
                nonlocal attempts
                attempts = attempt
                if self.config.enable_telemetry:
                    hooks.emit(
                        LLMEvent.create(
                            "retry",
                            self._provider.provider_name,
                            self._provider.model,
                            trace_id=trace_id,
                            attempt=attempt,
                            reason=str(error),
                        )
                    )

            parsed, total_cost = await call_with_retry(
                self._provider,
                messages,
                response_model,
                strategy,
                config=retry_config,
                on_retry=on_retry,
                timeout_s=timeout_s or self.config.timeout_s,
                cancel=cancel,
                stream=stream,
                on_stream_event=on_stream_event,
                pricing_fn=calculate_cost if self.config.enable_cost_tracking else None,
                profile=self._profile,
                plan=plan,
            )

            # Build cost object
            cost = Cost(
                input_cost=0.0,  # Detailed breakdown not available from retry loop
                output_cost=0.0,
                total_cost=total_cost,
            )

            # Accumulate cost
            self._total_cost = Cost(
                input_cost=self._total_cost.input_cost + cost.input_cost,
                output_cost=self._total_cost.output_cost + cost.output_cost,
                total_cost=self._total_cost.total_cost + cost.total_cost,
            )

            if self.config.enable_telemetry:
                hooks.emit(
                    LLMEvent.create(
                        "request_end",
                        self._provider.provider_name,
                        self._provider.model,
                        trace_id=trace_id,
                        cost=total_cost,
                        attempts=attempts + 1,
                        mode=mode.value,
                    )
                )

            return LLMResult(
                data=parsed,
                usage=Usage.zero(),  # Aggregated usage not tracked in retry loop
                cost=cost,
                mode_used=mode,
                attempts=attempts + 1,
            )

        except Exception as e:
            if self.config.enable_telemetry:
                hooks.emit(
                    LLMEvent.create(
                        "error",
                        self._provider.provider_name,
                        self._provider.model,
                        trace_id=trace_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                )
            raise

    async def complete_raw(
        self,
        request: LLMRequest,
        *,
        timeout_s: float | None = None,
        cancel: CancelToken | None = None,
        stream: bool = False,
        on_stream_event: StreamCallback | None = None,
    ) -> CompletionResponse:
        """Execute a raw completion request without structured output.

        Args:
            request: The LLM request.
            timeout_s: Request timeout.
            cancel: Cancellation token.
            stream: Enable streaming.
            on_stream_event: Streaming callback.

        Returns:
            Raw completion response.
        """
        return await self._provider.complete(
            request,
            timeout_s=timeout_s or self.config.timeout_s,
            cancel=cancel,
            stream=stream,
            on_stream_event=on_stream_event,
        )

    def _get_strategy(self, mode: OutputMode) -> Any:
        """Get the output strategy for a mode."""
        if mode == OutputMode.NATIVE:
            return self._native_strategy
        elif mode == OutputMode.TOOLS:
            return self._tools_strategy
        else:
            return self._prompted_strategy


async def generate_structured(
    model: str,
    messages: list[LLMMessage],
    response_model: type[T],
    *,
    api_key: str | None = None,
    **kwargs: Any,
) -> T:
    """Convenience function for one-shot structured generation.

    Args:
        model: Model identifier.
        messages: Conversation messages.
        response_model: Pydantic model for structured output.
        api_key: API key.
        **kwargs: Additional arguments for LLMClient.generate().

    Returns:
        Parsed Pydantic model instance.
    """
    client = LLMClient(model, api_key=api_key)
    result = await client.generate(messages, response_model, **kwargs)
    return result.data  # type: ignore


__all__ = [
    "LLMClient",
    "LLMClientConfig",
    "LLMResult",
    "generate_structured",
]
