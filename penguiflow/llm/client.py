"""High-level LLM client for the penguiflow LLM layer.

Provides a unified interface for structured output generation with automatic
mode selection, retry, and cost tracking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from .fallback import CooldownStore, FallbackProvider, ModelFallbackConfig
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
    from .output.base import OutputStrategy

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
    # Temperature is opt-in: None means the model uses its provider default.
    temperature: float | None = None
    force_mode: OutputMode | None = None
    enable_telemetry: bool = True
    enable_cost_tracking: bool = True


@dataclass
class LLMResult(Generic[T]):
    """Result from an LLM structured output call."""

    data: T
    usage: Usage
    cost: Cost
    mode_used: OutputMode
    attempts: int
    raw_response: Any = None


def _merge_fallback_profile(
    primary_model: str, fallback: ModelFallbackConfig
) -> ModelProfile:
    """Conservative capability profile across a fallback chain.

    The booleans that drive output-mode selection are AND-ed across every model
    in the chain so the mode chosen for the primary is also honored by any
    model we fail over to. Native schema-guided output additionally requires
    every model to share the same ``native_structured_kind`` (an OpenAI
    response-format request is not interchangeable with Databricks constrained
    decoding).

    Non-capability fields (pricing, token limits, transformers) are inherited
    from the primary; per-attempt cost is still attributed to the model that
    actually answered via ``FallbackProvider.model``.
    """
    chain = [primary_model]
    chain.extend(m for m in fallback.models if m != primary_model)
    profiles = [get_profile(m) for m in chain]

    merged = profiles[0]
    for other in profiles[1:]:
        same_native_kind = (
            merged.native_structured_kind == other.native_structured_kind
        )
        merged = replace(
            merged,
            supports_schema_guided_output=(
                merged.supports_schema_guided_output
                and other.supports_schema_guided_output
                and same_native_kind
            ),
            supports_json_only_output=(
                merged.supports_json_only_output
                and other.supports_json_only_output
            ),
            supports_tools=merged.supports_tools and other.supports_tools,
            supports_native_tool_calls=(
                merged.supports_native_tool_calls
                and other.supports_native_tool_calls
            ),
            supports_streaming=merged.supports_streaming
            and other.supports_streaming,
            supports_image_input=merged.supports_image_input
            and other.supports_image_input,
            supports_audio_input=merged.supports_audio_input
            and other.supports_audio_input,
            supports_reasoning=merged.supports_reasoning
            and other.supports_reasoning,
        )

    # Don't lead with a mode the merged chain can't uniformly satisfy.
    if (
        merged.default_output_mode == "native"
        and not merged.supports_schema_guided_output
    ):
        merged = replace(
            merged,
            default_output_mode="tools"
            if merged.supports_tools
            else "prompted",
        )
    return merged


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
            messages=[
                LLMMessage(role="user", parts=[TextPart(text="What is 2+2?")])
            ],
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
        fallback: ModelFallbackConfig | None = None,
        cooldown_store: CooldownStore | None = None,
        config: LLMClientConfig | None = None,
        provider: Provider | None = None,
        profile: ModelProfile | None = None,
        **provider_kwargs: object,
    ) -> None:
        """Initialize the LLM client.

        Args:
            model: Model identifier (e.g., "gpt-4o", "claude-3-5-sonnet").
            api_key: API key (uses environment variable if not provided).
            base_url: Base URL override for OpenAI-compatible endpoints.
            fallback: Optional model fallback chain applied on 429s.
            cooldown_store: Optional shared cooldown store used with fallback.
            config: Client configuration.
            provider: Pre-configured provider instance (overrides model-based
                creation).
            profile: Pre-configured model profile (overrides automatic lookup).
            **provider_kwargs: Additional provider-specific configuration.
        """
        self.model = model
        self.config = config or LLMClientConfig()

        if provider is not None and fallback is not None:
            msg = "fallback cannot be combined with a pre-configured provider"
            raise ValueError(msg)

        # Create or use provided provider
        if provider is not None:
            self._provider = provider
        elif fallback is not None:
            self._provider = FallbackProvider(
                model,
                fallback,
                provider_factory=create_provider,
                cooldown_store=cooldown_store,
                default_api_key=api_key,
                base_url=base_url,
                **provider_kwargs,
            )
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
        elif fallback is not None:
            # Output mode is chosen ONCE above the FallbackProvider seam and
            # the resulting request (response_format / tools) is reused across
            # every model in the chain. Use a capability profile that is the
            # intersection of all chain models so the chosen mode is honored by
            # whichever model
            # answers (e.g. downgrade native structured output -> prompted when
            # a fallback model lacks it).
            self._profile = _merge_fallback_profile(model, fallback)
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
    ) -> LLMResult[T]:
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
        strategy = self.get_strategy(mode)

        # Build retry config
        retry_config = RetryConfig(
            max_retries=max_retries
            if max_retries is not None
            else self.config.max_retries,
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
                pricing_fn=calculate_cost
                if self.config.enable_cost_tracking
                else None,
                profile=self._profile,
                plan=plan,
                temperature=temperature
                if temperature is not None
                else self.config.temperature,
            )

            # Build cost object
            cost = Cost(
                # Detailed breakdown not available from retry loop
                input_cost=0.0,
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
                # Aggregated usage not tracked in retry loop
                usage=Usage.zero(),
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

    def get_strategy(self, mode: OutputMode) -> OutputStrategy:
        """Get the output strategy for a mode."""
        if mode == OutputMode.NATIVE:
            return self._native_strategy
        if mode == OutputMode.TOOLS:
            return self._tools_strategy
        return self._prompted_strategy


async def generate_structured(
    model: str,
    messages: list[LLMMessage],
    response_model: type[T],
    *,
    api_key: str | None = None,
    fallback: ModelFallbackConfig | None = None,
    cooldown_store: CooldownStore | None = None,
    timeout_s: float | None = None,
    cancel: CancelToken | None = None,
    stream: bool = False,
    on_stream_event: StreamCallback | None = None,
    max_retries: int | None = None,
    temperature: float | None = None,
    force_mode: OutputMode | None = None,
) -> T:
    """Convenience function for one-shot structured generation.

    Args:
        model: Model identifier.
        messages: Conversation messages.
        response_model: Pydantic model for structured output.
        api_key: API key.
        fallback: Optional model fallback chain applied on 429s.
        cooldown_store: Optional shared cooldown store used with fallback.
        timeout_s: Request timeout override, see :meth:`LLMClient.generate`.
        cancel: Optional cancellation token.
        stream: Whether to stream the response.
        on_stream_event: Callback for streaming events.
        max_retries: Retry limit override.
        temperature: Optional sampling temperature.
        force_mode: Force a specific output mode.

    Returns:
        Parsed Pydantic model instance.
    """
    client = LLMClient(
        model,
        api_key=api_key,
        fallback=fallback,
        cooldown_store=cooldown_store,
    )
    result = await client.generate(
        messages,
        response_model,
        timeout_s=timeout_s,
        cancel=cancel,
        stream=stream,
        on_stream_event=on_stream_event,
        max_retries=max_retries,
        temperature=temperature,
        force_mode=force_mode,
    )
    return result.data


__all__ = [
    "LLMClient",
    "LLMClientConfig",
    "LLMResult",
    "generate_structured",
]
