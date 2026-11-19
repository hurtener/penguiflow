"""Native LLM layer for penguiflow.

This module provides a native, type-safe LLM abstraction layer with:
- Typed request/response models
- Provider-specific adapters (OpenAI, Anthropic, Google, Bedrock, Databricks,
  OpenRouter)
- Automatic output mode selection (native, tools, prompted)
- Schema transformation for provider compatibility
- Retry with LLM feedback
- Cost tracking and telemetry
- Streaming support

Quick Start:
    from penguiflow.llm import LLMClient, LLMMessage, TextPart
    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str
        confidence: float

    client = LLMClient("gpt-4o")
    result = await client.generate(
        messages=[LLMMessage(role="user", parts=[TextPart(text="What is
        2+2?")])],
        response_model=Answer,
    )
    print(result.data.text)

For backward compatibility with existing planner code:
    from penguiflow.llm.protocol import NativeLLMAdapter

    # Drop-in replacement for _LiteLLMJSONClient
    client = NativeLLMAdapter("gpt-4o")
    content, cost = await client.complete(
        messages=[{"role": "user", "content": "Hello"}],
        response_format={"type": "json_object"},
    )
"""

from __future__ import annotations

# Client
from .client import (
    LLMClient,
    LLMClientConfig,
    LLMResult,
    generate_structured,
)

# Errors
from .errors import (
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
    is_context_length_error,
    is_retryable,
    map_status_to_error,
)

# Protocol adapter
from .fallback import (
    CooldownStore,
    FallbackLLMClient,
    ModelFallbackConfig,
)

# Pricing
from .pricing import (
    calculate_cost,
    calculate_cost_from_usage,
    get_pricing,
    register_pricing,
)

# Profiles
from .profiles import (
    ModelProfile,
    get_profile,
    register_profile,
)
from .protocol import (
    NativeLLMAdapter,
    create_native_adapter,
)

# Providers
from .providers import (
    AnthropicProvider,
    BedrockProvider,
    DatabricksProvider,
    GoogleProvider,
    NIMProvider,
    OpenAIProvider,
    OpenRouterProvider,
    Provider,
    create_provider,
)

# Retry
from .retry import (
    ModelRetry,
    RetryConfig,
    RetryState,
    ValidationRetry,
    call_with_retry,
)

# Routing
from .routing import (
    ParsedModel,
    ProviderType,
    build_model_string,
    estimate_context_window,
    get_provider_for_model,
    is_reasoning_model,
    is_vision_model,
    normalize_model_id,
    parse_model_string,
)

# Schema
from .schema.plan import (
    OutputMode,
    SchemaPlan,
    choose_output_mode,
    plan_schema,
)

# Telemetry
from .telemetry import (
    LLMEvent,
    TelemetryCallback,
    TelemetryHooks,
    TimingContext,
    create_mlflow_callback,
    create_prometheus_callback,
    get_telemetry_hooks,
    set_telemetry_hooks,
)

# Tracing
from .tracing import (
    TRACING_ENV_VAR,
    LLMCallSpan,
    LLMTraceSink,
    LoggingLLMTraceSink,
    MlflowLLMTraceSink,
    resolve_trace_sink_from_env,
)

# Core types
from .types import (
    AudioPart,
    CancelToken,
    CompletionResponse,
    ContentPart,
    Cost,
    ImagePart,
    LLMMessage,
    LLMRequest,
    Role,
    StreamCallback,
    StreamEvent,
    StructuredOutputSpec,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    extract_single_tool_call,
    extract_text,
    strip_markdown_fences,
)

__all__ = [
    # Tracing
    "TRACING_ENV_VAR",
    # Providers
    "AnthropicProvider",
    # Types
    "AudioPart",
    "BedrockProvider",
    "CancelToken",
    "CompletionResponse",
    "ContentPart",
    "CooldownStore",
    "Cost",
    "DatabricksProvider",
    "FallbackLLMClient",
    "GoogleProvider",
    "ImagePart",
    # Errors
    "LLMAuthError",
    "LLMCallSpan",
    "LLMCancelledError",
    # Client
    "LLMClient",
    "LLMClientConfig",
    "LLMContextLengthError",
    "LLMError",
    # Telemetry
    "LLMEvent",
    "LLMInvalidRequestError",
    "LLMMessage",
    "LLMParseError",
    "LLMRateLimitError",
    "LLMRequest",
    "LLMResult",
    "LLMServerError",
    "LLMTimeoutError",
    "LLMTraceSink",
    "LLMValidationError",
    "LoggingLLMTraceSink",
    "MlflowLLMTraceSink",
    "ModelFallbackConfig",
    # Profiles
    "ModelProfile",
    # Retry
    "ModelRetry",
    "NIMProvider",
    # Protocol
    "NativeLLMAdapter",
    "OpenAIProvider",
    "OpenRouterProvider",
    # Schema
    "OutputMode",
    # Routing
    "ParsedModel",
    "Provider",
    "ProviderType",
    "RetryConfig",
    "RetryState",
    "Role",
    "SchemaPlan",
    "StreamCallback",
    "StreamEvent",
    "StructuredOutputSpec",
    "TelemetryCallback",
    "TelemetryHooks",
    "TextPart",
    "TimingContext",
    "ToolCallPart",
    "ToolResultPart",
    "ToolSpec",
    "Usage",
    "ValidationRetry",
    "build_model_string",
    # Pricing
    "calculate_cost",
    "calculate_cost_from_usage",
    "call_with_retry",
    "choose_output_mode",
    "create_mlflow_callback",
    "create_native_adapter",
    "create_prometheus_callback",
    "create_provider",
    "estimate_context_window",
    "extract_single_tool_call",
    "extract_text",
    "generate_structured",
    "get_pricing",
    "get_profile",
    "get_provider_for_model",
    "get_telemetry_hooks",
    "is_context_length_error",
    "is_reasoning_model",
    "is_retryable",
    "is_vision_model",
    "map_status_to_error",
    "normalize_model_id",
    "parse_model_string",
    "plan_schema",
    "register_pricing",
    "register_profile",
    "resolve_trace_sink_from_env",
    "set_telemetry_hooks",
    "strip_markdown_fences",
]
